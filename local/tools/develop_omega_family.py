"""Develop a FAMILY with explicit small-omega / big-Omega relations to a
generative set.

Generative set  : the system-developed Aliquot-Recaman kernel (Z000001's
                  dynamics -- Recaman with jump sigma(n)-n; algorithmic, not
                  algebraic). We walk it far enough to populate the strata.

Family          : for each relation r in
                    omega(n) == 1, 2, 3            (small-omega strata)
                    Omega(n) == 2, 3, 4            (big-Omega strata)
                  member F_r is the subsequence of the kernel walk whose
                  terms satisfy r. Same dynamical generator, different
                  (omega,Omega) relation -> an algorithmically generated
                  family, each member tied to the generative set by an
                  omega/Omega relation.

Only fully-factored terms (within the bounded factorizer budget) are emitted
so every member's relation is exactly well-defined. Deterministic.
Registered into calx.sequences (family 'omega-relation') + kan.sequence_terms.
"""

from __future__ import annotations

import os
import sys

import psycopg

PG_DSN = os.environ.get(
    "CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk"
)
PER_MEMBER = 60          # default target terms per family member
MAX_STEPS = 60_000       # generative-walk budget (aliquot / succ kernels)
VALUE_CAP = 2_000_000    # default value cap (aliquot / succ kernels; keeps factoring fast)
FIB_MAX_STEPS = 1_500    # max Fibonacci index to check (F(1500) ≈ 10^313)

# (member_suffix, label, predicate on (omega, bigomega))
RELATIONS = [
    ("W1", "omega=1 (prime powers)",      lambda w, W: w == 1),
    ("W2", "omega=2",                     lambda w, W: w == 2),
    ("W3", "omega=3",                     lambda w, W: w == 3),
    ("B2", "Omega=2 (2 prime factors)",   lambda w, W: W == 2),
    ("B3", "Omega=3",                     lambda w, W: W == 3),
    ("B4", "Omega=4",                     lambda w, W: W == 4),
]


def aliquot(n: int) -> int:
    """sigma(n) - n, via factorization of the index (fast for n up to ~1e5)."""
    if n <= 1:
        return 0
    sigma, m, p = 1, n, 2
    while p * p <= m:
        if m % p == 0:
            pk, s = 1, 1
            while m % p == 0:
                pk *= p
                s += pk
                m //= p
            sigma *= s
        p += 1 if p == 2 else 2
    if m > 1:
        sigma *= (1 + m)
    return sigma - n


def omega_bigomega(t: int):
    """(omega, bigomega, ok) by exact trial division; ok=False if a composite
    cofactor remains above the budget (then the term is not emitted)."""
    if t <= 1:
        return 0, 0, True
    w = W = 0
    m, d = t, 2
    while d * d <= m and d <= 100_000:
        if m % d == 0:
            w += 1
            while m % d == 0:
                W += 1
                m //= d
        d += 1 if d == 2 else 2
    if m == 1:
        return w, W, True
    # residual is a single prime (since no factor <= sqrt remained)
    return w + 1, W + 1, True


def generative_walk(max_steps: int):
    """Yield the Aliquot-Recaman kernel trajectory (the generative set)."""
    a, seen = 0, {0}
    yield a
    for n in range(1, max_steps):
        g = max(1, aliquot(n))
        cand = a - g
        a = cand if (cand > 0 and cand not in seen) else a + g
        seen.add(a)
        yield a


def succ_kernel(max_steps: int):
    """Yield the successor kernel 1,2,3,... (the canonical generative set)."""
    for n in range(1, max_steps):
        yield n


def fibonacci_kernel(max_n: int):
    """Yield F(1), F(2), ..., F(max_n) (standard 1,1,2,3,5,8,... indexing)."""
    a, b = 1, 1
    for _ in range(max_n):
        yield a
        a, b = b, a + b


# ---------------------------------------------------------------------------
# Miller-Rabin primality test — deterministic for n < 3.3e24 using the 12
# witnesses below; strong-probabilistic (no known false positives) for larger n.
# ---------------------------------------------------------------------------
_MR_WITNESSES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)
_TRIAL_LIMIT = 100_000

# Precomputed sieve of primes up to _TRIAL_LIMIT — trial-divide only by primes
# (9,592 primes < 100,000 vs. ~50,000 odd numbers; ~5x speedup for large F(n)).
def _sieve(limit: int) -> list[int]:
    is_prime = bytearray([1]) * (limit + 1)
    is_prime[0] = is_prime[1] = 0
    for i in range(2, int(limit**0.5) + 1):
        if is_prime[i]:
            is_prime[i*i::i] = bytearray(len(is_prime[i*i::i]))
    return [i for i, v in enumerate(is_prime) if v]

