"""
tests/test_cert_board.py
========================
Cert observability (step 118): status board + crown consensus.

A plain `trunkit init` must create the surfaces SKILL.md documents — they used
to exist only in a workspace overlay.

Covers:
  - cert.board / cert.board_summary exist with the documented columns
  - board_summary totals agree with the board it aggregates
  - cert.crown_consensus: no evidence => 'unverified' (never a silent green)
  - cert.crown_consensus topologies: veto / parallel / series / threshold,
    including the 'contested' partial-closure verdict and k_star dissent cost

Votes are written inside a transaction that is rolled back, against claim ids
that cannot collide with real claims (negative), so the ledger is left as found.
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
            cur.execute("SELECT to_regclass('cert.board')")
            if cur.fetchone()[0] is None:
                pytest.skip("cert.board missing — apply 118_cert_board.sql first")
        yield c
    finally:
        c.rollback()
        c.close()


def _fresh_claim_id() -> int:
    """A negative int4 id: cert.claim ids are positive, so this never collides."""
    return -(uuid.uuid4().int % 2_000_000_000) - 1


def _vote(conn, claim_id: int, model: str, agrees: bool, cost: float = 1) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO cert.evidence_vote (claim_id, model_name, agrees, cost)"
            " VALUES (%s, %s, %s, %s)",
            (claim_id, model, agrees, cost),
        )


def _consensus(conn, claim_id: int, topology: str = "veto", k: int | None = None):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT verdict, k_star, agree_n, total_n"
            " FROM cert.crown_consensus(%s, %s, %s)",
            (claim_id, topology, k),
        )
        return cur.fetchone()


def test_board_exposes_documented_columns(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM cert.board LIMIT 0")
        assert [d.name for d in cur.description] == [
            "claim_id", "area", "status", "plain", "statement",
        ]
        cur.execute("SELECT * FROM cert.board_summary LIMIT 0")
        assert [d.name for d in cur.description] == [
            "area", "verified", "failed", "unknown", "total",
        ]


def test_board_summary_totals_match_board(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM cert.board")
        board_rows = cur.fetchone()[0]
        cur.execute(
            "SELECT COALESCE(sum(total), 0),"
            "       COALESCE(bool_and(verified + failed + unknown <= total), TRUE)"
            "  FROM cert.board_summary"
        )
        summed, buckets_fit = cur.fetchone()
    assert summed == board_rows
    assert buckets_fit


def test_crown_consensus_without_evidence_is_unverified(conn):
    verdict, k_star, agree_n, total_n = _consensus(conn, _fresh_claim_id())
    assert verdict == "unverified"
    assert k_star is None
    assert (agree_n, total_n) == (0, 0)


def test_crown_consensus_split_evidence_is_contested_under_veto(conn):
    claim = _fresh_claim_id()
    _vote(conn, claim, "model-a", True)
    _vote(conn, claim, "model-b", False, cost=2)
    _vote(conn, claim, "model-c", False, cost=3)

    verdict, k_star, agree_n, total_n = _consensus(conn, claim, "veto")
    assert verdict == "contested"
    assert k_star == 3            # veto = max dissent cost
    assert (agree_n, total_n) == (1, 3)

    # series = sum of dissent cost; still contested (some agree, some dissent)
    verdict, k_star, _, _ = _consensus(conn, claim, "series")
    assert (verdict, k_star) == ("contested", 5)

    # parallel = one agreeing party suffices; an agreeing party costs 0 to yield
    verdict, k_star, _, _ = _consensus(conn, claim, "parallel")
    assert (verdict, k_star) == ("valid", 0)


def test_crown_consensus_threshold_is_k_of_n(conn):
    claim = _fresh_claim_id()
    _vote(conn, claim, "model-a", True)
    _vote(conn, claim, "model-b", False)
    _vote(conn, claim, "model-c", False)

    # default k = majority (2 of 3): one agreeing vote is not enough
    assert _consensus(conn, claim, "threshold")[0] == "contested"
    assert _consensus(conn, claim, "threshold", 1)[0] == "valid"


def test_crown_consensus_unanimous_verdicts(conn):
    agreed, rejected = _fresh_claim_id(), _fresh_claim_id()
    for model in ("model-a", "model-b"):
        _vote(conn, agreed, model, True)
        _vote(conn, rejected, model, False)

    verdict, k_star, _, _ = _consensus(conn, agreed, "veto")
    assert (verdict, k_star) == ("valid", 0)
    assert _consensus(conn, rejected, "veto")[0] == "refuted"
