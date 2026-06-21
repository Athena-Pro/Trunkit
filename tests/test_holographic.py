"""Holographic / Merkle commitment tests (96)."""

from __future__ import annotations

import hashlib
import os
import uuid

import psycopg
import pytest


def _lh(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _merkle(leaves):
    if not leaves:
        return _lh("")
    level = [_lh(x) for x in leaves]
    while len(level) > 1:
        nxt, i = [], 0
        while i < len(level):
            pair = level[i] + (level[i + 1] if i + 1 < len(level) else level[i])
            nxt.append(_lh(pair))
            i += 2
        level = nxt
    return level[0]


def _calx_dsn():
    dsn = os.environ.get("CALX_TEST_DSN") or os.environ.get("ARITHMETIC_DB_TEST_DSN")
    if not dsn:
        pytest.skip("No test DSN provided. Refusing to write to default/production ledger.")
    return dsn


@pytest.fixture()
def conn():
    try:
        c = psycopg.connect(_calx_dsn(), connect_timeout=3)
    except psycopg.Error as exc:
        pytest.skip(f"calx DB not reachable: {exc}")
    with c:
        yield c


def test_merkle_matches_python_and_is_compact(conn):
    leaves = [f"term:{i}" for i in range(1000)]   # a long "trace"
    with conn.cursor() as cur:
        cur.execute("SELECT cert.merkle_root(%s::text[])", (leaves,))
        root = cur.fetchone()[0]
    assert root == _merkle(leaves)
    assert len(root) == 64                          # 32-byte commitment, regardless of trace length


def test_merkle_tamper_sensitive(conn):
    a = [str(i) for i in range(50)]
    b = a.copy(); b[37] = "999"                     # flip one term
    with conn.cursor() as cur:
        cur.execute("SELECT cert.merkle_root(%s::text[]), cert.merkle_root(%s::text[])", (a, b))
        ra, rb = cur.fetchone()
    assert ra != rb


def test_claim_commitment_roundtrip(conn):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql) "
            "VALUES ('test','{}'::jsonb,%s,'computational','comp_sql',%s) RETURNING id",
            (f"holo {uuid.uuid4()}", "SELECT true AS ok, '{}'::jsonb AS evidence"),
        )
        cid = cur.fetchone()[0]
        cur.execute("SELECT cert.check(%s)", (cid,))
        cur.execute("SELECT cert.claim_commitment(%s)", (cid,))
        root = cur.fetchone()[0]
        assert root and len(root) == 64
        cur.execute("SELECT cert.verify_commitment(%s,%s)", (cid, root))
        assert cur.fetchone()[0] is True
        cur.execute("SELECT cert.verify_commitment(%s,%s)", (cid, "deadbeef"))
        assert cur.fetchone()[0] is False
