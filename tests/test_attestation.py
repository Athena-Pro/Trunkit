"""
tests/test_attestation.py
=========================
Tool-attestation axiom registry + auto-derivation (step 104), the first
implementation slice of docs/DESIGN_TOOL_ATTESTATION_TIER.md.

Write-heavy: refuses to run without a dedicated test DSN (same guard as
tests/test_cert_lifecycle.py).

Covers:
  - cert.register_attestation: name schema, idempotence, standing gate
    (unchecked / revoked claims cannot back an attestation axiom)
  - cert.attestation append-only law
  - witness attach with axiom_tier='tool_attested': auto-asserts the
    tool_attestation derivation (canonical premises, idempotent), rejected
    when an axiom is unregistered / mismatched / the list is empty
  - non-attested witnesses pass through untouched
  - end-to-end: cert.verify degrades the formal claim when an attestation
    is revoked (via the 102 deep check); signer independence (103) holds
    over the attesting claims
"""

from __future__ import annotations

import hashlib
import os
import uuid

import psycopg
import pytest


def _test_dsn() -> str:
    dsn = os.environ.get("CALX_TEST_DSN") or os.environ.get("ARITHMETIC_DB_TEST_DSN")
    if not dsn:
        pytest.skip("No test DSN provided. Refusing to write to default/production ledger.")
    return dsn


@pytest.fixture()
def conn():
    try:
        c = psycopg.connect(_test_dsn(), connect_timeout=3)
    except psycopg.Error as exc:
        pytest.skip(f"test DB not reachable: {exc}")
    try:
        with c.cursor() as cur:
            cur.execute("SELECT to_regclass('cert.attestation')")
            if cur.fetchone()[0] is None:
                pytest.skip("cert.attestation missing — apply 104_cert_attestation.sql first")
        yield c
    finally:
        c.close()


TRUE_PROBE = "SELECT TRUE, '{}'::jsonb"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _mk_claim(conn, *, probe_sql=TRUE_PROBE, method="comp_sql", kind="computational") -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO cert.claim (subject_kind, subject_ref, statement,"
            " claim_kind, method, probe_sql)"
            " VALUES ('attestation_test', '{}'::jsonb, %s, %s, %s, %s) RETURNING id",
            (f"attestation test claim {uuid.uuid4()}", kind, method, probe_sql),
        )
        claim_id = cur.fetchone()[0]
    conn.commit()
    return claim_id


def _check_as(conn, claim_id, signer="tool-runner") -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('trunkit.signer', %s, false)", (signer,))
        cur.execute("SELECT (cert.check(%s)).id", (claim_id,))
        cert_id = cur.fetchone()[0]
        cur.execute("RESET trunkit.signer")
    conn.commit()
    return cert_id


def _register(conn, claim_id, stmt: str) -> tuple[str, str]:
    """Register an attestation axiom; returns (axiom_name, stmt_sha256)."""
    sha = _sha(stmt)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT (cert.register_attestation(%s, %s)).axiom_name",
            (claim_id, sha),
        )
        name = cur.fetchone()[0]
    conn.commit()
    return name, sha


def _attested_source(conn, stmt: str, signer="tool-runner") -> tuple[int, str, str]:
    """A checked-valid claim registered as an attestation axiom."""
    claim = _mk_claim(conn)
    _check_as(conn, claim, signer)
    name, sha = _register(conn, claim, stmt)
    return claim, name, sha


def _attach(conn, claim_id, entries):
    body = {"axiom_tier": "tool_attested", "attestation_axioms": entries}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT cert.attach_witness(%s, 'term', %s::jsonb)",
            (claim_id, psycopg.types.json.Json(body)),
        )
    conn.commit()


def _derivations(conn, conclusion_id) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT premise_ids, rule FROM cert.derivation"
            " WHERE conclusion_id = %s ORDER BY id",
            (conclusion_id,),
        )
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_register_produces_schema_name_and_is_idempotent(conn):
    claim = _mk_claim(conn)
    _check_as(conn, claim)
    name, sha = _register(conn, claim, "CellAt r 'state' 'alabama'")
    assert name == f"trunkit_att_{claim}_{sha[:16]}"
    name2, _ = _register(conn, claim, "CellAt r 'state' 'alabama'")
    assert name2 == name
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM cert.attestation WHERE axiom_name = %s", (name,))
        assert cur.fetchone()[0] == 1


def test_register_rejects_unchecked_claim(conn):
    claim = _mk_claim(conn)  # never certified
    with pytest.raises(psycopg.errors.RaiseException, match="unchecked"), conn.cursor() as cur:
        cur.execute("SELECT cert.register_attestation(%s, %s)", (claim, _sha("x")))
    conn.rollback()


def test_register_rejects_revoked_claim(conn):
    claim = _mk_claim(conn)
    _check_as(conn, claim)
    with conn.cursor() as cur:
        cur.execute("SELECT (cert.revoke_claim(%s, 'tool compromised')).id", (claim,))
    conn.commit()
    with pytest.raises(psycopg.errors.RaiseException, match="revoked"), conn.cursor() as cur:
        cur.execute("SELECT cert.register_attestation(%s, %s)", (claim, _sha("y")))
    conn.rollback()


