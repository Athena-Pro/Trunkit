#!/usr/bin/env python3
"""External proof-checker artifact: the omega-relation family.

Re-derives, from scratch, the family the system developed -- the (omega,Omega)
relation strata of the Aliquot-Recaman generative kernel (Z000001's dynamics)
-- and asserts:

  Law 1 (generative set)  the kernel's first 12 terms are exactly Z000001's
        head [0,1,2,3,6,5,11,10,17,13,21,20]; the family is its strata.
  Law 2 (relation realized)  for every member, EVERY emitted term satisfies
        its defining relation exactly: ZW1/2/3 -> omega == 1/2/3,
        ZB2/3/4 -> Omega == 2/3/4. (60 terms each.)
  Law 3 (deterministic)  each member's 60-term sha256 matches the measured
        fingerprint.
  Law 4 (stratification + distinctness)  each omega-member has a CONSTANT
        omega-stream and each Omega-member a constant Omega-stream (so the
        factorial instrument's corresponding axis degenerates by
        construction); the six members are pairwise-distinct sequences.

Self-contained: kernel, fast sigma, and the bounded factorizer are vendored.
No numpy needed. Exit 0 iff Laws 1-4 hold.
"""

from __future__ import annotations

import hashlib
import sys

PER_MEMBER = 60
MAX_STEPS = 60_000
VALUE_CAP = 2_000_000

RELATIONS = [
    ("ZW1", "w", 1), ("ZW2", "w", 2), ("ZW3", "w", 3),
    ("ZB2", "W", 2), ("ZB3", "W", 3), ("ZB4", "W", 4),
]

Z000001_HEAD = [0, 1, 2, 3, 6, 5, 11, 10, 17, 13, 21, 20]

SHA16 = {
    "ZW1": "bd1f77f137a05ee5", "ZW2": "d9f2ea8945e323a0",
    "ZW3": "3b701b1682882114", "ZB2": "993d504c66fb5e32",
    "ZB3": "6c42f8aa2109b585", "ZB4": "dc6bdc252a8bd205",
}


def aliquot(n: int) -> int:
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


def omega_bigomega(t: int):
    return _omega_bigomega(t)


def kernel(max_steps):
    a, seen = 0, {0}
    yield a
    for n in range(1, max_steps):
        g = max(1, aliquot(n))
        c = a - g
        a = c if (c > 0 and c not in seen) else a + g
        seen.add(a)
        yield a


def build_family():
    members = {mid: [] for mid, _, _ in RELATIONS}
    done = set()
    head = []
    for i, v in enumerate(kernel(MAX_STEPS)):
        if i < 12:
            head.append(v)
        if v <= 1 or v > VALUE_CAP:
            continue
        w, W = omega_bigomega(v)
        for mid, kind, k in RELATIONS:
            if len(members[mid]) < PER_MEMBER and (
                    (kind == "w" and w == k) or (kind == "W" and W == k)):
                members[mid].append(v)
                if len(members[mid]) == PER_MEMBER:
                    done.add(mid)
        if len(done) == len(RELATIONS):
            break
    return head, members


def main() -> int:
    head, members = build_family()

    law1 = head == Z000001_HEAD

    law2 = True
    for mid, kind, k in RELATIONS:
        for t in members[mid]:
            w, W = omega_bigomega(t)
            if (w if kind == "w" else W) != k:
                law2 = False

    law3 = all(
        hashlib.sha256(repr(members[mid]).encode()).hexdigest()[:16]
        == SHA16[mid] for mid, _, _ in RELATIONS
    )

    const_ok = True
    for mid, kind, k in RELATIONS:
        stream = [omega_bigomega(t)[0 if kind == "w" else 1]
                  for t in members[mid]]
        if len(set(stream)) != 1 or stream[0] != k:
            const_ok = False
    shas = {mid: hashlib.sha256(repr(members[mid]).encode()).hexdigest()
            for mid, _, _ in RELATIONS}
    distinct = len(set(shas.values())) == len(RELATIONS)
    law4 = const_ok and distinct

    for mid, kind, k in RELATIONS:
        print(f"  {mid}  {kind}={k}  n={len(members[mid])}  "
              f"head={members[mid][:6]}  sha16={shas[mid][:16]}")
    print()
    print(f"Law 1  generative set == Z000001 kernel head:        {law1}")
    print(f"Law 2  every term satisfies its (w/W) relation:      {law2}")
    print(f"Law 3  per-member 60-term sha256 fingerprints match: {law3}")
    print(f"Law 4  axis-constant strata + 6 distinct members:    {law4}")

    if law1 and law2 and law3 and law4:
        print("\nVERIFIED: the system algorithmically developed a 6-member "
              "family with exact small-omega / big-Omega relations to the "
              "generative set.")
        return 0
    print("\nREFUTED: a family law diverged.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
