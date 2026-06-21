"""Exact C-finite / P-finite recurrence generation (cert recurrence layer).

Pure-Python mirror of the SQL in `93_cert_recurrence.sql`, so the two agree and
the math is unit-testable without a database. No third-party dependency — the
calx core stays psycopg-only.

A P-finite (holonomic) recurrence of order d, 0-indexed, with polynomial
coefficients in n:

    p0(n)·a_n + p1(n)·a_{n-1} + … + pd(n)·a_{n-d} = 0
  ⇒ a_n = −( p1(n)·a_{n-1} + … + pd(n)·a_{n-d} ) / p0(n)

`polys` is a list of d+1 coefficient lists, each ascending in n (so polys[0] is
p0). C-finite is the special case where every polynomial is a constant.
Generation is exact integer arithmetic and raises on a vanishing leading
coefficient or a non-exact division (no float, no irrational leakage).
"""

from __future__ import annotations

from collections.abc import Sequence


def poly_eval(coeffs: Sequence[int], n: int) -> int:
    """Evaluate an ascending-coefficient polynomial at integer n (exact)."""
    return sum(c * (n ** i) for i, c in enumerate(coeffs))


def generate(polys: Sequence[Sequence[int]], init: Sequence[int], count: int) -> list[int]:
    """First `count` terms of the recurrence; exact, raises on non-exactness."""
    d = len(polys) - 1
    if d < 1:
        raise ValueError("recurrence order must be >= 1")
    if len(init) < d:
        raise ValueError(f"need >= {d} initial terms, got {len(init)}")
    v = list(init[:d])
    for n in range(d, count):              # 0-based term index
        p0 = poly_eval(polys[0], n)
        if p0 == 0:
            raise ValueError(f"leading coefficient vanishes at n={n}")
        s = sum(poly_eval(polys[j], n) * v[n - j] for j in range(1, d + 1))
        num = -s
        if num % p0 != 0:
            raise ValueError(f"non-exact division at n={n} ({num} / {p0})")
        v.append(num // p0)
    return v[:count]


def matches(polys, init, terms, *, count: int | None = None) -> bool:
    """True iff the recurrence regenerates `terms` exactly (mismatch/non-exact → False)."""
    k = count if count is not None else len(terms)
    try:
        gen = generate(polys, init, k)
    except ValueError:
        return False
    return gen[:k] == list(terms[:k])


# Common recurrences, as ready-made certificates (polys ascending in n):
#   Fibonacci   : a_n = a_{n-1} + a_{n-2}      -> [[1],[-1],[-1]], init [1,1]
#   factorial   : a_n = n·a_{n-1}              -> [[1],[0,-1]],     init [1]
#   Catalan     : (n+1)·a_n = (4n-2)·a_{n-1}   -> [[1,1],[2,-4]],   init [1]
