#!/usr/bin/env python3
"""External proof-checker artifact: difference-tower H1 signatures of OEIS classics.

Independently re-derives the gap-pattern-homology signature analysis and
asserts two things:

  (1) the measured H1 signature vector [H1(d^0), H1(d^1), H1(d^2)] of nine
      curated OEIS classics, exactly; and

  (2) the headline structural finding -- Catalan (A000108), Bell (A000110)
      and Motzkin (A001006) form a CROSS-FAMILY depth-equivalence class
      (identical signature [0,0,0]) although their term prefixes diverge
      immediately, i.e. a similarity the original prefix/blowup OEIS scan
      structurally cannot detect.

Self-contained: term generators AND the Erdos gap-pattern construction are
vendored here; the trust root is THIS file + its sha256, independent of the
calx database and the live engine. numpy only. Exit 0 iff both hold.
"""

from __future__ import annotations

import sys

import numpy as np

N_TERMS = 60
MAX_ORDER = 2

EXPECTED = {
    "A000040": [40, 0, 2],   # primes
    "A000041": [14, 23, 23], # partitions
    "A000045": [56, 54, 52], # Fibonacci
    "A000108": [0, 0, 0],    # Catalan
    "A000110": [0, 0, 0],    # Bell
    "A000217": [72, 0, 0],   # triangular
    "A000290": [34, 0, 0],   # squares
    "A000578": [5, 70, 0],   # cubes
    "A001006": [0, 0, 0],    # Motzkin
}
# The cross-family depth-equivalence class the prefix scan cannot see.
DEPTH_CLASS = {"A000108", "A000110", "A001006"}


# ---- vendored term generators ----------------------------------------------

def primes(k):
    out, c = [], 2
    while len(out) < k:
        if all(c % p for p in out if p * p <= c):
            out.append(c)
        c += 1
    return out


def fibonacci(k):
    a, b, o = 1, 1, []
    for _ in range(k):
        o.append(a); a, b = b, a + b
    return o


def catalan(k):
    o, c = [], 1
    for n in range(k):
        o.append(c); c = c * 2 * (2 * n + 1) // (n + 2)
    return o


def bell(k):
    row, o = [1], [1]
    for _ in range(k - 1):
        nxt = [row[-1]]
        for x in row:
            nxt.append(nxt[-1] + x)
        row = nxt; o.append(row[0])
    return o


def motzkin(k):
    m = [1, 1]
    for n in range(2, k):
        m.append((m[-1] * (2 * n + 1) + m[-2] * (3 * n - 3)) // (n + 2))
    return m[:k]


def partitions(k):
    p = [1] + [0] * (k + 1)
    for i in range(1, k + 1):
        for j in range(i, k + 1):
            p[j] += p[j - i]
    return [p[i] for i in range(k)]


def triangular(k):
    return [n * (n + 1) // 2 for n in range(1, k + 1)]


def squares(k):
    return [n * n for n in range(1, k + 1)]


def cubes(k):
    return [n ** 3 for n in range(1, k + 1)]


GEN = {
    "A000040": primes, "A000041": partitions, "A000045": fibonacci,
    "A000108": catalan, "A000110": bell, "A000217": triangular,
    "A000290": squares, "A000578": cubes, "A001006": motzkin,
}


# ---- vendored Erdos gap-pattern complex (faithful) -------------------------

def build_complex(A):
    verts = sorted(set(A))
    vset = set(verts)
    su = sorted(set(A))
    gaps = {su[i + 1] - su[i] for i in range(len(su) - 1)}
    edges = []
    for i in verts:
        for g in gaps:
            if i + g in vset:
                edges.append((i, i + g, g))
    eidx = {e: k for k, e in enumerate(edges)}
    squares = []
    sg = sorted(gaps)
    for a in range(len(sg)):
        for b in range(a + 1, len(sg)):
            for gh, gv in ((sg[a], sg[b]), (sg[b], sg[a])):
                for i in verts:
                    if all(v in vset for v in (i, i + gh, i + gv, i + gh + gv)):
                        squares.append((i, i + gh, i + gv, i + gh + gv, gh, gv))
    C0, C1, C2 = len(verts), len(edges), len(squares)
    vidx = {v: k for k, v in enumerate(verts)}
    d1 = np.zeros((C0, C1), dtype=int)
    for col, (s, t, _g) in enumerate(edges):
        d1[vidx[s], col] = -1
        d1[vidx[t], col] = 1
    d2 = np.zeros((C1, C2), dtype=int)
    for col, (i, j, k, ell, gh, gv) in enumerate(squares):
        for e, sgn in (((i, j, gh), 1), ((k, ell, gh), -1),
                       ((j, ell, gv), 1), ((i, k, gv), -1)):
            if e in eidx:
                d2[eidx[e], col] += sgn
    return C0, C1, C2, d1, d2


def h1(A) -> int:
    if len(set(A)) < 2:
        return 0
    C0, C1, C2, d1, d2 = build_complex(A)
    if C1 == 0:
        return 0
    r1 = int(np.linalg.matrix_rank(d1))
    r2 = int(np.linalg.matrix_rank(d2)) if C2 > 0 else 0
    return max(0, (C1 - r1) - r2)


def signature(terms) -> list[int]:
    sig, cur = [], list(terms)
    for order in range(MAX_ORDER + 1):
        sig.append(h1(cur))
        if len(cur) < 2:
            break
        cur = [cur[i + 1] - cur[i] for i in range(len(cur) - 1)]
    while len(sig) <= MAX_ORDER:
        sig.append(0)
    return sig


def main() -> int:
    ok = True
    print("seq      computed        expected        match")
    measured = {}
    for sid, gen in GEN.items():
        sig = signature(gen(N_TERMS))
        measured[sid] = sig
        exp = EXPECTED[sid]
        m = sig == exp
        ok = ok and m
        print(f"{sid}  {str(sig):<15s} {str(exp):<15s} {'OK' if m else 'FAIL'}")

    cls = {tuple(measured[s]) for s in DEPTH_CLASS}
    cross_family = (len(cls) == 1 and next(iter(cls)) == (0, 0, 0))
    print()
    print(f"cross-family depth class {sorted(DEPTH_CLASS)} all == [0,0,0]: "
          f"{cross_family}")

    if ok and cross_family:
        print("VERIFIED: signature tower reproduced; Catalan/Bell/Motzkin form "
              "a prefix-invisible depth-equivalence class.")
        return 0
    print("REFUTED: signatures or the cross-family finding diverged.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
