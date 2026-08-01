"""
tests/test_derivation_closure.py
================================
Transitive closure over the proof-composition DAG (step 102).

Write-heavy: refuses to run without a dedicated test DSN (same guard as
tests/test_cert_lifecycle.py) — claims, derivations, and revocations are
append-only and cannot be cleaned up.

Covers:
  - cert.derivation_premise: trigger fans one row per (conclusion, premise)
  - cert.tainted_closure: direct (depth 1) and transitive (depth 2) taint
  - taint_status carries the seed's effective_status (revoked vs unchecked)
  - cert.derivation_valid_deep: clean chain passes, revoked ancestor fails,
    leaf claim is vacuously true, malformed cycles terminate
  - the depth-1 gap: cert.derivation_valid still passes where deep fails
  - cert.verify is rewired to the deep check: upstream revocation now
    degrades downstream verification transitively
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
            cur.execute("SELECT to_regclass('cert.derivation_premise')")
            if cur.fetchone()[0] is None:
                pytest.skip(
                    "cert.derivation_premise missing — apply 102_cert_derivation_closure.sql first"
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
            " VALUES ('closure_test', '{}'::jsonb, %s,"
            " 'computational', 'comp_sql', %s) RETURNING id",
            (f"closure test claim {uuid.uuid4()}", TRUE_PROBE),
        )
        claim_id = cur.fetchone()[0]
    conn.commit()
    return claim_id


def _check(conn, claim_id) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT (cert.check(%s)).id", (claim_id,))
        cert_id = cur.fetchone()[0]
    conn.commit()
    return cert_id


def _derive(conn, conclusion_id, premise_ids, rule="modus_ponens") -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO cert.derivation (conclusion_id, premise_ids, rule)"
            " VALUES (%s, %s::bigint[], %s) RETURNING id",
            (conclusion_id, premise_ids, rule),
        )
        deriv_id = cur.fetchone()[0]
    conn.commit()
    return deriv_id


def _revoke(conn, claim_id, reason="closure test"):
    with conn.cursor() as cur:
        cur.execute("SELECT (cert.revoke_claim(%s, %s)).id", (claim_id, reason))
    conn.commit()


def _taints(conn, claim_id) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tainted_by, taint_status, depth FROM cert.tainted_closure"
            " WHERE claim_id = %s ORDER BY depth",
            (claim_id,),
        )
        return cur.fetchall()


def _deep(conn, claim_id) -> tuple:
    with conn.cursor() as cur:
        cur.execute("SELECT ok, evidence FROM cert.derivation_valid_deep(%s)", (claim_id,))
        return cur.fetchone()


def _chain(conn, length=3) -> list[int]:
    """Build claims c[0] <- c[1] <- ... (each derived from the previous), all valid."""
    claims = [_mk_claim(conn) for _ in range(length)]
    for c in claims:
        _check(conn, c)
    for lower, upper in zip(claims, claims[1:], strict=False):
        _derive(conn, upper, [lower])
    return claims


# ---------------------------------------------------------------------------
# Edge table fan-out
# ---------------------------------------------------------------------------

def test_derivation_insert_fans_out_edges(conn):
    p1, p2, concl = _mk_claim(conn), _mk_claim(conn), _mk_claim(conn)
    deriv = _derive(conn, concl, [p1, p2])
    with conn.cursor() as cur:
        cur.execute(
            "SELECT premise_id FROM cert.derivation_premise"
            " WHERE derivation_id = %s AND conclusion_id = %s ORDER BY premise_id",
            (deriv, concl),
        )
        assert [r[0] for r in cur.fetchall()] == sorted([p1, p2])


def test_duplicate_premises_dedupe_to_one_edge(conn):
    p, concl = _mk_claim(conn), _mk_claim(conn)
    deriv = _derive(conn, concl, [p, p])
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM cert.derivation_premise WHERE derivation_id = %s",
            (deriv,),
        )
        assert cur.fetchone()[0] == 1


# ---------------------------------------------------------------------------
# Tainted closure
# ---------------------------------------------------------------------------

def test_valid_chain_is_untainted(conn):
    a, b, c = _chain(conn)
    assert _taints(conn, b) == []
    assert _taints(conn, c) == []


def test_revocation_taints_direct_conclusion(conn):
    a, b, _ = _chain(conn)
    _revoke(conn, a)
    rows = _taints(conn, b)
    assert (a, "revoked", 1) in rows


def test_revocation_taints_transitively(conn):
    a, _, c = _chain(conn)
    _revoke(conn, a)
    rows = _taints(conn, c)
    assert (a, "revoked", 2) in rows


def test_unchecked_premise_taints_with_its_status(conn):
    unchecked = _mk_claim(conn)  # never cert.check'ed
    concl = _mk_claim(conn)
    _check(conn, concl)
    _derive(conn, concl, [unchecked])
    rows = _taints(conn, concl)
    assert (unchecked, "unchecked", 1) in rows


# ---------------------------------------------------------------------------
# Deep derivation check
# ---------------------------------------------------------------------------

def test_deep_valid_on_clean_chain(conn):
    _, _, c = _chain(conn)
    ok, ev = _deep(conn, c)
    assert ok is True
    assert ev["transitive_premises"] == 2
    assert ev["invalid_count"] == 0


def test_deep_fails_on_revoked_ancestor_where_shallow_passes(conn):
    a, b, c = _chain(conn)
    _revoke(conn, a)

    # The documented depth-1 gap: c's direct premise b still stands valid...
    with conn.cursor() as cur:
        cur.execute(
            "SELECT d.ok FROM cert.derivation dv,"
            " LATERAL cert.derivation_valid(dv.id) d"
            " WHERE dv.conclusion_id = %s",
            (c,),
        )
        assert cur.fetchone()[0] is True

    # ...but the transitive check sees the revoked root.
    ok, ev = _deep(conn, c)
    assert ok is False
    bad = {e["claim_id"]: e for e in ev["invalid_premises"]}
    assert bad[a]["status"] == "revoked"
    assert bad[a]["depth"] == 2


def test_deep_vacuous_on_leaf_claim(conn):
    leaf = _mk_claim(conn)
    _check(conn, leaf)
    ok, ev = _deep(conn, leaf)
    assert ok is True
    assert ev["vacuous"] is True
    assert ev["transitive_premises"] == 0


def test_verify_degrades_transitively_after_upstream_revocation(conn):
    a, _, c = _chain(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT ok FROM cert.verify(%s)", (c,))
        assert cur.fetchone()[0] is True  # clean chain verifies

    _revoke(conn, a)
    with conn.cursor() as cur:
        cur.execute("SELECT ok, evidence FROM cert.verify(%s)", (c,))
        ok, ev = cur.fetchone()
    assert ok is False  # the probe still replays TRUE; the support collapsed
    bad = {e["claim_id"]: e["status"] for e in ev["derivation"]["invalid_premises"]}
    assert bad[a] == "revoked"


def test_deep_detects_and_fails_malformed_cycle(conn):
    a, b = _mk_claim(conn), _mk_claim(conn)
    _check(conn, a)
    _check(conn, b)
    _derive(conn, a, [b])
    _derive(conn, b, [a])
    ok, ev = _deep(conn, a)  # must terminate, and circular support must not pass
    assert ok is False
    assert ev["cycle_detected"] is True
    assert ev["invalid_count"] == 0  # every node stands valid; the SHAPE is the flaw
