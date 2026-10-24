"""End-to-end correctness for the pure PL/pgSQL pipeline at small N."""

from __future__ import annotations

import pytest

from calx import generate


PRIMES_UNDER_100 = [
    2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47,
    53, 59, 61, 67, 71, 73, 79, 83, 89, 97,
]


@pytest.fixture()
def populated(calx_conn):
    generate.generate_pure(calx_conn, 100)
    return calx_conn


def test_prime_count(populated):
    with populated.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM primes")
        (n,) = cur.fetchone()
    assert n == 25


def test_prime_membership(populated):
    with populated.cursor() as cur:
        cur.execute("SELECT p FROM primes ORDER BY p")
        rows = [r[0] for r in cur.fetchall()]
    assert rows == PRIMES_UNDER_100


@pytest.mark.parametrize(
    "n, expected",
    [
        (12, "2^2 · 3"),
        (60, "2^2 · 3 · 5"),
        (97, "97"),
        (2, "2"),
    ],
)
def test_prime_signatures(populated, n, expected):
    with populated.cursor() as cur:
        cur.execute("SELECT signature FROM prime_signatures WHERE n = %s", (n,))
        (sig,) = cur.fetchone()
    assert sig == expected


@pytest.mark.parametrize("n, tau", [(1, 1), (12, 6), (60, 12), (97, 2)])
def test_tau(populated, n, tau):
    if n == 1:
        pytest.skip("τ(1)=1 is not stored as a factorization row")
    with populated.cursor() as cur:
        cur.execute("SELECT tau FROM divisor_count WHERE n = %s", (n,))
        (got,) = cur.fetchone()
    assert got == tau


def test_perfect_numbers_under_100(populated):
    with populated.cursor() as cur:
        cur.execute("SELECT n FROM perfect_numbers ORDER BY n")
        rows = [r[0] for r in cur.fetchall()]
    assert rows == [6, 28]


def test_pure_generate_reruns_and_extends(calx_conn):
    # A re-run, or a raised limit, must not die on primes_pkey.
    generate.generate_pure(calx_conn, 100)
    generate.generate_pure(calx_conn, 100)
    generate.generate_pure(calx_conn, 200)
    with calx_conn.cursor() as cur:
        cur.execute("SELECT p, discovered_order FROM primes ORDER BY p")
        rows = cur.fetchall()
        cur.execute("SELECT signature FROM prime_signatures WHERE n = 192")
        (sig,) = cur.fetchone()
    assert [p for p, _ in rows][:25] == PRIMES_UNDER_100
    assert [rank for _, rank in rows] == list(range(1, 47))  # pi(200) = 46
    assert sig == "2^6 · 3"
