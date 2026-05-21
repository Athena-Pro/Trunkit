"""Property checks: factorization product reconstructs n; ω/Ω agree with rows."""

from __future__ import annotations

import math

import pytest

from calx import generate


@pytest.fixture(scope="module")
def populated_module(_initialized_calx_db):
    from calx import db as _db

    with _db.connect(_initialized_calx_db) as c:
        with c.cursor() as cur:
            cur.execute(
                "TRUNCATE factorizations, primes, integers RESTART IDENTITY CASCADE"
            )
        generate.generate_pure(c, 500)
        yield c


def test_product_recovers_n(populated_module):
    with populated_module.cursor() as cur:
        cur.execute(
            """
            SELECT n, prime, exponent
            FROM factorizations
            ORDER BY n, prime
            """
        )
        rows = cur.fetchall()
    by_n: dict[int, list[tuple[int, int]]] = {}
    for n, p, e in rows:
        by_n.setdefault(n, []).append((p, e))
    for n, factors in by_n.items():
        product = math.prod(p**e for p, e in factors)
        assert product == n


def test_omega_matches_distinct_factor_count(populated_module):
    with populated_module.cursor() as cur:
        cur.execute(
            """
            SELECT i.n, i.omega, COUNT(f.prime)
            FROM integers i
            LEFT JOIN factorizations f ON f.n = i.n
            WHERE i.n > 1
            GROUP BY i.n, i.omega
            """
        )
        for n, omega, count in cur.fetchall():
            assert omega == count, f"omega mismatch at n={n}"


def test_big_omega_matches_exponent_sum(populated_module):
    with populated_module.cursor() as cur:
        cur.execute(
            """
            SELECT i.n, i.big_omega, COALESCE(SUM(f.exponent), 0)
            FROM integers i
            LEFT JOIN factorizations f ON f.n = i.n
            WHERE i.n > 1
            GROUP BY i.n, i.big_omega
            """
        )
        for n, big_omega, exp_sum in cur.fetchall():
            assert big_omega == exp_sum, f"big_omega mismatch at n={n}"
