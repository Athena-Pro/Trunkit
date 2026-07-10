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
import json
import os

import pytest

from calx import db as calx_db
from calx.bundle import load_bundle, verify_bundle
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
        },
        "certificate": {"seq": 1, "status": cert_status},
        "witness": witness,
        "derivation": derivation,
        "artifact": artifact,
    }


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
    bundle = _bundle(_entry(probe_sql=None, witness={"kind": "term", "body": {}}))
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
    entry = _entry(witness={"kind": "term", "body": {}})
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
    entry = _entry(witness={"kind": "term", "body": {}})
    entry["certificate"]["valid_under"] = {"valid_until": "2020-01-01T00:00:00+00:00"}
    (result,) = verify_bundle(_bundle(entry))
    assert result.ok is None
    assert any("expired" in n for n in result.notes)


def test_unexpired_window_leaves_verdict_alone():
    entry = _entry(witness={"kind": "term", "body": {}})
    entry["certificate"]["valid_under"] = {"valid_until": "2099-01-01T00:00:00+00:00"}
    (result,) = verify_bundle(_bundle(entry))
    assert result.ok is True


def test_v2_bundle_version_accepted(tmp_path):
    bundle = _bundle(_entry(witness={"kind": "term", "body": {}}))
    bundle["trunk_bundle_version"] = 2
    path = tmp_path / "v2.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    assert load_bundle(path)["trunk_bundle_version"] == 2


def test_nonvalid_producer_status_is_surfaced():
    bundle = _bundle(
        _entry(witness={"kind": "term", "body": {}}, cert_status="unverified")
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
            witness={"kind": "hash_chain", "body": {}},
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
            witness={"kind": "hash_chain", "body": {}},
            artifact={"path": "proof.lean", "sha256": "0" * 64},
        )
    )
    (result,) = verify_bundle(bundle, base_dir=tmp_path)
    assert result.ok is False
    assert any("MISMATCH" in n for n in result.notes)


def test_artifact_missing_file_does_not_refute(tmp_path):
    bundle = _bundle(
        _entry(
            witness={"kind": "hash_chain", "body": {}},
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
            witness={"kind": "term", "body": {}},
            derivation={"premise_ids": [99], "rule": "modus_ponens"},
        )
    )
    (result,) = verify_bundle(bundle)
    assert result.ok is None


def test_derivation_unverified_premise_degrades_conclusion():
    premise = _entry(claim_id=1, witness=None)  # unverified: no witness, no probe
    conclusion = _entry(
        claim_id=2,
        witness={"kind": "term", "body": {}},
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
        witness={"kind": "hash_chain", "body": {}},
        artifact={"path": "premise.lean", "sha256": "0" * 64},
    )
    conclusion = _entry(
        claim_id=2,
        witness={"kind": "term", "body": {}},
        derivation={"premise_ids": [1], "rule": "modus_ponens"},
    )
    results = verify_bundle(_bundle(premise, conclusion), base_dir=tmp_path)
    assert results[0].ok is False
    assert results[1].ok is False


def test_derivation_valid_premise_keeps_conclusion_valid():
    premise = _entry(claim_id=1, witness={"kind": "term", "body": {}})
    conclusion = _entry(
        claim_id=2,
        witness={"kind": "term", "body": {}},
        derivation={"premise_ids": [1], "rule": "modus_ponens"},
    )
    results = verify_bundle(_bundle(premise, conclusion))
    assert all(r.ok is True for r in results)


# ---------------------------------------------------------------------------
# Probe replay against a live DB
# ---------------------------------------------------------------------------

@pytest.fixture()
def trunk_conn():
    dsn = (
        os.environ.get("CALX_TEST_DSN")
        or os.environ.get("ARITHMETIC_DB_TEST_DSN")
        or calx_db.resolve_dsn()
    )
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


def test_probe_error_refutes(trunk_conn):
    bundle = _bundle(_entry(probe_sql="SELECT * FROM no_such_table_anywhere"))
    (result,) = verify_bundle(bundle, trunk_conn)
    assert result.ok is False
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
