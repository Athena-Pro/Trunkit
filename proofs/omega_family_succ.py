#!/usr/bin/env python3
"""External proof-checker artifact: the SUCCESSOR-kernel omega family.

Re-derives the omega/Omega-relation family over the SUCCESSOR generative
kernel [1,2,3,...] and asserts:

  Law 1 (canonical strata)  with the successor kernel each member is exactly
        the first 60 naturals satisfying its relation -- NW1 = prime powers,
        NW2/NW3 = numbers with 2/3 distinct primes, NB2/NB3/NB4 =
        semiprimes / 3-almost-primes / 4-almost-primes.
  Law 2 (relation realized + deterministic)  every emitted term satisfies
        its (omega/Omega) relation; each 60-term member matches its sha256.
  Law 3 (KERNEL-DEPENDENCE)  for every relation suffix S the difference-tower
        H1 signature of the successor member NS differs from that of its
        Aliquot-Recaman (Z000001-kernel) twin ZS -- the (omega,Omega)
        relation alone does NOT fix the homology; the generative kernel does.
  Law 4  the six successor members are pairwise-distinct sequences.

Self-contained: both kernels, a bounded factorizer, the Erdos gap-pattern
complex and the difference tower are vendored. numpy only. Exit 0 iff 1-4.
"""

from __future__ import annotations

import hashlib
import sys

import numpy as np

PER = 60
MAX_STEPS = 60_000
VALUE_CAP = 2_000_000
REL = [("W1", "w", 1), ("W2", "w", 2), ("W3", "w", 3),
       ("B2", "W", 2), ("B3", "W", 3), ("B4", "W", 4)]

SUCC_SHA16 = {
    "NW1": "c3428dc5c062fd0c", "NW2": "0d547d3d7da6a20f",
    "NW3": "486e06994b7304c1", "NB2": "205f42aaa0068be0",
    "NB3": "0a2afa8dd670e4b9", "NB4": "adee0e95eba4328e",
}


def aliquot(n):
    if n <= 1:
        return 0
    sg, m, p = 1, n, 2
    while p * p <= m:
        if m % p == 0:
            pk, s = 1, 1
            while m % p == 0:
                pk *= p
                s += pk
                m //= p
            sg *= s
        p += 1 if p == 2 else 2
    if m > 1:
        sg *= (1 + m)
    return sg - n


def _is_prime(n):
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
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


def _pollard(n):
    import math as _m
    import random as _rnd
    if n % 2 == 0:
        return 2
    while True:
        x = _rnd.randrange(2, n - 1)
        y = x
        c = _rnd.randrange(1, n - 1)
        g = 1
        while g == 1:
            x = (x * x + c) % n
            y = (y * y + c) % n
            y = (y * y + c) % n
            g = _m.gcd(abs(x - y), n)
        if g != n:
            return g


def _factor(n, acc):
    if n == 1:
        return
    if _is_prime(n):
        acc[n] = acc.get(n, 0) + 1
        return
    d = _pollard(n)
    _factor(d, acc)
    _factor(n // d, acc)


def _omega_bigomega(t):
    if t <= 1:
        return 0, 0
    acc, m, d = {}, t, 2
    while d * d <= m and d <= 100_000:
        while m % d == 0:
            acc[d] = acc.get(d, 0) + 1
            m //= d
        d += 1 if d == 2 else 2
    if m > 1:
        _factor(m, acc)
    return len(acc), sum(acc.values())


def omega_bigomega(t):
    return _omega_bigomega(t)


def succ_kernel():
    n = 1
    while True:
        yield n
        n += 1


def aliquot_kernel():
    a, seen = 0, {0}
    yield a
    n = 1
    while True:
        g = max(1, aliquot(n))
        c = a - g
        a = c if (c > 0 and c not in seen) else a + g
        seen.add(a)
        yield a
        n += 1


def build_family(kernel_gen):
    members = {s: [] for s, _, _ in REL}
    done = set()
    for v in kernel_gen():
        if v <= 1 or v > VALUE_CAP:
            continue
        w, W = omega_bigomega(v)
        for s, kind, k in REL:
            if len(members[s]) < PER and (
                    (kind == "w" and w == k) or (kind == "W" and W == k)):
                members[s].append(v)
                if len(members[s]) == PER:
                    done.add(s)
        if len(done) == len(REL):
            break
    return members


# ---- vendored gap-pattern H1 + difference tower ----------------------------

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
    for _ in range(3):
        s.append(h1_stream(cur))
        if len(cur) < 2:
            break
        cur = [cur[i + 1] - cur[i] for i in range(len(cur) - 1)]
    while len(s) < 3:
        s.append(0)
    return tuple(s)


def canonical(kind, k):
    out, n = [], 2
    while len(out) < PER:
        w, W = omega_bigomega(n)
        if (kind == "w" and w == k) or (kind == "W" and W == k):
            out.append(n)
        n += 1
    return out


def main() -> int:
    succ = build_family(succ_kernel)
    aliq = build_family(aliquot_kernel)

    law1 = all(succ[s] == canonical(kind, k) for s, kind, k in REL)

    law2 = True
    for s, kind, k in REL:
        for t in succ[s]:
            w, W = omega_bigomega(t)
            if (w if kind == "w" else W) != k:
                law2 = False
        sha = hashlib.sha256(repr(succ[s]).encode()).hexdigest()[:16]
        if sha != SUCC_SHA16["N" + s]:
            law2 = False

    law3 = True
    print("suffix  succ diff_sig        z000001 diff_sig     differ")
    for s, _, _ in REL:
        ns, zs = diff_sig(succ[s]), diff_sig(aliq[s])
        differ = ns != zs
        law3 = law3 and differ
        print(f"  {s:<5s} {str(list(ns)):<20s} {str(list(zs)):<20s} {differ}")

    shas = {s: hashlib.sha256(repr(succ[s]).encode()).hexdigest() for s, _, _ in REL}
    law4 = len(set(shas.values())) == len(REL)

    print()
    print(f"Law 1  successor strata are the canonical sequences:  {law1}")
    print(f"Law 2  relation realized + sha256 fingerprints:       {law2}")
    print(f"Law 3  KERNEL-DEPENDENCE (succ != Z000001 per relation): {law3}")
    print(f"Law 4  six distinct successor members:                {law4}")

    if law1 and law2 and law3 and law4:
        print("\nVERIFIED: the successor kernel yields the canonical omega/"
              "Omega strata, and the family is provably generative-kernel-"
              "dependent (relation alone does not fix the homology).")
        return 0
    print("\nREFUTED: a successor-family law diverged.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
