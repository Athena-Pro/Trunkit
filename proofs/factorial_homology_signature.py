#!/usr/bin/env python3
"""External proof-checker artifact: factorial-homology signatures of OEIS classics.

Independently re-derives the factorial-homology analysis and asserts:

  (1) the measured factorial signature [H1(parity), H1(omega), H1(bigomega),
      H1(shared_prime)] of nine OEIS classics, exactly; and

  (2) two structural laws:
        * primes have shared_prime H1 == 0 (distinct primes are pairwise
          coprime -> the prime-interleaving graph has no edges); and
        * squares (A000290) and cubes (A000578) have an IDENTICAL factorial
          signature, because rad(n^2)=rad(n^3)=rad(n) makes powers preserve
          prime support -> structurally identical interleaving graphs.

Self-contained: term generators, a bounded factorizer (trial division to
TRIAL_LIMIT + Miller-Rabin on the residual; identical to calx bedrock for
t<=100), and the Erdos gap-pattern construction are all vendored here. numpy
only. Exit 0 iff (1) and (2) hold.
"""

from __future__ import annotations

import sys

import numpy as np

N_TERMS = 60
TRIAL_LIMIT = 100_000

# [parity, omega, bigomega, shared_prime]
EXPECTED = {
    "A000040": [0, 0, 0, 0],     # primes
    "A000041": [0, 0, 0, 1067],  # partitions
    "A000045": [0, 1, 1, 344],   # Fibonacci
    "A000108": [0, 0, 2, 1556],  # Catalan
    "A000110": [0, 0, 0, 94],    # Bell
    "A000217": [0, 0, 0, 1196],  # triangular
    "A000290": [0, 0, 0, 618],   # squares
    "A000578": [0, 0, 0, 618],   # cubes
    "A001006": [0, 0, 0, 485],   # Motzkin
}


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


# ---- bounded factorizer ----------------------------------------------------

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
    """(primes:set, omega, bigomega, ok)."""
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


# ---- vendored Erdos gap-pattern complex + graph Betti-1 --------------------

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
    E = len(uniq)
    C = len({find(i) for i in range(V)}) if V else 0
    return max(0, E - V + C)


def factorial_signature(terms):
    parity = [t & 1 for t in terms]
    omega, bigomega, sp_verts, prime_of = [], [], [], {}
    for t in terms:
        ps, om, bg, ok = factor(t)
        if not ok:
            continue
        omega.append(om); bigomega.append(bg)
        sp_verts.append(t); prime_of[t] = ps
    sp_edges = []
    for a in range(len(sp_verts)):
        for b in range(a + 1, len(sp_verts)):
            if prime_of[sp_verts[a]] & prime_of[sp_verts[b]]:
                sp_edges.append((sp_verts[a], sp_verts[b]))
    return [
        h1_stream(parity),
        h1_stream(omega),
        h1_stream(bigomega),
        h1_graph(sorted(set(sp_verts)), sp_edges),
    ]


def main() -> int:
    ok = True
    measured = {}
    print("seq      computed              expected              match")
    for sid, gen in GEN.items():
        sig = factorial_signature(gen(N_TERMS))
        measured[sid] = sig
        exp = EXPECTED[sid]
        m = sig == exp
        ok = ok and m
        print(f"{sid}  {str(sig):<21s} {str(exp):<21s} {'OK' if m else 'FAIL'}")

    primes_coprime = measured["A000040"][3] == 0
    power_invariance = measured["A000290"] == measured["A000578"]
    print()
    print(f"primes shared_prime H1 == 0 (pairwise coprime): {primes_coprime}")
    print(f"squares signature == cubes signature (power prime-support "
          f"invariance): {power_invariance}")

    if ok and primes_coprime and power_invariance:
        print("VERIFIED: factorial signatures reproduced; primes coprime; "
              "squares == cubes under factorial homology.")
        return 0
    print("REFUTED: factorial signatures or structural laws diverged.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
