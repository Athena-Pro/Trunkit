"""
tests/test_cert_lifecycle.py
============================
Certificate lifecycle (step 100): revocation, validity windows, signer identity.

Write-heavy: refuses to run without a dedicated test DSN (same guard as
tests/test_vacuity.py) — revocations are append-only and cannot be cleaned up.

Covers:
  - cert.revoke / cert.revoke_claim: append event, monotone, targets one cert
  - append-only law on cert.revocation (UPDATE/DELETE raise)
  - cert.standing.effective_status: 'revoked' / 'expired' supersede 'valid'
  - cert.verify: witnesses on revoked certificates stop counting (=> NULL)
  - probe replay is unaffected by revocation (fresh evidence stands)
  - re-attesting appends a fresh, unrevoked certificate (epoch semantics)
  - trunkit.cert_ttl GUC stamps valid_from/valid_until inside valid_under
  - trunkit.signer GUC binds signer_id on certificates and revocations
  - export_bundle carries the revocation event
"""

from __future__ import annotations

import json
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
            cur.execute("SELECT to_regclass('cert.revocation')")
            if cur.fetchone()[0] is None:
                pytest.skip("cert.revocation missing — apply 100_cert_lifecycle.sql first")
        yield c
    finally:
        c.close()


def _mk_claim(conn, *, probe_sql, method="comp_sql", kind="computational") -> int:
    """Insert a uniquely-named claim; returns its id."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO cert.claim (subject_kind, subject_ref, statement,"
            " claim_kind, method, probe_sql)"
            " VALUES ('lifecycle_test', '{}'::jsonb, %s, %s, %s, %s) RETURNING id",
            (f"lifecycle test claim {uuid.uuid4()}", kind, method, probe_sql),
        )
        claim_id = cur.fetchone()[0]
    conn.commit()
    return claim_id


def _check(conn, claim_id) -> int:
    """cert.check and return the new certificate id."""
    with conn.cursor() as cur:
        cur.execute("SELECT (cert.check(%s)).id", (claim_id,))
        cert_id = cur.fetchone()[0]
    conn.commit()
    return cert_id


def _standing(conn, claim_id) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, effective_status, revoked_at, valid_until, signer_id"
            " FROM cert.standing WHERE claim_id = %s",
            (claim_id,),
        )
        status, eff, revoked_at, valid_until, signer = cur.fetchone()
    return {"status": status, "effective": eff, "revoked_at": revoked_at,
            "valid_until": valid_until, "signer": signer}


TRUE_PROBE = "SELECT TRUE, '{}'::jsonb"


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------

def test_revoke_claim_marks_standing_revoked(conn):
    claim = _mk_claim(conn, probe_sql=TRUE_PROBE)
    _check(conn, claim)
    assert _standing(conn, claim)["effective"] == "valid"

    with conn.cursor() as cur:
        cur.execute("SELECT (cert.revoke_claim(%s, 'trust withdrawn')).id", (claim,))
    conn.commit()

    st = _standing(conn, claim)
    assert st["status"] == "valid"          # raw status untouched (append-only)
    assert st["effective"] == "revoked"
    assert st["revoked_at"] is not None


def test_revoke_is_monotone_and_idempotent(conn):
    claim = _mk_claim(conn, probe_sql=TRUE_PROBE)
    cert_id = _check(conn, claim)
    with conn.cursor() as cur:
        cur.execute("SELECT (cert.revoke(%s, 'first')).id", (cert_id,))
        first = cur.fetchone()[0]
        cur.execute("SELECT (cert.revoke(%s, 'second call')).id", (cert_id,))
        second = cur.fetchone()[0]
    conn.commit()
    assert first == second  # same event returned, no duplicate


def test_revocation_is_append_only(conn):
    claim = _mk_claim(conn, probe_sql=TRUE_PROBE)
    cert_id = _check(conn, claim)
    with conn.cursor() as cur:
        cur.execute("SELECT (cert.revoke(%s, 'x')).id", (cert_id,))
        rev_id = cur.fetchone()[0]
    conn.commit()
    for stmt in (
        "UPDATE cert.revocation SET reason = 'rewritten' WHERE id = %s",
        "DELETE FROM cert.revocation WHERE id = %s",
    ):
        with pytest.raises(psycopg.errors.RaiseException), conn.cursor() as cur:
            cur.execute(stmt, (rev_id,))
        conn.rollback()


def test_reattest_appends_fresh_unrevoked_certificate(conn):
    claim = _mk_claim(conn, probe_sql=TRUE_PROBE)
    _check(conn, claim)
    with conn.cursor() as cur:
        cur.execute("SELECT (cert.revoke_claim(%s, 'epoch rollover')).id", (claim,))
    conn.commit()
    assert _standing(conn, claim)["effective"] == "revoked"

    _check(conn, claim)  # new seq, not revoked
    assert _standing(conn, claim)["effective"] == "valid"


def test_probe_verify_unaffected_by_revocation(conn):
    claim = _mk_claim(conn, probe_sql=TRUE_PROBE)
    _check(conn, claim)
    with conn.cursor() as cur:
        cur.execute("SELECT (cert.revoke_claim(%s, 'x')).id", (claim,))
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT ok, evidence FROM cert.verify(%s)", (claim,))
        ok, ev = cur.fetchone()
    assert ok is True  # fresh probe replay stands on its own
    assert ev.get("lifecycle", {}).get("state") == "revoked"  # but it is surfaced


def test_witness_on_revoked_certificate_stops_counting(conn):
    claim = _mk_claim(conn, probe_sql=None, method="formal_external", kind="formal")
    cert_id = _check(conn, claim)  # unverified cert (no probe)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO cert.witness (certificate_id, kind, body)"
            " VALUES (%s, 'term', '{\"t\":1}'::jsonb)",
            (cert_id,),
        )
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT ok FROM cert.verify(%s)", (claim,))
        assert cur.fetchone()[0] is True  # witness present => attested

        cur.execute("SELECT (cert.revoke(%s, 'witness generator compromised')).id",
                    (cert_id,))
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT ok FROM cert.verify(%s)", (claim,))
        assert cur.fetchone()[0] is None  # revoked witness no longer counts


# ---------------------------------------------------------------------------
# Validity windows + signer identity
# ---------------------------------------------------------------------------

def test_cert_ttl_guc_stamps_window(conn):
    claim = _mk_claim(conn, probe_sql=TRUE_PROBE)
    with conn.cursor() as cur:
        cur.execute("SET trunkit.cert_ttl = '30 days'")
    _check(conn, claim)
    st = _standing(conn, claim)
    assert st["valid_until"] is not None
    assert st["effective"] == "valid"
    with conn.cursor() as cur:
        cur.execute("RESET trunkit.cert_ttl")
    conn.commit()


def test_expired_window_reads_expired(conn):
    claim = _mk_claim(conn, probe_sql=TRUE_PROBE)
    with conn.cursor() as cur:
        cur.execute("SET trunkit.cert_ttl = '-1 hours'")  # born expired
    _check(conn, claim)
    with conn.cursor() as cur:
        cur.execute("RESET trunkit.cert_ttl")
    conn.commit()
    st = _standing(conn, claim)
    assert st["status"] == "valid"
    assert st["effective"] == "expired"


def test_signer_guc_binds_identity(conn):
    claim = _mk_claim(conn, probe_sql=TRUE_PROBE)
    with conn.cursor() as cur:
        cur.execute("SET trunkit.signer = 'lifecycle-test-prover'")
    cert_id = _check(conn, claim)
    with conn.cursor() as cur:
        cur.execute("SELECT (cert.revoke(%s, 'signed revocation')).revoked_by", (cert_id,))
        revoked_by = cur.fetchone()[0]
        cur.execute("RESET trunkit.signer")
    conn.commit()
    st = _standing(conn, claim)
    assert st["signer"] == "lifecycle-test-prover"
    assert revoked_by == "lifecycle-test-prover"


# ---------------------------------------------------------------------------
# Bundle export carries the lifecycle
# ---------------------------------------------------------------------------

def test_export_bundle_carries_revocation(conn):
    claim = _mk_claim(conn, probe_sql=TRUE_PROBE)
    _check(conn, claim)
    with conn.cursor() as cur:
        cur.execute("SELECT (cert.revoke_claim(%s, 'do not trust')).id", (claim,))
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT cert.export_bundle(%s::bigint[])", ([claim],))
        bundle = cur.fetchone()[0]
    if isinstance(bundle, str):
        bundle = json.loads(bundle)
    (entry,) = bundle["claims"]
    assert entry["revocation"]["reason"] == "do not trust"
    assert entry["certificate"].get("signer_id") is not None
