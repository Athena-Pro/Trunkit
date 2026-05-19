"""Unit tests for the CRT layer.

These tests do not need the integers/primes tables populated; we exercise
the functions in isolation by inserting just enough rows where required.
"""

from __future__ import annotations

import psycopg
import pytest


@pytest.fixture(scope="module")
def conn(_initialized_calx_db):
    c = psycopg.connect(_initialized_calx_db)
    yield c
    c.close()


def _call_scalar(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


def _call_row(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


@pytest.mark.parametrize(
    "a, b, expected_g",
    [(12, 18, 6), (17, 31, 1), (0, 5, 5), (100, 75, 25)],
)
def test_ext_gcd(conn, a, b, expected_g):
    g, s, t = _call_row(conn, "SELECT g, s, t FROM ext_gcd(%s::bigint, %s::bigint)", a, b)
    assert g == expected_g
    assert a * s + b * t == g


@pytest.mark.parametrize("a, m", [(3, 7), (10, 17), (7, 13)])
def test_mod_inverse(conn, a, m):
    inv = _call_scalar(conn, "SELECT mod_inverse(%s::bigint, %s::bigint)", a, m)
    assert (a * inv) % m == 1
    assert 0 <= inv < m


def test_mod_inverse_no_inverse_raises(conn):
    with pytest.raises(psycopg.errors.RaiseException):
        _call_scalar(conn, "SELECT mod_inverse(%s::bigint, %s::bigint)", 6, 9)


def test_crt_three_moduli(conn):
    x = _call_scalar(
        conn,
        "SELECT crt(ARRAY[2,3,2]::BIGINT[], ARRAY[3,5,7]::BIGINT[])",
    )
    assert x == 23
    for r, m in zip([2, 3, 2], [3, 5, 7]):
        assert x % m == r


def test_progression_intersect_compatible(conn):
    r, m, intersects = _call_row(
        conn,
        "SELECT remainder, modulus, intersects FROM progression_intersect(0, 2, 0, 3)",
    )
    assert intersects is True
    assert m == 6
    assert r % 2 == 0 and r % 3 == 0


def test_progression_intersect_incompatible(conn):
    r, m, intersects = _call_row(
        conn,
        "SELECT remainder, modulus, intersects FROM progression_intersect(1, 4, 2, 6)",
    )
    assert intersects is False