def test_attestation_registry_is_append_only(conn):
    claim, name, _ = _attested_source(conn, "SumWhere 'pop' all 42")
    for stmt in (
        "UPDATE cert.attestation SET claim_id = claim_id + 1 WHERE axiom_name = %s",
        "DELETE FROM cert.attestation WHERE axiom_name = %s",
    ):
        with pytest.raises(psycopg.errors.RaiseException), conn.cursor() as cur:
            cur.execute(stmt, (name,))
        conn.rollback()


# ---------------------------------------------------------------------------
# Witness binding
# ---------------------------------------------------------------------------

def test_attested_witness_asserts_derivation(conn):
    a, name_a, sha_a = _attested_source(conn, "fact A", signer="alice")
    b, name_b, sha_b = _attested_source(conn, "fact B", signer="bob")
    formal = _mk_claim(conn, probe_sql=None, method="formal_external", kind="formal")
    _check_as(conn, formal, signer="prover")

    _attach(conn, formal, [
        {"name": name_a, "claim_id": a, "stmt_sha256": sha_a},
        {"name": name_b, "claim_id": b, "stmt_sha256": sha_b},
    ])

    derivs = _derivations(conn, formal)
    assert derivs == [(sorted([a, b]), "tool_attestation")]

    # The fanout trigger (102) normalized the edges too.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM cert.derivation_premise WHERE conclusion_id = %s",
            (formal,),
        )
        assert cur.fetchone()[0] == 2

    # Witness present + attestations valid => verify ok.
    with conn.cursor() as cur:
        cur.execute("SELECT ok FROM cert.verify(%s)", (formal,))
        assert cur.fetchone()[0] is True

    # Signer independence (103) holds over the attesting tools.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT i.ok FROM cert.derivation d,"
            " LATERAL cert.derivation_independent(d.id) i"
            " WHERE d.conclusion_id = %s AND d.rule = 'tool_attestation'",
            (formal,),
        )
        assert cur.fetchone()[0] is True


def test_reattach_is_idempotent_on_derivation(conn):
    a, name_a, sha_a = _attested_source(conn, "fact C")
    formal = _mk_claim(conn, probe_sql=None, method="formal_external", kind="formal")
    _check_as(conn, formal)
    entry = [{"name": name_a, "claim_id": a, "stmt_sha256": sha_a}]
    _attach(conn, formal, entry)
    _check_as(conn, formal)  # fresh certificate seq
    _attach(conn, formal, entry)
    assert len(_derivations(conn, formal)) == 1


def test_attach_rejects_unregistered_axiom(conn):
    formal = _mk_claim(conn, probe_sql=None, method="formal_external", kind="formal")
    _check_as(conn, formal)
    with pytest.raises(psycopg.errors.RaiseException, match="not registered"):
        _attach(conn, formal, [{
            "name": "trunkit_att_999999999_0123456789abcdef",
            "claim_id": 999999999,
            "stmt_sha256": _sha("ghost"),
        }])
    conn.rollback()


def test_attach_rejects_binding_mismatch(conn):
    a, name_a, sha_a = _attested_source(conn, "fact D")
    other = _mk_claim(conn)
    formal = _mk_claim(conn, probe_sql=None, method="formal_external", kind="formal")
    _check_as(conn, formal)
    with pytest.raises(psycopg.errors.RaiseException, match="mismatch"):
        _attach(conn, formal, [
            {"name": name_a, "claim_id": other, "stmt_sha256": sha_a},
        ])
    conn.rollback()


def test_attach_rejects_empty_axiom_list(conn):
    formal = _mk_claim(conn, probe_sql=None, method="formal_external", kind="formal")
    _check_as(conn, formal)
    with pytest.raises(psycopg.errors.RaiseException, match="non-empty"):
        _attach(conn, formal, [])
    conn.rollback()


def test_plain_witness_passes_through(conn):
    formal = _mk_claim(conn, probe_sql=None, method="formal_external", kind="formal")
    _check_as(conn, formal)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT cert.attach_witness(%s, 'term', '{\"t\": 1}'::jsonb)", (formal,)
        )
    conn.commit()
    assert _derivations(conn, formal) == []


# ---------------------------------------------------------------------------
# End-to-end: revocation of an attestation degrades the formal claim
# ---------------------------------------------------------------------------

def test_revoking_attestation_degrades_formal_claim(conn):
    a, name_a, sha_a = _attested_source(conn, "fact E", signer="alice")
    formal = _mk_claim(conn, probe_sql=None, method="formal_external", kind="formal")
    _check_as(conn, formal, signer="prover")
    _attach(conn, formal, [{"name": name_a, "claim_id": a, "stmt_sha256": sha_a}])

    with conn.cursor() as cur:
        cur.execute("SELECT ok FROM cert.verify(%s)", (formal,))
        assert cur.fetchone()[0] is True

    with conn.cursor() as cur:
        cur.execute("SELECT (cert.revoke_claim(%s, 'tool output was wrong')).id", (a,))
    conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT ok, evidence FROM cert.verify(%s)", (formal,))
        ok, ev = cur.fetchone()
    assert ok is False
    bad = {e["claim_id"]: e["status"] for e in ev["derivation"]["invalid_premises"]}
    assert bad[a] == "revoked"

    # And the formal claim shows up in the tainted closure (102).
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM cert.tainted_closure"
            " WHERE claim_id = %s AND tainted_by = %s",
            (formal, a),
        )
        assert cur.fetchone()[0] >= 1
