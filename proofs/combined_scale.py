#!/usr/bin/env python3
"""External proof-checker artifact: the combined invariant AT SCALE (N=23).

Recomputes the combined (difference (+) factorial) 7-vector for all 23 OEIS
classics and asserts the measured scale behaviour:

  Law 1  on the original 9-classic corpus the combined signature is a
         COMPLETE invariant (all 9 distinct) -- unchanged;
  Law 2  on the full 23-corpus it is NOT complete: exactly 20 distinct
         vectors / 23 sequences;
  Law 3  the collision kernel is structural -- {powers of 2, powers of 3,
         powers of 4} are mutually combined-equivalent ({p^n} have identical
         gap-pattern AND factorization homology, prime-independent), and
         primorials == evens.

Self-contained: all term generators, the Erdos gap-pattern complex, the
difference tower, a bounded factorizer and the (distinct-value) shared-prime
graph are vendored here. numpy only. Exit 0 iff Laws 1-3 hold.
"""

from __future__ import annotations

import itertools
import sys

import numpy as np

N = 60
TRIAL_LIMIT = 100_000
MAX_ORDER = 2

NINE = ["A000040", "A000041", "A000045", "A000108", "A000110",
        "A000217", "A000290", "A000578", "A001006"]
POWER_CLASS = ["A000079", "A000244", "A000302"]      # powers of 2,3,4
PRIMORIAL, EVENS = "A002110", "A005843"


# ---- vendored term generators (all 23) -------------------------------------

def _primes(k):
    o, c = [], 2
    while len(o) < k:
        if all(c % p for p in o if p * p <= c):
            o.append(c)
        c += 1
    return o


def fib(k):
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
    r, o = [1], [1]
    for _ in range(k - 1):
        nx = [r[-1]]
        for x in r:
            nx.append(nx[-1] + x)
        r = nx; o.append(r[0])
    return o


