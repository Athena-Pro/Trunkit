"""Unit tests for the CRT layer.

These tests do not need the integers/primes tables populated; we exercise
the functions in isolation by inserting just enough rows where required.
"""

from __future__ import annotations

import math

import pytest


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
def test_ext_gcd(calx_conn, a, b, expected_g):
    g, s, t = _call_row(calx_conn, "SELECT g, s, t FROM ext_gcd(%s, %s)", a, b)
    assert g == expected_g
    assert a * s + b * t == g


@pytest.mark.parametrize("a, m", [(3, 7), (10, 17), (7, 13)])
def test_mod_inverse(calx_conn, a, m):
    inv = _call_scalar(calx_conn, "SELECT mod_inverse(%s, %s)", a, m)
    assert (a * inv) % m == 1
    assert 0 <= inv < m


def test_mod_inverse_no_inverse_raises(calx_conn):
    import psycopg

    with pytest.raises(psycopg.errors.RaiseException):
        _call_scalar(calx_conn, "SELECT mod_inverse(%s, %s)", 6, 9)


def test_crt_three_moduli(calx_conn):
    x = _call_scalar(
        calx_conn,
        "SELECT crt(ARRAY[2,3,2]::BIGINT[], ARRAY[3,5,7]::BIGINT[])",
    )
    assert x == 23
    for r, m in zip([2, 3, 2], [3, 5, 7]):
        assert x % m == r


def test_progression_intersect_compatible(calx_conn):
    r, m, intersects = _call_row(
        calx_conn,
        "SELECT remainder, modulus, intersects FROM progression_intersect(0, 2, 0, 3)",
    )
    assert intersects is True
    assert m == 6
    assert r % 2 == 0 and r % 3 == 0


def test_progression_intersect_incompatible(calx_conn):
    r, m, intersects = _call_row(
        calx_conn,
        "SELECT remainder, modulus, intersects FROM progression_intersect(1, 4, 2, 6)",
    )
    assert intersects is False
