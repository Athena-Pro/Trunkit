"""
tests/test_bundle_verify.py
===========================
Consumer-side proof-bundle verification (calx.bundle).

Covers:
  - load_bundle: shape and version validation
  - offline verdicts: witness presence, probe-without-DB, artifact hashes
  - derivation degradation: missing / refuted / unverified premises
  - probe replay against a live DB, including rollback of probe state
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import subprocess
import sys
import uuid
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from calx.bundle import load_bundle, validate_bundle, verify_bundle
from calx.ledger import CLAIM_DIGEST_KIND, certificate_row_hash, claim_digest, witness_row_hash
from calx.proposition import witness_subject_ref
from tests.dbskip import connect_or_skip


def _entry(
    claim_id=1,
    statement="test claim",
    method="comp_sql",
    probe_sql=None,
    witness=None,
    derivation=None,
    artifact=None,
    cert_status="valid",
):
    return {
        "claim": {
            "id": claim_id,
            "statement": statement,
            "method": method,
            "probe_sql": probe_sql,
            "subject_ref": witness_subject_ref(witness["body"]) if (
                witness and isinstance(witness.get("body"), dict)
            ) else {},
        },
        "certificate": {"seq": 1, "status": cert_status},
        "witness": witness,
        "derivation": derivation,
        "artifact": artifact,
    }


def _proof(*, valid=True):
    return {"kind": "term", "body": {
        "schema": "factorization", "n": 12,
        "factors": [[2, 2 if valid else 1], [3, 1]],
    }}


def _bundle(*entries):
    return {"trunk_bundle_version": 1, "exported_at": "now", "claims": list(entries)}


# ---------------------------------------------------------------------------
# load_bundle
# ---------------------------------------------------------------------------

def test_load_bundle_roundtrip(tmp_path):
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(_bundle(_entry())), encoding="utf-8")
    assert load_bundle(path)["trunk_bundle_version"] == 1


@pytest.mark.parametrize(
    "payload",
    [
        "not json {",
        json.dumps([1, 2]),
        json.dumps({"trunk_bundle_version": 3, "claims": [{"claim": {}}]}),
        json.dumps({"trunk_bundle_version": 1, "claims": []}),
        json.dumps({"trunk_bundle_version": 1, "claims": [{"nope": 1}]}),
    ],
)
def test_load_bundle_rejects_malformed(tmp_path, payload):
    path = tmp_path / "bad.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError):
        load_bundle(path)


# ---------------------------------------------------------------------------
# Offline verdicts
# ---------------------------------------------------------------------------

def test_witness_claim_valid_offline():
    bundle = _bundle(_entry(probe_sql=None, witness=_proof()))
    (result,) = verify_bundle(bundle)
    assert result.ok is True


def test_witnessless_formal_claim_unverified_offline():
    # No probe and no witness means there is nothing to check: UNVERIFIED,
    # never refuted — absence of evidence is not refutation.
    (result,) = verify_bundle(_bundle(_entry(probe_sql=None, witness=None)))
    assert result.ok is None
    assert any("unverified" in n for n in result.notes)


def test_probe_claim_unverified_without_db():
    bundle = _bundle(_entry(probe_sql="SELECT TRUE, '{}'::jsonb"))
    (result,) = verify_bundle(bundle)
    assert result.ok is None
    assert any("requires a database" in n for n in result.notes)


def test_revoked_witness_claim_degrades_to_unverified_offline():
    entry = _entry(witness=_proof())
    entry["revocation"] = {
        "reason": "generator compromised", "revoked_by": "prover", "revoked_at": "2026-01-01"
    }
    (result,) = verify_bundle(_bundle(entry))
    assert result.ok is None  # revocation is loss of trust, not refutation
    assert any("REVOKED" in n for n in result.notes)


def test_revocation_does_not_touch_probe_verdicts_offline():
    entry = _entry(probe_sql="SELECT TRUE, '{}'::jsonb")
    entry["revocation"] = {"reason": "x", "revoked_by": "p", "revoked_at": "2026-01-01"}
    (result,) = verify_bundle(_bundle(entry))  # no DB: probe already None
    assert result.ok is None
    assert any("REVOKED" in n for n in result.notes)


def test_expired_window_degrades_witness_claim_offline():
    entry = _entry(witness=_proof())
    entry["certificate"]["valid_under"] = {"valid_until": "2020-01-01T00:00:00+00:00"}
    (result,) = verify_bundle(_bundle(entry))
    assert result.ok is None
    assert any("expired" in n for n in result.notes)


def test_unexpired_window_leaves_verdict_alone():
    entry = _entry(witness=_proof())
    entry["certificate"]["valid_under"] = {"valid_until": "2099-01-01T00:00:00+00:00"}
    (result,) = verify_bundle(_bundle(entry))
    assert result.ok is True


def test_v2_bundle_version_accepted(tmp_path):
    bundle = _bundle(_entry(witness=_proof()))
    bundle["trunk_bundle_version"] = 2
    path = tmp_path / "v2.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    assert load_bundle(path)["trunk_bundle_version"] == 2


def test_nonvalid_producer_status_is_surfaced():
    bundle = _bundle(
        _entry(witness=_proof(), cert_status="unverified")
    )
    (result,) = verify_bundle(bundle)
    assert any("'unverified'" in n for n in result.notes)


# ---------------------------------------------------------------------------
# Artifact hashes
# ---------------------------------------------------------------------------

def test_artifact_hash_match(tmp_path):
    blob = tmp_path / "proof.lean"
    blob.write_bytes(b"theorem perfect28 : True := trivial")
    digest = hashlib.sha256(blob.read_bytes()).hexdigest()
    bundle = _bundle(
        _entry(
            witness=_proof(),
            artifact={"path": "proof.lean", "sha256": digest},
        )
    )
    (result,) = verify_bundle(bundle, base_dir=tmp_path)
    assert result.ok is True
    assert any("sha256 verified" in n for n in result.notes)


def test_artifact_hash_mismatch_refutes(tmp_path):
    blob = tmp_path / "proof.lean"
    blob.write_bytes(b"tampered")
    bundle = _bundle(
        _entry(
            witness=_proof(),
            artifact={"path": "proof.lean", "sha256": "0" * 64},
        )
    )
    (result,) = verify_bundle(bundle, base_dir=tmp_path)
    assert result.ok is False
    assert any("MISMATCH" in n for n in result.notes)


def test_artifact_missing_file_does_not_refute(tmp_path):
    bundle = _bundle(
        _entry(
            witness=_proof(),
            artifact={"path": "gone.lean", "sha256": "0" * 64},
        )
    )
    (result,) = verify_bundle(bundle, base_dir=tmp_path)
    assert result.ok is True
    assert any("not found locally" in n for n in result.notes)


# ---------------------------------------------------------------------------
# Derivations
# ---------------------------------------------------------------------------

def test_derivation_missing_premise_degrades_to_unverified():
    bundle = _bundle(
        _entry(
            claim_id=10,
            witness=_proof(),
            derivation={"premise_ids": [99], "rule": "modus_ponens"},
        )
    )
    (result,) = verify_bundle(bundle)
    assert result.ok is None


def test_derivation_unverified_premise_degrades_conclusion():
    premise = _entry(claim_id=1, witness=None)  # unverified: no witness, no probe
    conclusion = _entry(
        claim_id=2,
        witness=_proof(),
        derivation={"premise_ids": [1], "rule": "modus_ponens"},
    )
    results = verify_bundle(_bundle(premise, conclusion))
    assert results[0].ok is None
    assert results[1].ok is None


def test_derivation_refuted_premise_refutes_conclusion(tmp_path):
    blob = tmp_path / "premise.lean"
    blob.write_bytes(b"tampered")
    premise = _entry(  # refuted: artifact hash mismatch
        claim_id=1,
        witness=_proof(),
        artifact={"path": "premise.lean", "sha256": "0" * 64},
    )
    conclusion = _entry(
        claim_id=2,
        witness=_proof(),
        derivation={"premise_ids": [1], "rule": "modus_ponens"},
    )
    results = verify_bundle(_bundle(premise, conclusion), base_dir=tmp_path)
    assert results[0].ok is False
    assert results[1].ok is False


def test_derivation_valid_premise_keeps_conclusion_valid():
    premise = _entry(claim_id=1, witness=_proof())
    conclusion = _entry(
        claim_id=2,
        witness=_proof(),
        derivation={"premise_ids": [1], "rule": "modus_ponens"},
    )
    results = verify_bundle(_bundle(premise, conclusion))
    assert all(r.ok is True for r in results)


@pytest.mark.parametrize("body", [
    {}, {"schema": "unknown"}, {"schema": []}, [],
    {"schema": "matrix_word", "generators": ["a"], "word": ["a"], "target": [[1]]},
    {"schema": "unit_fraction", "target": "1", "denominators": [1], "constraints": ["odd"]},
    {"schema": "factorization", "n": float("inf"), "factors": [[2, 1]]},
])
def test_uncheckable_witness_never_counts_as_proof(body):
    entry = _entry(witness={"kind": "term", "body": body})
    (result,) = verify_bundle(_bundle(entry))
    assert result.ok is None
    assert any("unverified" in note for note in result.notes)


def test_kernel_rejects_invalid_proof_despite_valid_producer_status():
    (result,) = verify_bundle(_bundle(_entry(witness=_proof(valid=False))))
    assert result.ok is False
    assert result.kernel_ok is False


@pytest.mark.parametrize("key,value", [
    ("valid_from", "2099-01-01T00:00:00+00:00"),
    ("valid_until", "not a timestamp"),
])
def test_unusable_validity_windows_are_unverified(key, value):
    entry = _entry(witness=_proof())
    entry["certificate"]["valid_under"] = {key: value}
    assert verify_bundle(_bundle(entry))[0].ok is None


@pytest.mark.parametrize("leaf_state", ["expired", "revoked", "refuted", "missing"])
def test_transitive_derivations_are_independent_of_bundle_order(leaf_state):
    leaf = _entry(claim_id=1, witness=_proof(valid=leaf_state != "refuted"))
    if leaf_state == "expired":
        leaf["certificate"]["valid_under"] = {"valid_until": "2020-01-01T00:00:00Z"}
    if leaf_state == "revoked":
        leaf["revocation"] = {"reason": "withdrawn"}
    middle = _entry(claim_id=2, witness=_proof(),
                    derivation={"premise_ids": [1], "rule": "test"})
    top = _entry(claim_id=3, witness=_proof(),
                 derivation={"premise_ids": [2], "rule": "test"})
    entries = [middle, top] if leaf_state == "missing" else [leaf, middle, top]
    for order in itertools.permutations(entries):
        results = {r.claim_id: r.ok for r in verify_bundle(_bundle(*order))}
        expected = False if leaf_state == "refuted" else None
        assert results[2] is expected
        assert results[3] is expected


def test_cycles_and_their_consumers_fail_without_affecting_independent_claims():
    entries = [
        _entry(claim_id=1, witness=_proof(), derivation={"premise_ids": [2]}),
        _entry(claim_id=2, witness=_proof(), derivation={"premise_ids": [1]}),
        _entry(claim_id=3, witness=_proof(), derivation={"premise_ids": [1]}),
        _entry(claim_id=4, witness=_proof()),
    ]
    results = verify_bundle(_bundle(*entries))
    assert [r.ok for r in results] == [False, False, False, True]
    assert all("cycle_detected" in r.notes[-1] for r in results[:3])


def test_deep_bundle_derivations_do_not_depend_on_python_recursion_limit():
    entries = [_entry(claim_id=1, witness=_proof())]
    entries += [_entry(claim_id=i, witness=_proof(), derivation={"premise_ids": [i - 1]})
                for i in range(2, 1100)]
    assert all(r.ok is True for r in verify_bundle(_bundle(*reversed(entries))))


@pytest.mark.parametrize("entry", [
    {"claim": None},
    _entry(claim_id=True),
    _entry(witness=[]),
    _entry(derivation={"premise_ids": "1"}),
])
def test_verification_rejects_malformed_entries_before_any_probes(entry):
    with pytest.raises(ValueError):
        verify_bundle(_bundle(entry))


def test_duplicate_claim_ids_are_rejected_instead_of_overwriting_support():
    with pytest.raises(ValueError, match="duplicate"):
        validate_bundle(_bundle(_entry(claim_id=1), _entry(claim_id=1, statement="conflict")))


def test_legacy_exporter_duplicate_claim_rows_merge_all_premises_without_mutating_input():
    first = _entry(claim_id=3, witness=_proof(), derivation={
        "id": 1, "premise_ids": [1], "rule": "first", "asserted_at": "2026-01-01",
    })
    second = {**first, "derivation": {
        "id": 2, "premise_ids": [2], "rule": "second", "asserted_at": "2026-02-01",
    }}
    leaf = _entry(claim_id=2, witness=_proof())
    leaf["revocation"] = {"reason": "withdrawn"}
    bundle = _bundle(first, second, _entry(claim_id=1, witness=_proof()), leaf)
    untouched = deepcopy(bundle)
    normalized = validate_bundle(bundle)
    assert bundle == untouched
    assert len(normalized["claims"]) == 3
    combined = normalized["claims"][0]["derivation"]
    assert combined["premise_ids"] == [1, 2]
    assert combined["rule"] == "all_derivations"
    assert combined["combination"] == "all"
    assert combined["rules"] == [first["derivation"], second["derivation"]]
    assert verify_bundle(bundle)[0].ok is None


def test_exact_duplicate_claim_rows_collapse_to_one_result():
    entry = _entry(witness=_proof())
    assert len(verify_bundle(_bundle(entry, deepcopy(entry)))) == 1


def test_duplicate_claim_json_boolean_and_integer_are_not_identical():
    first = _entry(witness=_proof())
    second = deepcopy(first)
    first["claim"]["subject_ref"] = {"flag": True}
    second["claim"]["subject_ref"] = {"flag": 1}
    with pytest.raises(ValueError, match="conflicting duplicate"):
        validate_bundle(_bundle(first, second))


@pytest.mark.parametrize("metadata", [
    {"status": []}, {}, {"status": "valid", "premise_hashes": 3},
    {"status": "valid", "premise_hashes": [{}]}, {"status": "valid", "row_hash": []},
    {"status": "valid", "prev_hash": []},
])
def test_malformed_v2_hash_metadata_is_rejected_before_hashing(metadata):
    entry = _entry(witness=_proof())
    entry["certificate"] = metadata
    bundle = _bundle(entry)
    bundle["trunk_bundle_version"] = 2
    with pytest.raises(ValueError):
        verify_bundle(bundle)


@pytest.mark.parametrize("rules", [None, [], ["rule"]])
def test_malformed_derivation_rule_metadata_is_rejected(rules):
    entry = _entry(witness=_proof(), derivation={"rules": rules})
    with pytest.raises(ValueError):
        validate_bundle(_bundle(entry))


class _ProbeConnection:
    def __init__(self, verdict=True, error=None):
        self.verdict, self.error = verdict, error
        self.info = SimpleNamespace(transaction_status=0)
        self.statements = []
        self.rolled_back = False
        self.prepared = False

    def transaction(self, *, force_rollback):
        assert force_rollback
        return self

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, *, prepare=False):
        self.statements.append(sql)
        self.prepared = prepare
        if self.error and prepare:
            raise self.error

    def fetchone(self):
        return (self.verdict, {})

    def rollback(self):
        self.rolled_back = True


def test_true_probe_cannot_override_a_refuted_kernel_witness():
    conn = _ProbeConnection()
    entry = _entry(witness=_proof(valid=False), probe_sql="SELECT TRUE")
    assert verify_bundle(_bundle(entry), conn)[0].ok is False
    assert conn.statements == []


def test_probe_uses_a_readonly_bounded_transaction_and_single_statement_protocol():
    conn = _ProbeConnection()
    assert verify_bundle(_bundle(_entry(probe_sql="SELECT TRUE")), conn)[0].ok is True
    assert conn.statements == [
        "SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = '5s'", "SELECT TRUE",
    ]
    assert conn.prepared
    assert conn.rolled_back


def test_probe_does_not_commit_or_rollback_a_callers_active_transaction():
    conn = _ProbeConnection()
    conn.info.transaction_status = 2
    result = verify_bundle(_bundle(_entry(probe_sql="COMMIT")), conn)[0]
    assert result.ok is None
    assert conn.statements == []
    assert conn.rolled_back is False


def test_true_probe_cannot_promote_an_uncheckable_witness():
    conn = _ProbeConnection()
    entry = _entry(witness={"kind": "term", "body": {}}, probe_sql="SELECT TRUE")
    assert verify_bundle(_bundle(entry), conn)[0].ok is None
    assert conn.rolled_back


def test_fresh_true_probe_supplies_its_own_evidence_after_revocation():
    entry = _entry(probe_sql="SELECT TRUE")
    entry["revocation"] = {"reason": "withdrawn"}
    result = verify_bundle(_bundle(entry), _ProbeConnection())[0]
    assert result.ok is True
    assert any("REVOKED" in note for note in result.notes)


@pytest.mark.parametrize("version", [1, 2])
def test_broken_chain_cannot_be_bypassed_by_a_true_probe_or_version_downgrade(version):
    entry = _entry(witness=_proof(), probe_sql="SELECT TRUE")
    entry["certificate"]["row_hash"] = "0" * 64
    bundle = _bundle(entry)
    bundle["trunk_bundle_version"] = version
    conn = _ProbeConnection()
    result = verify_bundle(bundle, conn)[0]
    assert result.ok is False
    assert conn.statements == []


@pytest.mark.parametrize("version", [1, 2])
def test_claim_commitment_failure_blocks_probe_replay_for_every_bundle_version(version):
    entry = _entry(witness=_proof(), probe_sql="SELECT TRUE")
    entry["certificate"]["valid_under"] = {
        "claim_digest_kind": CLAIM_DIGEST_KIND, "claim_digest": claim_digest(entry["claim"]),
    }
    entry["claim"]["statement"] = "substituted statement"
    bundle = _bundle(entry)
    bundle["trunk_bundle_version"] = version
    conn = _ProbeConnection()
    assert verify_bundle(bundle, conn)[0].ok is False
    assert conn.statements == []


def _hashed_entry():
    entry = _entry(witness=_proof())
    cert = entry["certificate"]
    cert["cert_id"] = 1
    cert["row_hash"] = certificate_row_hash(1, 1, "valid", {}, {}, None, None, [])
    entry["witness"]["row_hash"] = witness_row_hash(
        1, cert["row_hash"], "term", entry["witness"]["body"]
    )
    return entry


def test_intact_v2_chain_still_allows_independently_checked_legacy_proof():
    bundle = _bundle(_hashed_entry())
    bundle["trunk_bundle_version"] = 2
    result = verify_bundle(bundle)[0]
    assert result.ok is True
    assert result.claim_bound is None
    assert any("legacy certificate" in note for note in result.notes)


def test_tampered_witness_hash_is_not_ignored_when_kernel_still_passes():
    entry = _hashed_entry()
    entry["witness"]["row_hash"] = "0" * 64
    bundle = _bundle(entry)
    bundle["trunk_bundle_version"] = 2
    result = verify_bundle(bundle)[0]
    assert result.kernel_ok is True
    assert result.chain["content_ok"] is True
    assert result.chain["witness_ok"] is False
    assert result.ok is False


@pytest.mark.parametrize("verdict,error", [(None, None), (True, RuntimeError("unavailable"))])
def test_unavailable_probe_is_unverified_and_rolled_back(verdict, error):
    conn = _ProbeConnection(verdict, error)
    assert verify_bundle(_bundle(_entry(probe_sql="SELECT TRUE")), conn)[0].ok is None
    assert conn.rolled_back


def test_standalone_verifier_fails_for_uncheckable_claim_without_psycopg():
    tool = Path(__file__).resolve().parents[1] / "tools" / "verify_bundle.py"
    result = subprocess.run([sys.executable, "-S", str(tool)],
                            input=json.dumps(_bundle(_entry())),
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 1, result.stderr
    assert "unverified" in result.stdout


def test_standalone_verifier_uses_shared_lifecycle_and_proof_checks():
    tool = Path(__file__).resolve().parents[1] / "tools" / "verify_bundle.py"
    entry = _entry(witness=_proof())
    entry["revocation"] = {"reason": "withdrawn"}
    result = subprocess.run([sys.executable, "-S", str(tool)],
                            input=json.dumps(_bundle(entry)),
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 1, result.stderr
    assert "unverified" in result.stdout


def test_kernel_bundle_compatibility_api_honors_lifecycle_and_composition():
    from calx.kernel import verify_bundle as kernel_bundle

    leaf = _entry(claim_id=1, witness=_proof())
    leaf["revocation"] = {"reason": "withdrawn"}
    top = _entry(claim_id=2, witness=_proof(), derivation={"premise_ids": [1]})
    assert all(r["independent_verdict"] == "unverified"
               for r in kernel_bundle({"claims": [top, leaf]}))


# ---------------------------------------------------------------------------
# Probe replay against a live DB
# ---------------------------------------------------------------------------

@pytest.fixture()
def trunk_conn():
    # Dedicated test DSN or skip -- same guard as tests/conftest.py::calx_dsn
    # and tests/test_cert_lifecycle.py.  Probe replay is read-only in intent,
    # but it executes arbitrary probe_sql from a bundle against whatever this
    # connects to, which is not something to point at the canonical ledger by
    # default.  The `or calx_db.resolve_dsn()` that used to close this
    # expression is how test debris reached that ledger elsewhere in the suite.
    dsn = (
        os.environ.get("CALX_TEST_DSN")
        or os.environ.get("ARITHMETIC_DB_TEST_DSN")
    )
    if not dsn:
        pytest.skip("No CALX_TEST_DSN provided. Refusing to replay probes "
                    "against the default/production ledger.")
    conn = connect_or_skip(dsn)
    yield conn
    conn.close()


def test_probe_replay_true(trunk_conn):
    bundle = _bundle(_entry(probe_sql="SELECT TRUE, jsonb_build_object('n', 1)"))
    (result,) = verify_bundle(bundle, trunk_conn)
    assert result.ok is True


def test_probe_replay_false(trunk_conn):
    bundle = _bundle(_entry(probe_sql="SELECT FALSE, '{}'::jsonb"))
    (result,) = verify_bundle(bundle, trunk_conn)
    assert result.ok is False


def test_probe_error_is_unverified(trunk_conn):
    bundle = _bundle(_entry(probe_sql="SELECT * FROM no_such_table_anywhere"))
    (result,) = verify_bundle(bundle, trunk_conn)
    assert result.ok is None
    assert any("probe failed" in n for n in result.notes)


def test_probe_state_rolls_back(trunk_conn):
    bundle = _bundle(
        _entry(
            probe_sql=(
                "CREATE TABLE bundle_probe_leak (id int); SELECT TRUE, '{}'::jsonb"
            )
        )
    )
    verify_bundle(bundle, trunk_conn)
    with trunk_conn.cursor() as cur:
        cur.execute("SELECT to_regclass('bundle_probe_leak')")
        assert cur.fetchone()[0] is None
    trunk_conn.rollback()


@pytest.mark.parametrize("autocommit", [False, True])
@pytest.mark.parametrize("probe", [
    "CREATE TABLE public.{table} (id int)",
    "CREATE TABLE public.{table} (id int); COMMIT; SELECT TRUE",
    "COMMIT; CREATE TABLE public.{table} (id int)",
    "DO $$ BEGIN EXECUTE 'CREATE TABLE public.{table} (id int)'; END $$",
])
def test_probe_writes_cannot_escape_readonly_transaction(trunk_conn, probe, autocommit):
    trunk_conn.autocommit = autocommit
    table = f"bundle_readonly_{uuid.uuid4().hex}"
    entry = _entry(probe_sql=probe.format(table=table))
    result = verify_bundle(_bundle(entry), trunk_conn)[0]
    assert result.ok is None
    with trunk_conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        assert cur.fetchone()[0] is None
    trunk_conn.rollback()


@pytest.mark.parametrize("probe", ["COMMIT", "COMMIT AND CHAIN", "ROLLBACK"])
def test_single_transaction_control_probe_leaves_no_live_transaction(trunk_conn, probe):
    from psycopg.pq import TransactionStatus

    assert verify_bundle(_bundle(_entry(probe_sql=probe)), trunk_conn)[0].ok is None
    assert trunk_conn.info.transaction_status == TransactionStatus.IDLE


def test_probe_does_not_end_an_existing_caller_transaction(trunk_conn):
    from psycopg.pq import TransactionStatus

    with trunk_conn.cursor() as cur:
        cur.execute("SELECT 1")
    result = verify_bundle(_bundle(_entry(probe_sql="COMMIT")), trunk_conn)[0]
    assert result.ok is None
    assert trunk_conn.info.transaction_status == TransactionStatus.INTRANS
    trunk_conn.rollback()
