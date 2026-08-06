"""Tests for the Chinese Remainder Theorem certificate layer (101).

Unit tests (DB-free) check the exact CRT reconstruction; DB-backed tests
confirm the SQL mirrors Python and that congruence claims attest valid /
refuted via cert.check. Skips cleanly when no test DSN is set (never writes
to a production ledger).
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from calx import congruence as cong

# (remainders, moduli, x) — x ≡ 2 (mod 3), x ≡ 3 (mod 5), x ≡ 2 (mod 7) -> 23
SUNZI = ([2, 3, 2], [3, 5, 7], 23)
SINGLE = ([4], [9], 4)
LARGER = ([1, 2, 3, 4], [3, 5, 7, 11], None)  # solved below


# --- unit (DB-free) ---------------------------------------------------------

def test_crt_sunzi():
    remainders, moduli, expected = SUNZI
    assert cong.crt(remainders, moduli) == expected


def test_crt_single_congruence():
    remainders, moduli, expected = SINGLE
    assert cong.crt(remainders, moduli) == expected


def test_matches_true_for_correct_solution():
    remainders, moduli, expected = SUNZI
    assert cong.matches(remainders, moduli, expected) is True
    assert cong.matches(remainders, moduli, expected + 105) is True  # mod M periodicity


def test_matches_false_for_wrong_solution():
    remainders, moduli, expected = SUNZI
    assert cong.matches(remainders, moduli, expected + 1) is False


def test_non_coprime_moduli_raises_in_crt():
    with pytest.raises(ValueError):
        cong.crt([1, 1], [4, 6])  # gcd(4, 6) = 2 -> no inverse


def test_non_coprime_moduli_matches_is_false_not_raise():
    assert cong.matches([1, 1], [4, 6], 1) is False


def test_ext_gcd_bezout_identity():
    a, b = 240, 46
    g, s, t = cong.ext_gcd(a, b)
    assert g == 2
    assert a * s + b * t == g


def test_mod_inverse_round_trips():
    a, m = 7, 26
    inv = cong.mod_inverse(a, m)
    assert (a * inv) % m == 1


def test_crt_four_way():
    remainders, moduli, _ = LARGER
    x = cong.crt(remainders, moduli)
    for r, m in zip(remainders, moduli, strict=True):
        assert x % m == r


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


@pytest.mark.parametrize("case", [SUNZI, SINGLE], ids=["sunzi", "single"])
def test_sql_crt_matches_python(conn, case):
    remainders, moduli, expected = case
    with conn.cursor() as cur:
        cur.execute("SELECT crt(%s::bigint[], %s::bigint[])", (remainders, moduli))
        sql_solution = cur.fetchone()[0]
    assert sql_solution == cong.crt(remainders, moduli) == expected


def _register(cur, subject_id, remainders, moduli, x):
    cur.execute(
        "SELECT (cert.register_congruence(%s,%s::bigint[],%s::bigint[],%s)).id",
        (subject_id, remainders, moduli, x),
    )
    return cur.fetchone()[0]


def test_congruence_claim_valid(conn):
    remainders, moduli, expected = SUNZI
    with conn.cursor() as cur:
        rid = _register(cur, f"sunzi_{uuid.uuid4().hex[:8]}", remainders, moduli, expected)
        cur.execute("SELECT cert.congruence_claim(%s)", (rid,))
        claim = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (claim,))
        assert cur.fetchone()[0] == "valid"


def test_congruence_claim_refuted_on_wrong_x(conn):
    remainders, moduli, expected = SUNZI
    with conn.cursor() as cur:
        rid = _register(cur, f"bad_{uuid.uuid4().hex[:8]}", remainders, moduli, expected + 1)
        cur.execute("SELECT cert.congruence_claim(%s)", (rid,))
        claim = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (claim,))
        assert cur.fetchone()[0] == "refuted"


def test_congruence_claim_refuted_on_non_coprime_moduli(conn):
    with conn.cursor() as cur:
        rid = _register(cur, f"noncoprime_{uuid.uuid4().hex[:8]}", [1, 1], [4, 6], 1)
        cur.execute("SELECT cert.congruence_claim(%s)", (rid,))
        claim = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (claim,))
        assert cur.fetchone()[0] == "refuted"
