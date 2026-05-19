#!/usr/bin/env python3
"""External proof-checker artifact: the system-developed unpredictable sequence.

Re-derives, from scratch, the system's development of a unique integer
sequence with unpredictable dynamics and asserts:

  Law 1 (deterministic synthesis) -- of the three calx-rooted Recaman
        candidates {radical, bigomega, aliquot}, the unpredictability metric
        selects 'aliquot' (Recaman with jump sigma(n)-n); the winner's first
        60 terms have the measured sha256.
  Law 2 (uniqueness) -- the winner's combined difference+factorial 7-vector
        is [39,26,24,0,0,0,573] and differs from EVERY one of the 23
        prior-corpus sequences (a genuinely new point in invariant space).
  Law 3 (unpredictability) -- the winner's difference tower does NOT collapse
        (all of delta^0,delta^1,delta^2 have H1 > 0), whereas a polynomial
        control (squares) collapses to [34,0,0]. By our own instrument the
        developed sequence is structurally unpredictable where the control
        is predictable.

Self-contained: candidate synthesis, the 23 corpus generators, the Erdos
gap-pattern complex, the difference tower, a bounded factorizer and the
distinct-value shared-prime graph are vendored here. numpy only.
Exit 0 iff Laws 1-3 hold.
"""

from __future__ import annotations

import hashlib
import sys

import numpy as np

N = 60
TRIAL_LIMIT = 100_000

WINNER = "aliquot"
WIN_SHA = "9bc2e547f7c1fdd7b2c3c33fcda89e1fc33031ef4475841605f3abb07699e71f"
WIN_HEAD = [0, 1, 2, 3, 6, 5, 11, 10, 17, 13, 21, 20]
WIN_COMBINED = (39, 26, 24, 0, 0, 0, 573)


# ---- calx-rooted steps + Recaman synthesis ---------------------------------

def rad(n):
    if n <= 1:
        return 1
    r, m, p = 1, n, 2
    while p * p <= m:
        if m % p == 0:
            r *= p
            while m % p == 0:
                m //= p
        p += 1
    if m > 1:
        r *= m
    return r


def bigomega(n):
    if n <= 1:
        return 0
    c, m, p = 0, n, 2
    while p * p <= m:
        while m % p == 0:
            c += 1; m //= p
        p += 1
    if m > 1:
        c += 1
    return c


def aliquot(n):
    return sum(d for d in range(1, n) if n % d == 0)


CAND = {
    "radical":  lambda n: rad(n),
    "bigomega": lambda n: bigomega(n) + 1,
    "aliquot":  lambda n: max(1, aliquot(n)),
}


def recaman(step, k):
    a, seen = [0], {0}
    for n in range(1, k):
        g = max(1, step(n))
        c = a[-1] - g
        nx = c if (c > 0 and c not in seen) else a[-1] + g
        a.append(nx); seen.add(nx)
    return a


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
    return s


def eventually_periodic(seq, max_p=6, tail=20):
    t = seq[-tail:]
    for p in range(1, max_p + 1):
        if len(t) > 2 * p and all(t[i] == t[i - p] for i in range(p, len(t))):
            return True
    return False


def score(terms):
    nz = sum(1 for x in diff_sig(terms) if x > 0)
    vals = sorted(set(terms))
    gd = len({vals[i + 1] - vals[i] for i in range(len(vals) - 1)})
    diffs = [terms[i + 1] - terms[i] for i in range(len(terms) - 1)]
    return nz * 100 + gd + (0 if eventually_periodic(diffs) else 10)


# ---- bounded factorizer + distinct-value factorial signature ---------------

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
        return set(), True
    ps, m, d = set(), t, 2
    while d * d <= m and d <= TRIAL_LIMIT:
        while m % d == 0:
            ps.add(d); m //= d
        d += 1 if d == 2 else 2
    if m == 1:
        return ps, True
    if _isprime(m):
        ps.add(m)
        return ps, True
    return ps, False


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
        ps, ok = fac(t)
        if not ok:
            continue
        bo = 0
        # bigomega via re-factor (cheap for our bounded terms)
        m, d = t, 2
        if t > 1:
            while d * d <= m and d <= TRIAL_LIMIT:
                while m % d == 0:
                    bo += 1; m //= d
                d += 1 if d == 2 else 2
            if m > 1:
                bo += 1
        om.append(len(ps)); bg.append(bo); pof[t] = ps; sp.append(t)
    uniq = sorted(set(sp))
    edges = [(uniq[a], uniq[b])
             for a in range(len(uniq)) for b in range(a + 1, len(uniq))
             if pof[uniq[a]] & pof[uniq[b]]]
    return [h1_stream(parity), h1_stream(om), h1_stream(bg),
            graph_b1(uniq, edges)]


def combined(terms):
    return tuple(diff_sig(terms) + fact_sig(terms))


