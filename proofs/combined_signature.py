#!/usr/bin/env python3
"""External proof-checker artifact: the combined (difference (+) factorial) signature.

Independently re-derives BOTH homology lenses for nine OEIS classics and forms
the 7-vector [H1(d^0),H1(d^1),H1(d^2), H1(parity),H1(omega),H1(bigomega),
H1(shared_prime)], then asserts:

  (1) the measured combined 7-vector of every sequence, exactly;
  (2) Law A -- the combined signature is a COMPLETE invariant on this corpus
      (all nine pairwise distinct);
  (3) Law B -- Catalan/Bell/Motzkin (identical under the difference lens) are
      combined-distinct: the factorial lens resolves that class;
  (4) Law C -- squares/cubes (identical under the factorial lens) are
      combined-distinct: the difference lens resolves that class.

Self-contained: term generators, the Erdos gap-pattern complex, the difference
tower, a bounded factorizer, and the shared-prime graph are all vendored here.
numpy only. Exit 0 iff (1)-(4) hold.
"""

from __future__ import annotations

import itertools
import sys

import numpy as np

N_TERMS = 60
TRIAL_LIMIT = 100_000
MAX_ORDER = 2

# [d0,d1,d2, parity,omega,bigomega,shared_prime]
EXPECTED = {
    "A000040": [40, 0, 2, 0, 0, 0, 0],
    "A000041": [14, 23, 23, 0, 0, 0, 1067],
    "A000045": [56, 54, 52, 0, 1, 1, 344],
    "A000108": [0, 0, 0, 0, 0, 2, 1556],
    "A000110": [0, 0, 0, 0, 0, 0, 94],
    "A000217": [72, 0, 0, 0, 0, 0, 1196],
    "A000290": [34, 0, 0, 0, 0, 0, 618],
    "A000578": [5, 70, 0, 0, 0, 0, 618],
    "A001006": [0, 0, 0, 0, 0, 0, 485],
}
DIFF_CLASS = ("A000108", "A000110", "A001006")   # equal under difference lens
FACT_CLASS = ("A000290", "A000578")              # equal under factorial lens


# ---- vendored term generators ----------------------------------------------

def primes_seq(k):
    o, c = [], 2
    while len(o) < k:
        if all(c % p for p in o if p * p <= c):
            o.append(c)
        c += 1
    return o


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
    "A000040": primes_seq, "A000041": partitions, "A000045": fibonacci,
    "A000108": catalan, "A000110": bell, "A000217": triangular,
    "A000290": squares, "A000578": cubes, "A001006": motzkin,
}


# ---- vendored Erdos gap-pattern complex ------------------------------------

def h1_stream(A):
    vals = sorted(set(A))
    if len(vals) < 2:
        return 0
    vset = set(vals)
    gaps = {vals[i + 1] - vals[i] for i in range(len(vals) - 1)}
    edges = [(i, i + g) for i in vals for g in gaps if i + g in vset]
    eidx = {e: k for k, e in enumerate(edges)}
    sq, sg = [], sorted(gaps)
    for a in range(len(sg)):
        for b in range(a + 1, len(sg)):
            for gh, gv in ((sg[a], sg[b]), (sg[b], sg[a])):
                for i in vals:
                    if all(v in vset for v in (i, i + gh, i + gv, i + gh + gv)):
                        sq.append((i, gh, gv))
    C1, C2 = len(edges), len(sq)
    vidx = {v: k for k, v in enumerate(vals)}
    d1 = np.zeros((len(vals), C1), dtype=int)
    for col, (s, tt) in enumerate(edges):
        d1[vidx[s], col] = -1
        d1[vidx[tt], col] = 1
    d2 = np.zeros((C1, C2), dtype=int)
    for col, (i, gh, gv) in enumerate(sq):
        for e, sgn in (((i, i + gh), 1), ((i + gv, i + gv + gh), -1),
                       ((i + gh, i + gh + gv), 1), ((i, i + gv), -1)):
            if e in eidx:
                d2[eidx[e], col] += sgn
    r1 = int(np.linalg.matrix_rank(d1)) if C1 else 0
    r2 = int(np.linalg.matrix_rank(d2)) if C2 else 0
    return max(0, (C1 - r1) - r2)


def diff_signature(terms):
    sig, cur = [], list(terms)
    for _ in range(MAX_ORDER + 1):
        sig.append(h1_stream(cur))
        if len(cur) < 2:
            break
        cur = [cur[i + 1] - cur[i] for i in range(len(cur) - 1)]
    while len(sig) <= MAX_ORDER:
        sig.append(0)
    return sig


# ---- vendored bounded factorizer + factorial signature ---------------------

def _is_prime(n):
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2; r += 1
    for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def factor(t):
    if t <= 1:
        return set(), 0, 0, True
    primes, big, m, d = set(), 0, t, 2
    while d * d <= m and d <= TRIAL_LIMIT:
        while m % d == 0:
            primes.add(d); big += 1; m //= d
        d += 1 if d == 2 else 2
    if m == 1:
        return primes, len(primes), big, True
    if _is_prime(m):
        primes.add(m); big += 1
        return primes, len(primes), big, True
    return primes, len(primes), big, False


def h1_graph(verts, edges):
    V = len(verts)
    idx = {v: i for i, v in enumerate(verts)}
    par = list(range(V))

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]; x = par[x]
        return x

    uniq = set()
    for a, b in edges:
        ia, ib = idx[a], idx[b]
        uniq.add((min(ia, ib), max(ia, ib)))
        ra, rb = find(ia), find(ib)
        if ra != rb:
            par[ra] = rb
    return max(0, len(uniq) - V + (len({find(i) for i in range(V)}) if V else 0))


def fact_signature(terms):
    parity = [t & 1 for t in terms]
    omega, big, sp, pof = [], [], [], {}
    for t in terms:
        ps, om, bg, ok = factor(t)
        if not ok:
            continue
        omega.append(om); big.append(bg); sp.append(t); pof[t] = ps
    edges = [(sp[a], sp[b]) for a, b in itertools.combinations(range(len(sp)), 2)
             if pof[sp[a]] & pof[sp[b]]]
    return [h1_stream(parity), h1_stream(omega), h1_stream(big),
            h1_graph(sorted(set(sp)), edges)]


def combined(terms):
    return diff_signature(terms) + fact_signature(terms)


def main() -> int:
    measured, ok = {}, True
    print("seq      combined 7-vector                 expected                          match")
    for sid, gen in GEN.items():
        sig = combined(gen(N_TERMS))
        measured[sid] = sig
        exp = EXPECTED[sid]
        m = sig == exp
        ok = ok and m
        print(f"{sid}  {str(sig):<33s} {str(exp):<33s} {'OK' if m else 'FAIL'}")

    vecs = [tuple(measured[s]) for s in GEN]
    law_a = len(set(vecs)) == len(vecs)                       # complete invariant
    law_b = len({tuple(measured[s]) for s in DIFF_CLASS}) == len(DIFF_CLASS)
    law_c = measured["A000290"] != measured["A000578"]
    print()
    print(f"Law A  combined is a complete invariant (all 9 distinct): {law_a}")
    print(f"Law B  factorial lens separates the difference class "
          f"{list(DIFF_CLASS)}: {law_b}")
    print(f"Law C  difference lens separates the factorial class "
          f"{list(FACT_CLASS)}: {law_c}")

    if ok and law_a and law_b and law_c:
        print("VERIFIED: 7-vectors reproduced; combined signature fully "
              "separates the corpus; each lens resolves the other's class.")
        return 0
    print("REFUTED: combined signatures or a separation law diverged.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
