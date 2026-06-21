"""Verified sequence morphism tests (95)."""

from __future__ import annotations

import json
import os
import uuid

import psycopg
import pytest


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


FIB = [1, 1, 2, 3, 5, 8, 13, 21]


def test_morphism_apply_kinds(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT cert.morphism_apply('scale', %s::jsonb, %s::numeric[])",
                    (json.dumps({"c": 2}), FIB))
        assert [int(x) for x in cur.fetchone()[0]] == [2 * x for x in FIB]
        cur.execute("SELECT cert.morphism_apply('affine', %s::jsonb, %s::numeric[])",
                    (json.dumps({"a": 3, "b": 1}), FIB))
        assert [int(x) for x in cur.fetchone()[0]] == [3 * x + 1 for x in FIB]
        cur.execute("SELECT cert.morphism_apply('index_shift', %s::jsonb, %s::numeric[])",
                    (json.dumps({"s": 2}), FIB))
        assert [int(x) for x in cur.fetchone()[0]] == FIB[2:]


def _register(cur, src, dst, kind, params, src_terms, dst_terms):
    cur.execute(
        "SELECT (cert.register_morphism(%s,%s,%s,%s::jsonb,%s::numeric[],%s::numeric[])).id",
        (src, dst, kind, json.dumps(params), src_terms, dst_terms),
    )
    return cur.fetchone()[0]


def test_scale_morphism_valid(conn):
    """The cosine-flagged Fib×2 case, now proven exactly as a scale morphism."""
    tag = uuid.uuid4().hex[:6]
    with conn.cursor() as cur:
        mid = _register(cur, f"fib_{tag}", f"fibx2_{tag}", "scale", {"c": 2}, FIB, [2 * x for x in FIB])
        cur.execute("SELECT cert.morphism_claim(%s)", (mid,))
        claim = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (claim,))
        assert cur.fetchone()[0] == "valid"
        # morphism claims are tagged exact_int (94)
        cur.execute("SELECT domain FROM cert.claim WHERE id=%s", (claim,))
        assert cur.fetchone()[0] == "exact_int"


def test_wrong_morphism_refuted(conn):
    tag = uuid.uuid4().hex[:6]
    with conn.cursor() as cur:
        mid = _register(cur, f"fib_{tag}", f"x_{tag}", "scale", {"c": 2}, FIB, [3 * x for x in FIB])
        cur.execute("SELECT cert.morphism_claim(%s)", (mid,))
        claim = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (claim,))
        assert cur.fetchone()[0] == "refuted"
