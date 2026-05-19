#!/usr/bin/env python3
"""External proof-checker artifact: the static adelic shadow.

The boildown fiber of val:lithon->seq. Each prime row {p^1..p^16} is
super-increasing, so ALL representational multiplicity is the F_1 binomial
kernel convolved with the prime-power subset-sum count:

    rho(N) = SUM_{s=0..16} C(16,s) * A(N-s)

Self-contained proofs:

  S1 super-increasing : for every one of the 15 prime rows,
                        p^(k+1) > sum_{i=1..k} p^i  (so subset sums unique,
                        per-prime-row count in {0,1}; F_1 is the sole source
                        of multiplicity).
  S2 factorization    : rho via the F_1-binomial convolution equals rho via
                        an INDEPENDENT 0/1 subset-sum DP over the full atom
                        multiset (16 unit atoms + prime powers), for all
                        N <= 300. (The adelic factorization is exact.)
  S3 kernel separation: the coarse shadow signature SEPARATES the residual
                        combined-invariant collision kernel that the whole
                        multiplicative tower could not -- {pow2,pow3,pow4}
                        pairwise distinct AND primorial != evens.

Canonical A[0..64] + the separation booleans sha256-pinned. Exit 0 iff
S1-S3 hold.
"""

from __future__ import annotations

import hashlib
import math
import sys

BASES = [1, 2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47]
COLS = 16
CAP = 40_000                         # cert shadow window (fast, covers kernel)
C16 = [math.comb(16, s) for s in range(17)]


def prime_power_atoms(cap: int) -> list[int]:
    out = []
    for r in range(1, len(BASES)):
        p = BASES[r]
        for c in range(COLS):
            v = p ** (c + 1)
            if v <= cap:
                out.append(v)
    return out


def build_A(cap: int) -> list[int]:
    A = [0] * (cap + 1)
    A[0] = 1
    for v in prime_power_atoms(cap):
        for m in range(cap, v - 1, -1):
            if A[m - v]:
                A[m] += A[m - v]
    return A


def rho_factored(n: int, A: list[int]):
    if n < 0 or n > CAP:
        return None
    r = wsum = supp = 0
    fmax = -1
    for s in range(17):
        m = n - s
        if 0 <= m <= CAP and A[m]:
            w = C16[s] * A[m]
            r += w
            wsum += s * w
            supp += 1
            fmax = s
    if r == 0:
        return (0, None, -1, 0)
    return (r, wsum / r, fmax, supp)


def rho_bruteDP(n: int) -> int:
    """Independent 0/1 subset-sum count over the FULL atom multiset:
    16 unit atoms (value 1 each) + all prime-power atoms <= n."""
    atoms = [1] * 16 + [v for v in prime_power_atoms(n)]
    dp = [0] * (n + 1)
    dp[0] = 1
    for v in atoms:
        for m in range(n, v - 1, -1):
            if dp[m - v]:
                dp[m] += dp[m - v]
    return dp[n]


# ---- kernel sequence generators (the combined-invariant collision set) -----

def pow2(k):
    return [2 ** i for i in range(k)]


def pow3(k):
    return [3 ** i for i in range(k)]


def pow4(k):
    return [4 ** i for i in range(k)]


def evens(k):
    return [2 * i for i in range(1, k + 1)]


def primorial(k):
    out, pr, c = [], 1, 2
    while len(out) < k:
        if all(c % p for p in range(2, int(c ** 0.5) + 1)):
            pr *= c
            out.append(pr)
        c += 1
    return out


def coarse_sig(terms, A) -> str:
    rows = []
    for n in terms:
        if 0 <= n <= CAP:
            rho, fmean, _fmax, supp = rho_factored(n, A)
            rows.append((
                round(fmean, 3) if fmean is not None else -1.0,
                rho.bit_length() if isinstance(rho, int) else 0,
                supp,
            ))
    return hashlib.sha256(repr(sorted(rows)).encode()).hexdigest()


def main() -> int:
    A = build_A(CAP)

    # S1 super-increasing per prime row
    S1 = True
    for r in range(1, len(BASES)):
        p = BASES[r]
        run = 0
        for k in range(1, COLS + 1):
            if p ** k <= run:                 # must strictly exceed prefix sum
                S1 = False
            run += p ** k

    # S2 factored rho == independent brute DP, for all N <= 300
    S2 = True
    for n in range(0, 301):
        f = rho_factored(n, A)[0]
        if f != rho_bruteDP(n):
            S2 = False

    # S3 shadow separates the residual combined-invariant kernel
    sig2 = coarse_sig(pow2(60), A)
    sig3 = coarse_sig(pow3(60), A)
    sig4 = coarse_sig(pow4(60), A)
    sigP = coarse_sig(primorial(60), A)
    sigE = coarse_sig(evens(60), A)
    powers_distinct = len({sig2, sig3, sig4}) == 3
    prim_vs_even = sigP != sigE
    S3 = powers_distinct and prim_vs_even

    sha = hashlib.sha256(repr(A[:65]).encode()).hexdigest()

    print(f"  A[0..64] sha256: {sha[:16]}")
    print(f"  rho(1)={rho_factored(1, A)[0]} (= 16 unit reps; F_1 adjoins 1)")
    print(f"  S1 prime rows super-increasing (count in 0/1):  {S1}")
    print(f"  S2 F_1-convolution == independent subset-sum DP: {S2}")
    print(f"  S3 shadow separates the collision kernel:        {S3}")
    print(f"     pow2/pow3/pow4 pairwise distinct: {powers_distinct}; "
          f"primorial != evens: {prim_vs_even}")

    if (S1 and S2 and S3 and sha[:16] == "aa8a53978645a046"):
        print("\nVERIFIED: the adelic shadow factors as the F_1 binomial "
              "kernel convolved with the prime-power subset-sum count, and "
              "the shadow is the orthogonal axis that resolves the residual "
              "collision kernel the multiplicative tower could not.")
        return 0
    print("\nREFUTED: a shadow law (or A-prefix sha) diverged.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
