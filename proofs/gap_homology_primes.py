#!/usr/bin/env python3
"""External proof-checker artifact: prime gap-pattern H1 growth.

Independently re-derives the central quantitative claim of the "Gap Pattern
Homology" project (CPP 2027):

    The simplicial complex built from the gap set of the primes has
    H1 rank  >  0  and STRICTLY GROWS with N, while the chain-complex
    property d1 . d2 = 0 holds at every scale.

Self-contained by design: the construction is *vendored* here (frozen copy),
so the trust root is THIS file + its sha256 -- independent of both the calx
database and the live Erdos codebase. numpy is the only dependency.

Asserted, measured values (the paper's own numbers, captured 2026-05-16):

    N      V     E    Sq   H1
    50     15    22   10    3
    100    25    41   18    8
    200    46   102   54   30
    500    95   227  152   59
    1000  168   478  396  128

Exit 0 iff every (V,E,Sq,H1) matches, H1 is strictly increasing, and
d1 . d2 = 0 at every N. Nonzero otherwise.
"""

from __future__ import annotations

import sys

import numpy as np

SCHEDULE = [50, 100, 200, 500, 1000]
# (N): (V, E, Sq, H1) -- the paper's measured invariants.
EXPECTED = {
    50:   (15, 22, 10, 3),
    100:  (25, 41, 18, 8),
    200:  (46, 102, 54, 30),
    500:  (95, 227, 152, 59),
    1000: (168, 478, 396, 128),
}


def sieve(limit: int) -> list[int]:
    if limit < 2:
        return []
    s = [True] * (limit + 1)
    s[0] = s[1] = False
    for i in range(2, int(limit ** 0.5) + 1):
        if s[i]:
            for j in range(i * i, limit + 1, i):
                s[j] = False
    return [i for i in range(limit + 1) if s[i]]


def gap_set(A: list[int]) -> set[int]:
    A = sorted(set(A))
    return {A[i + 1] - A[i] for i in range(len(A) - 1)}


def build_complex(A: list[int], N: int):
    """Vendored faithful copy of build_gap_pattern_complex (2-cell version)."""
    verts = sorted(set(A))
    vset = set(verts)
    gaps = gap_set(A)

    # 1-cells: (i, j=i+g) with both endpoints in A and j <= N
    edges = []
    for i in verts:
        for g in gaps:
            j = i + g
            if j in vset and j <= N:
                edges.append((i, j, g))
    edge_idx = {e: k for k, e in enumerate(edges)}

    # 2-cells: commuting squares over ordered gap pairs (both orderings)
    squares = []
    sgaps = sorted(gaps)
    for a in range(len(sgaps)):
        for b in range(a + 1, len(sgaps)):
            for gh, gv in ((sgaps[a], sgaps[b]), (sgaps[b], sgaps[a])):
                for i in verts:
                    j, k, ell = i + gh, i + gv, i + gh + gv
                    if all(v in vset for v in (i, j, k, ell)):
                        squares.append((i, j, k, ell, gh, gv))

    C0, C1, C2 = len(verts), len(edges), len(squares)
    vidx = {v: k for k, v in enumerate(verts)}

    d1 = np.zeros((C0, C1), dtype=int)
    for col, (s, t, _g) in enumerate(edges):
        d1[vidx[s], col] = -1
        d1[vidx[t], col] = +1

    d2 = np.zeros((C1, C2), dtype=int)
    for col, (i, j, k, ell, gh, gv) in enumerate(squares):
        bottom = (i, j, gh)
        top = (k, ell, gh)
        right = (j, ell, gv)
        left = (i, k, gv)
        if bottom in edge_idx:
            d2[edge_idx[bottom], col] += 1
        if top in edge_idx:
            d2[edge_idx[top], col] += -1
        if right in edge_idx:
            d2[edge_idx[right], col] += 1
        if left in edge_idx:
            d2[edge_idx[left], col] += -1

    return C0, C1, C2, d1, d2


def h1_rank(C1: int, d1: np.ndarray, d2: np.ndarray, C2: int) -> int:
    if C1 == 0:
        return 0
    rank_d1 = int(np.linalg.matrix_rank(d1))
    rank_d2 = int(np.linalg.matrix_rank(d2)) if C2 > 0 else 0
    return max(0, (C1 - rank_d1) - rank_d2)


def main() -> int:
    ok = True
    prev_h1 = -1
    print("N      V     E    Sq   H1   d1.d2=0  match")
    for N in SCHEDULE:
        primes = sieve(N)
        C0, C1, C2, d1, d2 = build_complex(primes, N)
        h1 = h1_rank(C1, d1, d2, C2)
        comp_zero = (C2 == 0) or np.allclose(d1 @ d2, 0)
        exp = EXPECTED[N]
        got = (C0, C1, C2, h1)
        matches = (got == exp) and comp_zero
        growing = h1 > prev_h1
        prev_h1 = h1
        ok = ok and matches and growing
        print(f"{N:<5d} {C0:<5d} {C1:<5d} {C2:<4d} {h1:<4d} {str(comp_zero):<8s} "
              f"{'OK' if matches and growing else 'FAIL'}  exp={exp}")

    print()
    if ok:
        print("VERIFIED: prime gap-pattern H1 strictly grows "
              "(3->8->30->59->128); d1.d2=0 at every scale.")
        return 0
    print("REFUTED: measured invariants diverged from the asserted claim.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
