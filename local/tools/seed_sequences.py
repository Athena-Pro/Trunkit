"""Seed the ``sequences`` and ``sequence_membership`` tables with a curated
collection of named integer sequences and orbit traces.

Run after the DB has been populated via ``calx generate``.
Each sequence is stored once with all members ≤ MAX(integers.n).
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg

from calx import db


# ─── Sequence generators ─────────────────────────────────────────────────────

def collatz(start: int) -> Iterator[int]:
    n = start
    yield n
    while n != 1:
        n = n // 2 if n % 2 == 0 else 3 * n + 1
        yield n


def recaman() -> Iterator[int]:
    seen = {0}
    a = 0
    yield a
    i = 1
    while True:
        cand = a - i
        a = cand if cand > 0 and cand not in seen else a + i
        seen.add(a)
        yield a
        i += 1


def fibonacci() -> Iterator[int]:
    a, b = 1, 1
    yield a
    yield b
    while True:
        a, b = b, a + b
        yield b


def powers_of(base: int) -> Iterator[int]:
    v = 1
    while True:
        yield v
        v *= base


def triangular() -> Iterator[int]:
    i = 1
    while True:
        yield i * (i + 1) // 2
        i += 1


def squares() -> Iterator[int]:
    i = 1
    while True:
        yield i * i
        i += 1


def cubes() -> Iterator[int]:
    i = 1
    while True:
        yield i * i * i
        i += 1


def pentagonal() -> Iterator[int]:
    i = 1
    while True:
        yield i * (3 * i - 1) // 2
        i += 1


def hexagonal() -> Iterator[int]:
    i = 1
    while True:
        yield i * (2 * i - 1)
        i += 1


def factorials() -> Iterator[int]:
    f, i = 1, 1
    yield f
    while True:
        f *= i
        yield f
        i += 1


def catalan() -> Iterator[int]:
    """C(n) = (2n)! / ((n+1)! n!)"""
    from math import comb
    n = 0
    while True:
        yield comb(2 * n, n) // (n + 1)
        n += 1


def bell() -> Iterator[int]:
    """B(n) via the triangle recurrence."""
    row = [1]
    yield 1
    while True:
        new_row = [row[-1]]
        for v in row:
            new_row.append(new_row[-1] + v)
        row = new_row
        yield row[0]


def motzkin() -> Iterator[int]:
    """M(0)=1; M(n+1) = ((2n+3)M(n) + 3n M(n-1)) / (n+3)"""
    m_prev, m_cur = 1, 1
    yield 1
    yield 1
    n = 1
    while True:
        m_next = ((2 * n + 3) * m_cur + 3 * n * m_prev) // (n + 3)
        yield m_next
        m_prev, m_cur = m_cur, m_next
        n += 1


def aliquot_orbit(start: int, sigma_lookup: dict[int, int], lim_n: int, max_steps: int = 500) -> Iterator[int]:
    n = start
    yield n
    seen = {n}
    for _ in range(max_steps):
        if n == 1:
            break
        sigma = sigma_lookup.get(n)
        if sigma is None:
            break
        n = sigma - n
        if n <= 0 or n > lim_n or n in seen:
            break
        seen.add(n)
        yield n


# ─── DB helpers ──────────────────────────────────────────────────────────────

def upsert_sequence(cur, seq_id, name, seq_type, formula=None, modulus=None, residue=None):
    cur.execute(
        """
        INSERT INTO sequences (seq_id, name, seq_type, formula, modulus, residue)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (seq_id) DO UPDATE
          SET name     = EXCLUDED.name,
              seq_type = EXCLUDED.seq_type,
              formula  = EXCLUDED.formula,
              modulus  = EXCLUDED.modulus,
              residue  = EXCLUDED.residue
        """,
        (seq_id, name, seq_type, formula, modulus, residue),
    )


def install_members(cur, seq_id: str, generator: Iterator[int], lim: int, *, stop_after_overflow: int = 5):
    """Walk a generator, dedupe, drop n<1 or n>lim, store first-occurrence idx."""
    seen: set[int] = set()
    members: list[tuple[int, int]] = []
    idx = 0
    overflow = 0
    for v in generator:
        idx += 1
        if v < 1:
            continue
        if v > lim:
            overflow += 1
            if overflow >= stop_after_overflow:
                break
            continue
        if v in seen:
            continue
        seen.add(v)
        members.append((v, idx))

    cur.execute("DELETE FROM sequence_membership WHERE seq_id = %s", (seq_id,))
    if not members:
        return 0
    with cur.copy(
        "COPY sequence_membership (seq_id, n, idx) FROM STDIN"
    ) as copy:
        for n, i in members:
            copy.write_row((seq_id, n, i))
    return len(members)


def install_from_query(cur, seq_id: str, sql: str, params: tuple = ()) -> int:
    cur.execute(sql, params)
    members = list(cur.fetchall())
    cur.execute("DELETE FROM sequence_membership WHERE seq_id = %s", (seq_id,))
    if not members:
        return 0
    with cur.copy(
        "COPY sequence_membership (seq_id, n, idx) FROM STDIN"
    ) as copy:
        for n, i in members:
            copy.write_row((seq_id, n, i))
    return len(members)


# ─── Catalog ─────────────────────────────────────────────────────────────────

NAMED_SEQUENCES = [
    ("A000045", "Fibonacci",          "recursive",      "F(n)=F(n-1)+F(n-2)",                   fibonacci),
    ("A000079", "Powers of 2",        "multiplicative", "2^n",                                  lambda: powers_of(2)),
    ("A000244", "Powers of 3",        "multiplicative", "3^n",                                  lambda: powers_of(3)),
    ("A000351", "Powers of 5",        "multiplicative", "5^n",                                  lambda: powers_of(5)),
    ("A000217", "Triangular",         "arithmetic",     "n(n+1)/2",                             triangular),
    ("A000290", "Squares",            "multiplicative", "n^2",                                  squares),
    ("A000578", "Cubes",              "multiplicative", "n^3",                                  cubes),
    ("A000326", "Pentagonal",         "arithmetic",     "n(3n-1)/2",                            pentagonal),
    ("A000384", "Hexagonal",          "arithmetic",     "n(2n-1)",                              hexagonal),
    ("A000142", "Factorials",         "recursive",      "n!",                                   factorials),
    ("A000108", "Catalan",            "recursive",      "C(2n,n)/(n+1)",                        catalan),
    ("A000110", "Bell",               "recursive",      "B(n)",                                 bell),
    ("A001006", "Motzkin",            "recursive",      "M(n)",                                 motzkin),
    ("A005132", "Recaman",            "recursive",      "a(n)=a(n-1)±n (subtractive when possible)", recaman),
]

# Bare-DB sequences materialized from existing tables/views
DERIVED_SEQUENCES = [
    ("A000040", "Primes",              "multiplicative", "p prime",
     "SELECT p, discovered_order FROM primes WHERE p <= %s ORDER BY p"),
    ("A001358", "Semiprimes",          "multiplicative", "Ω(n)=2",
     "SELECT n, ROW_NUMBER() OVER (ORDER BY n) FROM integers WHERE big_omega = 2 AND n <= %s ORDER BY n"),
    ("A014612", "3-almost primes",     "multiplicative", "Ω(n)=3",
     "SELECT n, ROW_NUMBER() OVER (ORDER BY n) FROM integers WHERE big_omega = 3 AND n <= %s ORDER BY n"),
    ("A005117", "Squarefree",          "multiplicative", "μ²(n)=1",
     "SELECT n, ROW_NUMBER() OVER (ORDER BY n) FROM integers WHERE is_squarefree AND n <= %s ORDER BY n"),
    ("A002808", "Composites",          "multiplicative", "n composite",
     "SELECT n, ROW_NUMBER() OVER (ORDER BY n) FROM integers WHERE NOT is_prime AND n > 1 AND n <= %s ORDER BY n"),
    ("A000396", "Perfect numbers",     "multiplicative", "σ(n)=2n",
     "SELECT n, ROW_NUMBER() OVER (ORDER BY n) FROM perfect_numbers WHERE n <= %s ORDER BY n"),
    ("A005101", "Abundant numbers",    "multiplicative", "σ(n)>2n",
     "SELECT n, ROW_NUMBER() OVER (ORDER BY n) FROM abundant_numbers WHERE n <= %s ORDER BY n"),
    ("A002182", "Highly composite",    "multiplicative", "τ(n) > τ(k) ∀ k<n",
     """WITH t AS (SELECT * FROM divisor_count WHERE n <= %s)
        SELECT t1.n, ROW_NUMBER() OVER (ORDER BY t1.n)
        FROM t t1
        WHERE NOT EXISTS (SELECT 1 FROM t t2 WHERE t2.n < t1.n AND t2.tau >= t1.tau)
        ORDER BY t1.n"""),
    ("A001359", "Lower twin primes",   "multiplicative", "p prime, p+2 prime",
     "SELECT p1.p, ROW_NUMBER() OVER (ORDER BY p1.p) FROM primes p1 JOIN primes p2 ON p2.p = p1.p + 2 WHERE p1.p <= %s ORDER BY p1.p"),
    ("A006881", "Squarefree semiprimes","multiplicative", "ω(n)=2 ∧ μ²(n)=1",
     "SELECT n, ROW_NUMBER() OVER (ORDER BY n) FROM integers WHERE omega=2 AND big_omega=2 AND n <= %s ORDER BY n"),
]

COLLATZ_SEEDS = [27, 97, 871, 6171]
ALIQUOT_SEEDS = [6, 28, 220, 12496, 138, 276]


# ─── Driver ──────────────────────────────────────────────────────────────────

def main():
    dsn = os.environ.get(
        "CALX_DSN",
        "postgresql://trunk:trunk@localhost:5434/trunk",
    )
    with db.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(n) FROM integers")
            lim = cur.fetchone()[0]
            assert lim, "no integers populated — run `calx generate` first"
            print(f"populating sequences over [1..{lim}]")

            for seq_id, name, kind, formula, gen in NAMED_SEQUENCES:
                upsert_sequence(cur, seq_id, name, kind, formula)
                cnt = install_members(cur, seq_id, gen(), lim)
                print(f"  {seq_id:8s}  {name:24s}  {cnt:>6} members")

            for seq_id, name, kind, formula, sql in DERIVED_SEQUENCES:
                upsert_sequence(cur, seq_id, name, kind, formula)
                cnt = install_from_query(cur, seq_id, sql, (lim,))
                print(f"  {seq_id:8s}  {name:24s}  {cnt:>6} members")

            cur.execute("SELECT n, sigma FROM divisor_sum")
            sigma_lookup = dict(cur.fetchall())

            for seed in COLLATZ_SEEDS:
                sid = f"collatz_{seed}"
                upsert_sequence(cur, sid, f"Collatz orbit from {seed}", "recursive",
                                f"3n+1 / n/2, starting at {seed}")
                cnt = install_members(cur, sid, collatz(seed), lim)
                print(f"  {sid:14s}  Collatz orbit            {cnt:>6} members")

            for seed in ALIQUOT_SEEDS:
                sid = f"aliquot_{seed}"
                upsert_sequence(cur, sid, f"Aliquot trajectory from {seed}", "recursive",
                                f"s(n)=σ(n)-n, starting at {seed}")
                cnt = install_members(
                    cur, sid, aliquot_orbit(seed, sigma_lookup, lim), lim
                )
                print(f"  {sid:14s}  Aliquot trajectory       {cnt:>6} members")

        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM sequences; "
            )
            (nseq,) = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM sequence_membership")
            (nmem,) = cur.fetchone()
            print(f"\ndone: {nseq} sequences, {nmem} memberships")


if __name__ == "__main__":
    main()
