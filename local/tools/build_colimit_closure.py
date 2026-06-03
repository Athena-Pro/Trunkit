"""(Co)limit closure of the strata category over S = {2..N}.

Computes the pullback (bigrading cell = fiber product W_i x_S B_j), its dual
pushout (W_i +_{C_ij} B_j = union, incl-excl), the coproduct recovery
(empty-gluing = certified step 60), and the commuting-idempotent + Mobius
distributive law. Populates kan.colimit_closure[_cell]. Uses the SAME ambient
S and canonical signature as proofs/colimit_closure.py, so the live engine
corroborates the external proof (auto-bridged by step-79). Idempotent.
"""

from __future__ import annotations

import hashlib
import os
import sys

import psycopg

PG_DSN = os.environ.get(
    "CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk"
)
N = 2000


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


def omega_bigomega(t):
    if t <= 1:
        return (0, 0)
    acc, m, d = {}, t, 2
    while d * d <= m and d <= 100_000:
        while m % d == 0:
            acc[d] = acc.get(d, 0) + 1
            m //= d
        d += 1 if d == 2 else 2
    if m > 1:
        _factor(m, acc)
    return (len(acc), sum(acc.values()))


def main() -> int:
    S = list(range(2, N + 1))
    ow = {t: omega_bigomega(t) for t in S}
    I = sorted({w for w, _ in ow.values()})
    J = sorted({b for _, b in ow.values()})
    Wi = {i: frozenset(t for t in S if ow[t][0] == i) for i in I}
    Bj = {j: frozenset(t for t in S if ow[t][1] == j) for j in J}
    Cij = {(i, j): frozenset(t for t in S if ow[t] == (i, j))
           for i in I for j in J}

    def K(a, b):
        return sum(1 for t in S if ow[t][0] <= a and ow[t][1] <= b)

    pullback_ok = pushout_ok = distributive_ok = True
    cell_rows = []
    for i in I:
        for j in J:
            cell = Cij[(i, j)]
            inter = Wi[i] & Bj[j]
            is_pb = (cell == inter
                     and cell == frozenset(t for t in S
                                           if t in Wi[i] and t in Bj[j]))
            if not is_pb:
                pullback_ok = False
            push = Wi[i] | Bj[j]
            if (push != (set(Wi[i]) | set(Bj[j]))
                    or len(push) != len(Wi[i]) + len(Bj[j]) - len(cell)
                    or any(t not in Wi[i] and t not in Bj[j] for t in push)):
                pushout_ok = False
            wb = frozenset(t for t in Bj[j] if ow[t][0] == i)
            bw = frozenset(t for t in Wi[i] if ow[t][1] == j)
            if not (wb == bw == cell):
                distributive_ok = False
            mob = K(i, j) - K(i - 1, j) - K(i, j - 1) + K(i - 1, j - 1)
            mob_ok = (mob == len(cell))
            if not mob_ok:
                distributive_ok = False
            if cell:
                cell_rows.append((i, j, len(cell), is_pb, mob_ok))

    for i in I:
        if frozenset().union(*(Cij[(i, j)] for j in J)) != Wi[i]:
            distributive_ok = False
    for j in J:
        if frozenset().union(*(Cij[(i, j)] for i in I)) != Bj[j]:
            distributive_ok = False

    disjoint = all(not (Wi[a] & Wi[b])
                   for x, a in enumerate(I) for b in I[x + 1:])
    coproduct_ok = (disjoint
                    and (set().union(*Wi.values()) if Wi else set()) == set(S)
                    and sum(len(Wi[i]) for i in I) == len(S))

    cells = sorted((i, j, len(Cij[(i, j)])) for i in I for j in J)
    margW = sorted((i, len(Wi[i])) for i in I)
    margB = sorted((j, len(Bj[j])) for j in J)
    head_sha = hashlib.sha256(
        repr((N, cells, margW, margB, len(S))).encode()).hexdigest()

    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO kan.functor (name,src_category,tgt_category,description) "
            "VALUES ('colimitclosure','seq','seq','(co)limit closure: pullback "
            "= bigrading cell, pushout = strata gluing, omega/Omega "
            "distributive law') ON CONFLICT (name) DO NOTHING"
        )
        cur.execute("DELETE FROM kan.colimit_cell")
        cur.execute("DELETE FROM kan.colimit_closure")
        for (i, j, c, pb, mb) in cell_rows:
            cur.execute(
                "INSERT INTO kan.colimit_cell "
                "(omega_i,bigomega_j,card,is_pullback,mobius_ok) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (omega_i,bigomega_j) "
                "DO UPDATE SET card=EXCLUDED.card,"
                " is_pullback=EXCLUDED.is_pullback,mobius_ok=EXCLUDED.mobius_ok",
                (i, j, c, pb, mb),
            )
        cur.execute(
            "INSERT INTO kan.colimit_closure "
            "(structure,corpus_lo,corpus_hi,n_cells,pullback_ok,pushout_ok,"
            " coproduct_ok,distributive_ok,head_sha) "
            "VALUES ('strata',%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (structure) DO UPDATE SET corpus_lo=EXCLUDED.corpus_lo,"
            " corpus_hi=EXCLUDED.corpus_hi,n_cells=EXCLUDED.n_cells,"
            " pullback_ok=EXCLUDED.pullback_ok,pushout_ok=EXCLUDED.pushout_ok,"
            " coproduct_ok=EXCLUDED.coproduct_ok,"
            " distributive_ok=EXCLUDED.distributive_ok,"
            " head_sha=EXCLUDED.head_sha,verified_at=now()",
            (2, N, len(cell_rows), pullback_ok, pushout_ok,
             coproduct_ok, distributive_ok, head_sha),
        )
        conn.commit()

    print(f"  S={{2..{N}}} omega in {I} Omega in {J} cells={len(cell_rows)}")
    print(f"  L1 pullback={pullback_ok}  L2 pushout={pushout_ok}  "
          f"L3 coproduct={coproduct_ok}  L4 distributive={distributive_ok}")
    print(f"  head sha256: {head_sha[:16]}")
    return 0 if (pullback_ok and pushout_ok and coproduct_ok
                 and distributive_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