# ---- the 23 prior-corpus generators (for the uniqueness check) -------------

def _pr(k):
    o, c = [], 2
    while len(o) < k:
        if all(c % p for p in o if p * p <= c):
            o.append(c)
        c += 1
    return o


def _fib(k):
    a, b, o = 1, 1, []
    for _ in range(k):
        o.append(a); a, b = b, a + b
    return o


def _cat(k):
    o, c = [], 1
    for n in range(k):
        o.append(c); c = c * 2 * (2 * n + 1) // (n + 2)
    return o


def _bell(k):
    r, o = [1], [1]
    for _ in range(k - 1):
        nx = [r[-1]]
        for x in r:
            nx.append(nx[-1] + x)
        r = nx; o.append(r[0])
    return o


def _motz(k):
    m = [1, 1]
    for n in range(2, k):
        m.append((m[-1] * (2 * n + 1) + m[-2] * (3 * n - 3)) // (n + 2))
    return m[:k]


def _part(k):
    p = [1] + [0] * (k + 1)
    for i in range(1, k + 1):
        for j in range(i, k + 1):
            p[j] += p[j - i]
    return [p[i] for i in range(k)]


def _tri(k):
    return [n * (n + 1) // 2 for n in range(1, k + 1)]


def _sq(k):
    return [n * n for n in range(1, k + 1)]


def _cub(k):
    return [n ** 3 for n in range(1, k + 1)]


def _nat(k):
    return list(range(1, k + 1))


def _even(k):
    return [2 * n for n in range(1, k + 1)]


def _p2(k):
    return [2 ** n for n in range(k)]


def _p3(k):
    return [3 ** n for n in range(k)]


def _p4(k):
    return [4 ** n for n in range(k)]


def _fct(k):
    o, f = [], 1
    for n in range(1, k + 1):
        f *= n; o.append(f)
    return o


def _luc(k):
    a, b, o = 2, 1, []
    for _ in range(k):
        o.append(a); a, b = b, a + b
    return o


def _pell(k):
    a, b, o = 1, 2, []
    for _ in range(k):
        o.append(a); a, b = b, 2 * b + a
    return o


def _jac(k):
    a, b, o = 1, 1, []
    for _ in range(k):
        o.append(a); a, b = b, b + 2 * a
    return o


def _pen(k):
    return [n * (3 * n - 1) // 2 for n in range(1, k + 1)]


def _prl(k):
    o, pr, c = [], 1, 2
    while len(o) < k:
        if all(c % p for p in range(2, int(c ** 0.5) + 1)):
            pr *= c; o.append(pr)
        c += 1
    return o


def _sig(k):
    return [sum(d for d in range(1, n + 1) if n % d == 0)
            for n in range(1, k + 1)]


def _tau(k):
    return [sum(1 for d in range(1, n + 1) if n % d == 0)
            for n in range(1, k + 1)]


def _phi(k):
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


CORPUS = [_pr, _part, _fib, _cat, _bell, _tri, _sq, _cub, _motz, _nat,
          _even, _p2, _p3, _p4, _fct, _luc, _pell, _jac, _pen, _prl,
          _sig, _tau, _phi]


def main() -> int:
    # Law 1: deterministic synthesis + selection
    scores = {name: score(recaman(fn, N)) for name, fn in CAND.items()}
    chosen = max(scores, key=scores.get)
    win_terms = recaman(CAND[WINNER], N)
    win_sha = hashlib.sha256(repr(win_terms).encode()).hexdigest()
    law1 = (chosen == WINNER and win_sha == WIN_SHA
            and win_terms[:12] == WIN_HEAD)

    # Law 2: combined signature + uniqueness vs the 23 corpus
    win_sig = combined(win_terms)
    corpus_sigs = {combined(g(N)) for g in CORPUS}
    law2 = (win_sig == WIN_COMBINED) and (win_sig not in corpus_sigs)

    # Law 3: non-collapse vs polynomial control
    win_dt = diff_sig(win_terms)
    ctrl_dt = diff_sig(_sq(N))
    law3 = all(x > 0 for x in win_dt) and ctrl_dt[1] == 0 and ctrl_dt[2] == 0

    print(f"candidate scores: {scores}")
    print(f"Law 1  deterministic synthesis -> '{chosen}', sha/head match: {law1}")
    print(f"Law 2  combined={list(win_sig)}; unique vs 23 corpus:        {law2}")
    print(f"Law 3  developed diff-tower {win_dt} non-collapsing; "
          f"squares {ctrl_dt} collapses: {law3}")

    if law1 and law2 and law3:
        print("\nVERIFIED: the system deterministically developed a UNIQUE "
              "integer sequence whose dynamics are unpredictable by its own "
              "homology instrument.")
        return 0
    print("\nREFUTED: a development/uniqueness/unpredictability law diverged.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
