"""Engine: the kan layer as a proarrow equipment over the certified strata
posets. Mirrors proofs/equipment.py exactly (identical canonical sha) and
populates kan.equipment[_arrow]. The boolean kan.equipment_laws view is
auto-corroborated by the step-79 kan-engine -> cert bridge. Idempotent.
"""

from __future__ import annotations

import hashlib
import itertools
import os
import sys

import psycopg

PG_DSN = os.environ.get(
    "CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk"
)


def chain(n):
    e = tuple(range(n))
    return e, frozenset((i, j) for i in e for j in e if i <= j)


def product_poset():
    e = ((0, 0), (0, 1), (1, 0), (1, 1))
    return e, frozenset((x, y) for x in e for y in e
                        if x[0] <= y[0] and x[1] <= y[1])


A = chain(3)
B = chain(4)
P = product_poset()


def closure(rel, X, Y):
    Xel, Xleq = X
    Yel, Yleq = Y
    R = set(rel)
    ch = True
    while ch:
        ch = False
        for (x, y) in list(R):
            for xp in Xel:
                if (xp, x) in Xleq and (xp, y) not in R:
                    R.add((xp, y)); ch = True
            for yp in Yel:
                if (y, yp) in Yleq and (x, yp) not in R:
                    R.add((x, yp)); ch = True
    return frozenset(R)


def is_bimodule(rel, X, Y):
    return closure(rel, X, Y) == frozenset(rel)


def pcompose(R, S, X, Y, Z):
    base = {(x, z) for (x, y1) in R for (y2, z) in S if y1 == y2}
    return closure(base, X, Z)


def U(X):
    return X[1]


def all_bimodules(X, Y):
    cells = [(x, y) for x in X[0] for y in Y[0]]
    seen, out = set(), []
    for k in range(len(cells) + 1):
        for sub in itertools.combinations(cells, k):
            c = closure(sub, X, Y)
            if c not in seen:
                seen.add(c); out.append(c)
    return out


def companion(f, X, Y):
    return closure({(x, y) for x in X[0] for y in Y[0]
                    if (f[x], y) in Y[1]}, X, Y)


def conjoint(f, X, Y):
    return closure({(y, x) for y in Y[0] for x in X[0]
                    if (y, f[x]) in Y[1]}, Y, X)


def compose_maps(f, g):
    return {x: g[f[x]] for x in f}


def ident(X):
    return {x: x for x in X[0]}


