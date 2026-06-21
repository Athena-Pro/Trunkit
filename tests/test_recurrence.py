"""Tests for the C-finite / P-finite recurrence certificate layer (93).

Unit tests (DB-free) check the exact generator; DB-backed tests confirm the SQL
mirrors Python and that recurrence claims attest valid / refuted via cert.check.
Skips cleanly when no test DSN is set (never writes to a production ledger).
"""

from __future__ import annotations

import json
import math
import os
import uuid

import psycopg
import pytest

from calx import recurrence as rec

# (polys ascending in n, init, expected prefix)
FIB = ([[1], [-1], [-1]], [1, 1], [1, 1, 2, 3, 5, 8, 13, 21, 34, 55])
FACT = ([[1], [0, -1]], [1], [1, 1, 2, 6, 24, 120, 720])           # a_n = n·a_{n-1}
CATALAN = ([[1, 1], [2, -4]], [1], [1, 1, 2, 5, 14, 42, 132, 429])  # (n+1)a_n=(4n-2)a_{n-1}


# --- unit (DB-free) ---------------------------------------------------------

def test_generate_fibonacci():
    polys, init, expected = FIB
    assert rec.generate(polys, init, len(expected)) == expected


def test_generate_factorial():
    polys, init, expected = FACT
    assert rec.generate(polys, init, len(expected)) == expected
    assert rec.generate(polys, init, 7)[6] == math.factorial(6)


def test_generate_catalan():
    polys, init, expected = CATALAN
    assert rec.generate(polys, init, len(expected)) == expected


def test_non_exact_division_raises():
    # a_n = a_{n-1}/2  ->  [[2],[-1]], init [1]  -> a_1 = 1/2 not integer
    with pytest.raises(ValueError):
        rec.generate([[2], [-1]], [1], 3)


def test_matches_false_on_wrong_terms():
    polys, init, _ = FIB
    assert rec.matches(polys, init, [1, 1, 2, 3, 5]) is True
    assert rec.matches(polys, init, [1, 1, 2, 3, 6]) is False
    assert rec.matches([[2], [-1]], [1], [1, 0, 0]) is False  # non-exact -> False


# --- DB-backed --------------------------------------------------------------

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


@pytest.mark.parametrize("case", [FIB, FACT, CATALAN], ids=["fib", "factorial", "catalan"])
def test_sql_generate_matches_python(conn, case):
    polys, init, expected = case
    with conn.cursor() as cur:
        cur.execute(
            "SELECT cert.recurrence_generate(%s::jsonb, %s::numeric[], %s)",
            (json.dumps(polys), init, len(expected)),
        )
        sql_terms = [int(x) for x in cur.fetchone()[0]]
    assert sql_terms == rec.generate(polys, init, len(expected)) == expected


def _register(cur, seq_id, polys, init, terms, kind="p_finite"):
    cur.execute(
        "SELECT (cert.register_recurrence(%s,%s,%s::jsonb,%s::numeric[],%s::numeric[])).id",
        (seq_id, kind, json.dumps(polys), init, terms),
    )
    return cur.fetchone()[0]


def test_recurrence_claim_valid(conn):
    polys, init, terms = FIB
    with conn.cursor() as cur:
        rid = _register(cur, f"fib_{uuid.uuid4().hex[:8]}", polys, init, terms, kind="c_finite")
        cur.execute("SELECT cert.recurrence_claim(%s)", (rid,))
        claim = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (claim,))
        assert cur.fetchone()[0] == "valid"


def test_recurrence_claim_refuted_on_wrong_terms(conn):
    polys, init, _ = FIB
    bad_terms = [1, 1, 2, 3, 6, 9, 15]  # not Fibonacci
    with conn.cursor() as cur:
        rid = _register(cur, f"bad_{uuid.uuid4().hex[:8]}", polys, init, bad_terms)
        cur.execute("SELECT cert.recurrence_claim(%s)", (rid,))
        claim = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (claim,))
        assert cur.fetchone()[0] == "refuted"


def test_recurrence_claim_refuted_on_non_exact(conn):
    # a_n = a_{n-1}/2 claimed to produce integers -> generation non-exact -> refuted
    with conn.cursor() as cur:
        rid = _register(cur, f"halve_{uuid.uuid4().hex[:8]}", [[2], [-1]], [1], [1, 0, 0])
        cur.execute("SELECT cert.recurrence_claim(%s)", (rid,))
        claim = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (claim,))
        assert cur.fetchone()[0] == "refuted"
