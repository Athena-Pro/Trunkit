"""Seed curated OEIS classics into the unified model.

calx.sequences      <- catalog metadata (seq_id, name, type, family)
kan.sequence_terms  <- the first N terms (NUMERIC)

NOT calx.sequence_membership: its n -> calx.integers(n) FK only admits
integers present in the calx integer DB (here 1..100), so it cannot hold
large OEIS terms (Bell/Catalan/Fibonacci). kan is the analysis substrate;
calx.sequences stays the catalog. Idempotent.
"""

from __future__ import annotations

import os
import sys

import psycopg

PG_DSN = os.environ.get(
    "CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk"
)

N_TERMS = 60


def primes(k: int) -> list[int]:
    out, c = [], 2
    while len(out) < k:
        if all(c % p for p in out if p * p <= c):
            out.append(c)
        c += 1
    return out


def fibonacci(k: int) -> list[int]:
    a, b, out = 1, 1, []
    for _ in range(k):
        out.append(a)
        a, b = b, a + b
    return out


def catalan(k: int) -> list[int]:
    out, c = [], 1
    for n in range(k):
        out.append(c)
        c = c * 2 * (2 * n + 1) // (n + 2)
    return out


def bell(k: int) -> list[int]:
    row = [1]
    out = [1]
    for _ in range(k - 1):
        nxt = [row[-1]]
        for x in row:
            nxt.append(nxt[-1] + x)
        row = nxt
        out.append(row[0])
    return out


def motzkin(k: int) -> list[int]:
    m = [1, 1]
    for n in range(2, k):
        m.append((m[-1] * (2 * n + 1) + m[-2] * (3 * n - 3)) // (n + 2))
    return m[:k]


def partitions(k: int) -> list[int]:
    p = [1] + [0] * (k + 1)
    for i in range(1, k + 1):
        for j in range(i, k + 1):
            p[j] += p[j - i]
    return [p[i] for i in range(k)]


def triangular(k: int) -> list[int]:
    return [n * (n + 1) // 2 for n in range(1, k + 1)]


def squares(k: int) -> list[int]:
    return [n * n for n in range(1, k + 1)]


def cubes(k: int) -> list[int]:
    return [n ** 3 for n in range(1, k + 1)]


# --- expanded corpus: collision-prone families --------------------------------

def naturals(k):
    return list(range(1, k + 1))


def evens(k):
    return [2 * n for n in range(1, k + 1)]


def pow2(k):
    return [2 ** n for n in range(k)]


def pow3(k):
    return [3 ** n for n in range(k)]


def pow4(k):
    return [4 ** n for n in range(k)]


def factorials(k):
    o, f = [], 1
    for n in range(1, k + 1):
        f *= n
        o.append(f)
    return o


def lucas(k):
    a, b, o = 2, 1, []
    for _ in range(k):
        o.append(a); a, b = b, a + b
    return o


def pell(k):
    a, b, o = 1, 2, []
    for _ in range(k):
        o.append(a); a, b = b, 2 * b + a
    return o


def jacobsthal(k):
    a, b, o = 1, 1, []
    for _ in range(k):
        o.append(a); a, b = b, b + 2 * a
    return o


def pentagonal(k):
    return [n * (3 * n - 1) // 2 for n in range(1, k + 1)]


def primorial(k):
    o, prod, c = [], 1, 2
    while len(o) < k:
        if all(c % p for p in range(2, int(c ** 0.5) + 1)):
            prod *= c
            o.append(prod)
        c += 1
    return o


def sigma(k):
    return [sum(d for d in range(1, n + 1) if n % d == 0)
            for n in range(1, k + 1)]


def tau(k):
    return [sum(1 for d in range(1, n + 1) if n % d == 0)
            for n in range(1, k + 1)]


def totient(k):
    def phi(n):
        r, m, p = n, n, 2
        while p * p <= m:
            if m % p == 0:
                while m % p == 0:
                    m //= p
                r -= r // p
            p += 1
        if m > 1:
            r -= r // m
        return r
    return [phi(n) for n in range(1, k + 1)]


# (seq_id, name, seq_type, family, generator)
CLASSICS = [
    ("A000040", "Prime numbers",        "number-theoretic", "primes",      primes),
    ("A000045", "Fibonacci numbers",    "linear-recurrence", "recurrence",  fibonacci),
    ("A000108", "Catalan numbers",      "combinatorial",     "catalan",     catalan),
    ("A000110", "Bell numbers",         "combinatorial",     "set-partition", bell),
    ("A001006", "Motzkin numbers",      "combinatorial",     "lattice-path", motzkin),
    ("A000041", "Partition numbers",    "combinatorial",     "partition",   partitions),
    ("A000217", "Triangular numbers",   "polynomial",        "polygonal",   triangular),
    ("A000290", "Squares",              "polynomial",        "power",       squares),
    ("A000578", "Cubes",                "polynomial",        "power",       cubes),
    # --- expanded corpus ---
    ("A000027", "Natural numbers",      "polynomial",        "linear",      naturals),
    ("A005843", "Even numbers",         "polynomial",        "linear",      evens),
    ("A000079", "Powers of 2",          "exponential",       "power",       pow2),
    ("A000244", "Powers of 3",          "exponential",       "power",       pow3),
    ("A000302", "Powers of 4",          "exponential",       "power",       pow4),
    ("A000142", "Factorials",           "combinatorial",     "factorial",   factorials),
    ("A000032", "Lucas numbers",        "linear-recurrence", "recurrence",  lucas),
    ("A000129", "Pell numbers",         "linear-recurrence", "recurrence",  pell),
    ("A001045", "Jacobsthal numbers",   "linear-recurrence", "recurrence",  jacobsthal),
    ("A000326", "Pentagonal numbers",   "polynomial",        "polygonal",   pentagonal),
    ("A002110", "Primorials",           "number-theoretic",  "primorial",   primorial),
    ("A000203", "Sigma (sum of divisors)", "number-theoretic", "arithmetic", sigma),
    ("A000005", "Tau (number of divisors)", "number-theoretic", "arithmetic", tau),
    ("A000010", "Euler totient",        "number-theoretic",  "arithmetic",  totient),
]


def main() -> int:
    with psycopg.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            for seq_id, name, seq_type, family, gen in CLASSICS:
                terms = gen(N_TERMS)
                cur.execute(
                    "INSERT INTO calx.sequences (seq_id, name, seq_type, family) "
                    "VALUES (%s,%s,%s,%s) ON CONFLICT (seq_id) DO NOTHING",
                    (seq_id, name, seq_type, family),
                )
                for idx, val in enumerate(terms, start=1):
                    cur.execute(
                        "INSERT INTO kan.sequence_terms (seq_id, idx, term) "
                        "VALUES (%s,%s,%s) ON CONFLICT (seq_id, idx) DO NOTHING",
                        (seq_id, idx, int(val)),
                    )
                print(f"  [OK] {seq_id} {name:<22s} {len(terms)} terms "
                      f"(head={terms[:6]})")
        conn.commit()

    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM calx.sequences")
        ns = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM kan.sequence_terms")
        nm = cur.fetchone()[0]
    print(f"\nunified OEIS layer: {ns} calx.sequences, {nm} kan.sequence_terms rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