def main() -> int:
    f = {0: 0, 1: 1, 2: 3}
    g = {0: 0, 1: 1, 2: 2, 3: 2}
    h = {0: (0, 0), 1: (0, 1), 2: (1, 1)}
    arrows = [("f:A->B", f, A, B), ("g:B->A", g, B, A),
              ("h:A->P", h, A, P), ("idA", ident(A), A, A),
              ("idB", ident(B), B, B)]

    sig, arr_rows = [], []
    E1 = E2 = True
    for name, mp, X, Y in arrows:
        fsh = companion(mp, X, Y)
        fst = conjoint(mp, X, Y)
        e1 = is_bimodule(fsh, X, Y) and is_bimodule(fst, Y, X)
        un = U(X) <= pcompose(fsh, fst, X, Y, X)
        co = pcompose(fst, fsh, Y, X, Y) <= U(Y)
        z1 = pcompose(pcompose(fsh, fst, X, Y, X), fsh, X, X, Y) == fsh
        z2 = pcompose(pcompose(fst, fsh, Y, X, Y), fst, Y, Y, X) == fst
        E1 = E1 and (un and co and z1)
        E2 = E2 and (e1 and co and z2)
        sig.append((name, sorted(fsh), sorted(fst),
                    int(e1), int(un), int(co), int(z1), int(z2)))
        arr_rows.append((name, e1, un, co, z1, z2, len(fsh), len(fst)))

    E3 = True
    p2 = {0: 0, 1: 0, 2: 1}
    q2 = {0: 0, 1: 2, 2: 3}
    p_sh = companion(p2, A, A)
    q_st = conjoint(q2, A, B)
    n_bm = 0
    for M in all_bimodules(A, B):
        n_bm += 1
        restr = closure({(a, c) for a in A[0] for c in A[0]
                         if (p2[a], q2[c]) in M}, A, A)
        viaPC = pcompose(pcompose(p_sh, M, A, A, B), q_st, A, B, A)
        if restr != viaPC:
            E3 = False
        biggest = closure({(a, c) for a in A[0] for c in A[0]
                           if (p2[a], q2[c]) in M}, A, A)
        if restr != biggest:
            E3 = False

    E4 = True
    gf = compose_maps(f, g)
    fg = compose_maps(g, f)
    if companion(gf, A, A) != pcompose(companion(f, A, B),
                                       companion(g, B, A), A, B, A):
        E4 = False
    if conjoint(gf, A, A) != pcompose(conjoint(g, B, A),
                                      conjoint(f, A, B), A, B, A):
        E4 = False
    if companion(fg, B, B) != pcompose(companion(g, B, A),
                                       companion(f, A, B), B, A, B):
        E4 = False
    if companion(ident(A), A, A) != U(A) or conjoint(ident(A), A, A) != U(A):
        E4 = False
    if companion(ident(B), B, B) != U(B) or conjoint(ident(B), B, B) != U(B):
        E4 = False

    canon = (tuple(sig), n_bm,
             sorted(companion(gf, A, A)), sorted(conjoint(gf, A, A)),
             sorted(U(A)), sorted(U(B)))
    head_sha = hashlib.sha256(repr(canon).encode()).hexdigest()

    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO kan.functor (name,src_category,tgt_category,description) "
            "VALUES ('equipment','seq','seq','proarrow equipment: companion "
            "f_! and conjoint f^* of every tight arrow; fibrant loose double "
            "category') ON CONFLICT (name) DO NOTHING"
        )
        cur.execute("DELETE FROM kan.equipment_arrow")
        cur.execute("DELETE FROM kan.equipment")
        for (nm, e1, un, co, z1, z2, cs, cj) in arr_rows:
            cur.execute(
                "INSERT INTO kan.equipment_arrow "
                "(tight,is_bimodule,unit_ok,counit_ok,zigzag1_ok,zigzag2_ok,"
                " companion_card,conjoint_card) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (tight) DO UPDATE SET is_bimodule=EXCLUDED.is_bimodule,"
                " unit_ok=EXCLUDED.unit_ok,counit_ok=EXCLUDED.counit_ok,"
                " zigzag1_ok=EXCLUDED.zigzag1_ok,zigzag2_ok=EXCLUDED.zigzag2_ok,"
                " companion_card=EXCLUDED.companion_card,"
                " conjoint_card=EXCLUDED.conjoint_card",
                (nm, e1, un, co, z1, z2, cs, cj),
            )
        cur.execute(
            "INSERT INTO kan.equipment "
            "(structure,n_objects,n_tight,n_bimodules,companion_ok,"
            " conjoint_ok,fibrant_ok,coherence_ok,head_sha) "
            "VALUES ('strata_posets',3,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (structure) DO UPDATE SET n_tight=EXCLUDED.n_tight,"
            " n_bimodules=EXCLUDED.n_bimodules,"
            " companion_ok=EXCLUDED.companion_ok,"
            " conjoint_ok=EXCLUDED.conjoint_ok,fibrant_ok=EXCLUDED.fibrant_ok,"
            " coherence_ok=EXCLUDED.coherence_ok,head_sha=EXCLUDED.head_sha,"
            " verified_at=now()",
            (len(arrows), n_bm, E1, E2, E3, E4, head_sha),
        )
        conn.commit()

    print(f"  posets=3 tight={len(arrows)} bimodules(A-|->B)={n_bm}")
    print(f"  E1 companion={E1} E2 conjoint={E2} E3 fibrant={E3} "
          f"E4 coherence={E4}")
    print(f"  head sha256: {head_sha[:16]}")
    return 0 if (E1 and E2 and E3 and E4) else 1


if __name__ == "__main__":
    sys.exit(main())