_SMALL_PRIMES = _sieve(_TRIAL_LIMIT)


def _is_prime_mr(n: int) -> bool:
    """Miller-Rabin primality test."""
    if n < 2:
        return False
    if n in _MR_WITNESSES:
        return True
    if any(n % p == 0 for p in _MR_WITNESSES):
        return False
    r, d = 0, n - 1
    while not d & 1:
        r += 1
        d >>= 1
    for a in _MR_WITNESSES:
        x = pow(a, d, n)
        if x == 1 or x == n - 1:
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def omega_bigomega_fib(t: int):
    """(omega, bigomega, ok) for arbitrary-sized t.

    Uses trial division up to _TRIAL_LIMIT, then Miller-Rabin to determine
    whether the residual is prime.  ok=False means the residual is a known
    composite whose full factorisation is unknown (very rare for Fibonacci;
    the term is skipped by the caller).
    """
    if t <= 1:
        return 0, 0, True
    w = W = 0
    m = t
    for p in _SMALL_PRIMES:
        if p * p > m:
            break
        if m % p == 0:
            w += 1
            while m % p == 0:
                W += 1
                m //= p
    if m == 1:
        return w, W, True
    # Residual m has no prime factor <= min(_TRIAL_LIMIT, sqrt(m)).
    if m <= _TRIAL_LIMIT * _TRIAL_LIMIT or _is_prime_mr(m):
        # Either m < 10^10 (all factors found) or Miller-Rabin confirms prime.
        return w + 1, W + 1, True
    # m is composite with factors > _TRIAL_LIMIT — can't determine exact counts.
    return w, W, False


# kernel name -> (member-id prefix, generator, label suffix, val_cap, max_steps)
# val_cap=None disables the value cap (required for Fibonacci).
KERNELS = {
    "aliquot": ("Z", generative_walk,  "Z000001-kernel",       VALUE_CAP, MAX_STEPS),
    "succ":    ("N", succ_kernel,      "naturals (succ kernel)", VALUE_CAP, MAX_STEPS),
    "fib":     ("F", fibonacci_kernel, "Fibonacci",              None,      FIB_MAX_STEPS),
}


def main() -> int:
    kernel = sys.argv[1] if len(sys.argv) > 1 else "aliquot"
    if kernel not in KERNELS:
        print(f"usage: develop_omega_family.py [{'|'.join(KERNELS)}] [per_member]")
        return 2
    per_member = int(sys.argv[2]) if len(sys.argv) > 2 else PER_MEMBER
    prefix, walk, ksuffix, val_cap, kmax_steps = KERNELS[kernel]
    factoriser = omega_bigomega_fib if val_cap is None else omega_bigomega
    members = {prefix + s: [] for s, _, _ in RELATIONS}
    done = set()
    steps = 0
    for v in walk(kmax_steps):
        steps += 1
        if v <= 1:
            continue
        if val_cap is not None and v > val_cap:
            continue
        w, W, ok = factoriser(v)
        if not ok:
            continue
        for suf, _, pred in RELATIONS:
            mid = prefix + suf
            if len(members[mid]) < per_member and pred(w, W):
                members[mid].append(v)
                if len(members[mid]) == per_member:
                    done.add(mid)
        if len(done) == len(RELATIONS):
            break

    print(f"[{kernel}] generative walk: {steps} steps consumed\n")
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        for suf, label, _ in RELATIONS:
            mid = prefix + suf
            terms = members[mid]
            cur.execute(
                "INSERT INTO calx.sequences (seq_id,name,seq_type,family) "
                "VALUES (%s,%s,'dynamical','omega-relation') "
                "ON CONFLICT (seq_id) DO UPDATE SET name=EXCLUDED.name",
                (mid, f"OmegaFamily {label} of {ksuffix}"),
            )
            cur.execute("DELETE FROM kan.sequence_terms WHERE seq_id=%s", (mid,))
            for idx, val in enumerate(terms, start=1):
                cur.execute(
                    "INSERT INTO kan.sequence_terms (seq_id,idx,term) "
                    "VALUES (%s,%s,%s)",
                    (mid, idx, int(val)),
                )
            found, target = len(terms), per_member
            shortage = f"  WARNING: only {found}/{target} terms found" if found < target else ""
            print(f"  {mid}  {label:<26s} n={found:<3d} "
                  f"head={terms[:8]}{shortage}")
        conn.commit()
    print(f"\nregistered {len(RELATIONS)}-member omega-relation family "
          f"into the unified model")
    return 0


if __name__ == "__main__":
    sys.exit(main())