def motzkin(k):
    m = [1, 1]
    for n in range(2, k):
        m.append((m[-1] * (2 * n + 1) + m[-2] * (3 * n - 3)) // (n + 2))
    return m[:k]


def parts(k):
    p = [1] + [0] * (k + 1)
    for i in range(1, k + 1):
        for j in range(i, k + 1):
            p[j] += p[j - i]
    return [p[i] for i in range(k)]


def tri(k):
    return [n * (n + 1) // 2 for n in range(1, k + 1)]


def sq(k):
    return [n * n for n in range(1, k + 1)]


def cube(k):
    return [n ** 3 for n in range(1, k + 1)]


def nat(k):
    return list(range(1, k + 1))


def even(k):
    return [2 * n for n in range(1, k + 1)]


def p2(k):
    return [2 ** n for n in range(k)]


def p3(k):
    return [3 ** n for n in range(k)]


def p4(k):
    return [4 ** n for n in range(k)]


def fact(k):
    o, f = [], 1
    for n in range(1, k + 1):
        f *= n; o.append(f)
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


def jac(k):
    a, b, o = 1, 1, []
    for _ in range(k):
        o.append(a); a, b = b, b + 2 * a
    return o


def penta(k):
    return [n * (3 * n - 1) // 2 for n in range(1, k + 1)]


def primorial(k):
    o, pr, c = [], 1, 2
    while len(o) < k:
        if all(c % p for p in range(2, int(c ** 0.5) + 1)):
            pr *= c; o.append(pr)
        c += 1
    return o


def sigma(k):
    return [sum(d for d in range(1, n + 1) if n % d == 0)
            for n in range(1, k + 1)]


def tau(k):
    return [sum(1 for d in range(1, n + 1) if n % d == 0)
            for n in range(1, k + 1)]


def phi(k):
    def f(n):
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
    return [f(n) for n in range(1, k + 1)]


GEN = {
    "A000040": _primes, "A000041": parts, "A000045": fib, "A000108": catalan,
    "A000110": bell, "A000217": tri, "A000290": sq, "A000578": cube,
    "A001006": motzkin, "A000027": nat, "A005843": even, "A000079": p2,
    "A000244": p3, "A000302": p4, "A000142": fact, "A000032": lucas,
    "A000129": pell, "A001045": jac, "A000326": penta, "A002110": primorial,
    "A000203": sigma, "A000005": tau, "A000010": phi,
}


# ---- vendored Erdos gap-pattern H1 -----------------------------------------

def h1_stream(A):
    vals = sorted(set(A))
    if len(vals) < 2:
        return 0
    vset = set(vals)
    gaps = {vals[i + 1] - vals[i] for i in range(len(vals) - 1)}
    edges = [(i, i + g) for i in vals for g in gaps if i + g in vset]
    eidx = {e: k for k, e in enumerate(edges)}
    sqs, sg = [], sorted(gaps)
    for a in range(len(sg)):
        for b in range(a + 1, len(sg)):
            for gh, gv in ((sg[a], sg[b]), (sg[b], sg[a])):
                for i in vals:
                    if all(v in vset for v in (i, i + gh, i + gv, i + gh + gv)):
                        sqs.append((i, gh, gv))
    C1, C2 = len(edges), len(sqs)
    vidx = {v: k for k, v in enumerate(vals)}
    d1 = np.zeros((len(vals), C1), dtype=int)
    for col, (s, t) in enumerate(edges):
        d1[vidx[s], col] = -1
        d1[vidx[t], col] = 1
    d2 = np.zeros((C1, C2), dtype=int)
    for col, (i, gh, gv) in enumerate(sqs):
        for e, sgn in (((i, i + gh), 1), ((i + gv, i + gv + gh), -1),
                       ((i + gh, i + gh + gv), 1), ((i, i + gv), -1)):
            if e in eidx:
                d2[eidx[e], col] += sgn
    r1 = int(np.linalg.matrix_rank(d1)) if C1 else 0
    r2 = int(np.linalg.matrix_rank(d2)) if C2 else 0
    return max(0, (C1 - r1) - r2)


def diff_sig(terms):
    s, cur = [], list(terms)
    for _ in range(MAX_ORDER + 1):
        s.append(h1_stream(cur))
        if len(cur) < 2:
            break
        cur = [cur[i + 1] - cur[i] for i in range(len(cur) - 1)]
    while len(s) <= MAX_ORDER:
        s.append(0)
    return s


def _isprime(n):
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


def fac(t):
    if t <= 1:
        return set(), 0, 0, True
    ps, big, m, d = set(), 0, t, 2
    while d * d <= m and d <= TRIAL_LIMIT:
        while m % d == 0:
            ps.add(d); big += 1; m //= d
        d += 1 if d == 2 else 2
    if m == 1:
        return ps, len(ps), big, True
    if _isprime(m):
        ps.add(m); big += 1
        return ps, len(ps), big, True
    return ps, len(ps), big, False


def graph_b1(verts, edges):
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


def fact_sig(terms):
    parity = [t & 1 for t in terms]
    om, bg, pof, sp = [], [], {}, []
    for t in terms:
        ps, o, b, ok = fac(t)
        if not ok:
            continue
        om.append(o); bg.append(b); pof[t] = ps; sp.append(t)
    uniq = sorted(set(sp))
    edges = [(uniq[a], uniq[b])
             for a in range(len(uniq)) for b in range(a + 1, len(uniq))
             if pof[uniq[a]] & pof[uniq[b]]]
    return [h1_stream(parity), h1_stream(om), h1_stream(bg),
            graph_b1(uniq, edges)]


def combined(terms):
    return tuple(diff_sig(terms) + fact_sig(terms))


def main() -> int:
    sig = {sid: combined(g(N)) for sid, g in GEN.items()}

    nine_distinct = len({sig[s] for s in NINE}) == len(NINE)
    all_distinct = len(set(sig.values()))
    law2 = all_distinct == 20
    pc = len({sig[s] for s in POWER_CLASS}) == 1
    pe = sig[PRIMORIAL] == sig[EVENS]

    print(f"Law 1  N=9 complete invariant (all 9 distinct):        {nine_distinct}")
    print(f"Law 2  N=23 -> {all_distinct} distinct / 23 (expect 20): {law2}")
    print(f"Law 3a {POWER_CLASS} mutually combined-equivalent:      {pc}")
    print(f"Law 3b primorials == evens:                            {pe}")
    print()
    for sid in sorted(GEN):
        print(f"  {sid} {list(sig[sid])}")

    if nine_distinct and law2 and pc and pe:
        print("\nVERIFIED: complete at N=9; degrades to 20/23 at N=23; "
              "kernel = single-prime-power class + primorial==evens.")
        return 0
    print("\nREFUTED: scale behaviour diverged from the measured claim.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
