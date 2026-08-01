"""
tests/test_signer_independence.py
=================================
Signer independence over derivation premises (step 103) — the
institutional-attestation pattern: each precondition attested by a separate
source, none of them the concluder itself.

Write-heavy: refuses to run without a dedicated test DSN (same guard as
tests/test_cert_lifecycle.py).

Covers:
  - pairwise-distinct signers pass; a shared signer fails
  - min_distinct override relaxes the default pairwise requirement
  - self-attestation (conclusion signer among premise signers) fails
  - revoked / unchecked premises fail independence
  - independence degrades when a premise certificate is revoked LATER
  - cert.independence_claim attests valid / refuted via cert.check
"""

from __future__ import annotations

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
            cur.execute("SELECT to_regprocedure('cert.derivation_independent(bigint,int)')")
            if cur.fetchone()[0] is None:
                pytest.skip(
                    "cert.derivation_independent missing — "
                    "apply 103_cert_signer_independence.sql first"
                )
        yield c
    finally:
        c.close()


TRUE_PROBE = "SELECT TRUE, '{}'::jsonb"


def _mk_claim(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO cert.claim (subject_kind, subject_ref, statement,"
            " claim_kind, method, probe_sql)"
            " VALUES ('independence_test', '{}'::jsonb, %s,"
            " 'computational', 'comp_sql', %s) RETURNING id",
            (f"independence test claim {uuid.uuid4()}", TRUE_PROBE),
        )
        claim_id = cur.fetchone()[0]
    conn.commit()
    return claim_id


def _check_as(conn, claim_id, signer) -> int:
    """cert.check under a specific signer identity; returns certificate id."""
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('trunkit.signer', %s, false)", (signer,))
        cur.execute("SELECT (cert.check(%s)).id", (claim_id,))
        cert_id = cur.fetchone()[0]
        cur.execute("RESET trunkit.signer")
    conn.commit()
    return cert_id


def _derive(conn, conclusion_id, premise_ids, rule="gated_action") -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO cert.derivation (conclusion_id, premise_ids, rule)"
            " VALUES (%s, %s::bigint[], %s) RETURNING id",
            (conclusion_id, premise_ids, rule),
        )
        deriv_id = cur.fetchone()[0]
    conn.commit()
    return deriv_id


def _independent(conn, deriv_id, min_distinct=None) -> tuple:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ok, evidence FROM cert.derivation_independent(%s, %s)",
            (deriv_id, min_distinct),
        )
        return cur.fetchone()


def _gate(conn, signers) -> tuple[list[int], int, int]:
    """Premises checked by the given signers + an uncertified conclusion."""
    premises = [_mk_claim(conn) for _ in signers]
    for claim, signer in zip(premises, signers, strict=True):
        _check_as(conn, claim, signer)
    conclusion = _mk_claim(conn)
    deriv = _derive(conn, conclusion, premises)
    return premises, conclusion, deriv


# ---------------------------------------------------------------------------
# Distinct signers
# ---------------------------------------------------------------------------

def test_pairwise_distinct_signers_pass(conn):
    _, _, deriv = _gate(conn, ["alice", "bob"])
    ok, ev = _independent(conn, deriv)
    assert ok is True
    assert ev["distinct_signers"] == 2
    assert ev["required"] == 2
    assert ev["self_attested"] is False


def test_shared_signer_fails_pairwise_default(conn):
    _, _, deriv = _gate(conn, ["alice", "alice"])
    ok, ev = _independent(conn, deriv)
    assert ok is False
    assert ev["distinct_signers"] == 1
    assert ev["required"] == 2


def test_min_distinct_override_relaxes(conn):
    _, _, deriv = _gate(conn, ["alice", "alice", "bob"])
    assert _independent(conn, deriv)[0] is False        # pairwise default: 3 needed
    ok, ev = _independent(conn, deriv, min_distinct=2)  # policy: any 2 sources
    assert ok is True
    assert ev["distinct_signers"] == 2


# ---------------------------------------------------------------------------
# Self-attestation
# ---------------------------------------------------------------------------

def test_self_attestation_fails(conn):
    premises, conclusion, deriv = _gate(conn, ["alice", "bob"])
    _check_as(conn, conclusion, "alice")  # concluder is also a premise signer
    ok, ev = _independent(conn, deriv)
    assert ok is False
    assert ev["self_attested"] is True
    assert ev["conclusion_signer"] == "alice"


def test_disjoint_conclusion_signer_passes(conn):
    _, conclusion, deriv = _gate(conn, ["alice", "bob"])
    _check_as(conn, conclusion, "carol")
    ok, ev = _independent(conn, deriv)
    assert ok is True
    assert ev["self_attested"] is False


# ---------------------------------------------------------------------------
# Premise standing feeds independence
# ---------------------------------------------------------------------------

def test_unchecked_premise_fails(conn):
    unchecked = _mk_claim(conn)  # never certified: no signer, not valid
    signed = _mk_claim(conn)
    _check_as(conn, signed, "bob")
    deriv = _derive(conn, _mk_claim(conn), [unchecked, signed])
    ok, ev = _independent(conn, deriv)
    assert ok is False
    assert ev["invalid_premises"] == 1
    assert ev["unsigned_premises"] == 1


def test_revocation_degrades_independence(conn):
    premises, _, deriv = _gate(conn, ["alice", "bob"])
    assert _independent(conn, deriv)[0] is True
    with conn.cursor() as cur:
        cur.execute("SELECT (cert.revoke_claim(%s, 'source compromised')).id",
                    (premises[0],))
    conn.commit()
    ok, ev = _independent(conn, deriv)
    assert ok is False
    assert ev["invalid_premises"] == 1


# ---------------------------------------------------------------------------
# The claim wrapper
# ---------------------------------------------------------------------------

def test_independence_claim_attests_valid(conn):
    _, _, deriv = _gate(conn, ["alice", "bob"])
    with conn.cursor() as cur:
        cur.execute("SELECT cert.independence_claim(%s)", (deriv,))
        claim = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (claim,))
        assert cur.fetchone()[0] == "valid"


def test_independence_claim_refuted_on_shared_signer(conn):
    _, _, deriv = _gate(conn, ["alice", "alice"])
    with conn.cursor() as cur:
        cur.execute("SELECT cert.independence_claim(%s)", (deriv,))
        claim = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (claim,))
        assert cur.fetchone()[0] == "refuted"
