"""DB-backed tests for the OEIS cosine candidate layer (92_oeis_cosine.sql).

Inserts descriptor rows directly via calx.vectorize_terms (no sequence_membership
/ integers FK needed), then exercises ranking, the exact-prefix bridge, and the
cosine→exact-confirm claim. Skips cleanly when the calx DB is unreachable.
"""

from __future__ import annotations

import math
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


@pytest.fixture(autouse=True)
def _clean_seq_vec(conn):
    with conn.cursor() as cur:
        cur.execute("TRUNCATE calx.seq_vector")
    conn.commit()
    yield

def _vectorize_py(terms, k=None):
    t = terms[: (k or len(terms))]
    v = [math.log1p(abs(x)) for x in t]
    m = sum(v) / len(v)
    return [x - m for x in v]


def _fib(k):
    a, b, out = 1, 1, []
    for _ in range(k):
        out.append(a)
        a, b = b, a + b
    return out


def _insert(cur, seq_id, terms, kind="logc"):
    cur.execute(
        "INSERT INTO calx.seq_vector (seq_id, vector_kind, k, terms, vec) "
        "VALUES (%s,%s,%s,%s, calx.vectorize_terms(%s::numeric[], %s)) "
        "ON CONFLICT (seq_id, vector_kind) DO UPDATE SET terms=EXCLUDED.terms, vec=EXCLUDED.vec",
        (seq_id, kind, len(terms), terms, terms, len(terms)),
    )


def test_vectorize_terms_matches_python(conn):
    terms = [1, 1, 2, 3, 5, 8, 13, 21]
    with conn.cursor() as cur:
        cur.execute("SELECT calx.vectorize_terms(%s::numeric[], %s)", (terms, len(terms)))
        sql_vec = cur.fetchone()[0]
    py = _vectorize_py(terms)
    assert sql_vec == pytest.approx(py, rel=1e-9)


def test_vec_cosine_matches_python(conn):
    a = [1.0, -2.0, 3.0, 0.5]
    b = [2.0, 1.0, 0.0, 4.0]
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    with conn.cursor() as cur:
        cur.execute("SELECT calx.vec_cosine(%s, %s)", (a, b))
        assert cur.fetchone()[0] == pytest.approx(dot / (na * nb), rel=1e-9)


def test_terms_prefix_agree(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT calx.terms_prefix_agree(%s::numeric[], %s::numeric[])",
                    ([1, 1, 2, 3, 5], [1, 1, 2, 9, 9]))
        assert cur.fetchone()[0] == 3
        cur.execute("SELECT calx.terms_prefix_agree(%s::numeric[], %s::numeric[])",
                    ([1, 1, 2], [1, 1, 2]))
        assert cur.fetchone()[0] == 3


def test_scaled_variant_high_cosine_but_zero_exact(conn):
    """The headline: cosine recovers a scaled sequence the exact matcher rejects."""
    tag = uuid.uuid4().hex[:8]
    k = 12
    fib = _fib(k)
    fib2 = [2 * x for x in fib]
    lucas = [2, 1, 3, 4, 7, 11, 18, 29, 47, 76, 123, 199]
    with conn.cursor() as cur:
        q = f"Fib_{tag}"
        _insert(cur, q, fib)
        _insert(cur, f"Fibx2_{tag}", fib2)
        _insert(cur, f"Lucas_{tag}", lucas)
        cur.execute(
            "SELECT seq_id, cosine, exact_prefix FROM calx.oeis_cosine_candidates(%s, 5)", (q,))
        rows = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    cos2, ex2 = rows[f"Fibx2_{tag}"]
    assert cos2 > 0.99            # scale-invariant: shape matches
    assert ex2 == 0               # exact verifier rejects (1 != 2 at term 1)
    # Lucas (same recurrence) is also a strong shape neighbour
    assert rows[f"Lucas_{tag}"][0] > 0.99


def test_cosine_match_claim_confirms_and_refutes(conn):
    tag = uuid.uuid4().hex[:8]
    fib = _fib(12)
    with conn.cursor() as cur:
        a, b, c = f"A_{tag}", f"B_{tag}", f"C_{tag}"
        _insert(cur, a, fib)
        _insert(cur, b, fib)                       # identical -> exact confirms
        _insert(cur, c, [2 * x for x in fib])      # scaled -> exact refutes
        cur.execute("SELECT calx.oeis_cosine_match_claim(%s,%s,%s)", (a, b, 8))
        claim_ok = cur.fetchone()[0]
        cur.execute("SELECT calx.oeis_cosine_match_claim(%s,%s,%s)", (a, c, 8))
        claim_bad = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (claim_ok,))
        assert cur.fetchone()[0] == "valid"
        cur.execute("SELECT (cert.check(%s)).status", (claim_bad,))
        assert cur.fetchone()[0] == "refuted"
