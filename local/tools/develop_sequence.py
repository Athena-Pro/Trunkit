"""Develop a unique integer sequence with unpredictable dynamics.

The system synthesizes several deterministic Recaman/Collatz-style candidates
whose jump is driven by a calx number-theoretic function OF THE INDEX (so the
step is bounded by ~n and the resulting terms stay factor-friendly for the
homology instruments), scores each by an unpredictability metric grounded in
our OWN tools, selects the winner, and registers it in the unified model.

Unpredictability score (higher = less predictable):
  * difference-tower non-collapse : #nonzero H1 over delta^0,delta^1,delta^2
                                    (predictable polynomial/linear sequences
                                     collapse to 0 by delta^1/delta^2)
  * gap diversity                 : |gap set of delta^0|
  * non-eventual-periodicity      : first differences not eventually periodic
                                     with small period over the tail

Deterministic end to end (so the choice is hash-pinnable by cert).
"""

from __future__ import annotations

import os
import sys

import numpy as np
import psycopg

PG_DSN = os.environ.get(
    "CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk"
)
K = 60


# ---- calx-rooted number-theoretic step functions (of the index) ------------

def rad(n: int) -> int:
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


def bigomega(n: int) -> int:
    if n <= 1:
        return 0
    c, m, p = 0, n, 2
    while p * p <= m:
        while m % p == 0:
            c += 1
            m //= p
        p += 1
    if m > 1:
        c += 1
    return c


def aliquot(n: int) -> int:
    return sum(d for d in range(1, n) if n % d == 0)  # sigma(n)-n


def recaman_variant(step_fn, k: int) -> list[int]:
    """Recaman recurrence with a custom (index-driven) jump; deterministic."""
    a, seen = [0], {0}
    for n in range(1, k):
        g = max(1, step_fn(n))
        cand = a[-1] - g
        nxt = cand if (cand > 0 and cand not in seen) else a[-1] + g
        a.append(nxt)
        seen.add(nxt)
    return a


CANDIDATES = {
    "radical":  lambda n: rad(n),
    "bigomega": lambda n: bigomega(n) + 1,
    "aliquot":  lambda n: max(1, aliquot(n)),
}


# ---- vendored gap-pattern H1 (for the unpredictability metric) -------------

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
    sig = diff_sig(terms)
    nz = sum(1 for x in sig if x > 0)
    vals = sorted(set(terms))
    gapdiv = len({vals[i + 1] - vals[i] for i in range(len(vals) - 1)})
    diffs = [terms[i + 1] - terms[i] for i in range(len(terms) - 1)]
    nonper = 0 if eventually_periodic(diffs) else 10
    return nz * 100 + gapdiv + nonper, sig, gapdiv, nonper > 0


def main() -> int:
    print("Candidate development (system selects the most unpredictable):\n")
    results = {}
    for name, fn in CANDIDATES.items():
        terms = recaman_variant(fn, K)
        sc, sig, gd, nonper = score(terms)
        results[name] = (sc, terms, sig, gd, nonper)
        print(f"  {name:<9s} score={sc:<5d} diff_sig={sig} gap_div={gd} "
              f"non_periodic={nonper}  head={terms[:8]}")

    winner = max(results, key=lambda k: results[k][0])
    sc, terms, sig, gd, nonper = results[winner]
    seq_id = "Z000001"
    name = f"{winner.title()}-Recaman (system-developed)"
    print(f"\n  >>> selected: '{winner}'  -> {seq_id} {name}")
    print(f"      diff-tower H1 signature = {sig}  (non-collapsing = "
          f"unpredictable)")
    print(f"      first 12 terms: {terms[:12]}")

    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO calx.sequences (seq_id,name,seq_type,family) "
            "VALUES (%s,%s,'dynamical','recaman-variant') "
            "ON CONFLICT (seq_id) DO UPDATE SET name=EXCLUDED.name",
            (seq_id, name),
        )
        cur.execute("DELETE FROM kan.sequence_terms WHERE seq_id=%s", (seq_id,))
        for idx, val in enumerate(terms, start=1):
            cur.execute(
                "INSERT INTO kan.sequence_terms (seq_id,idx,term) "
                "VALUES (%s,%s,%s)",
                (seq_id, idx, int(val)),
            )
        conn.commit()
    print(f"\n  registered {seq_id}: {len(terms)} terms into the unified model")
    return 0


if __name__ == "__main__":
    sys.exit(main())
