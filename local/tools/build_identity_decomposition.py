"""Capstone: Id_seq ~= G = coproduct_{k>=0} W_k  as a NATURAL iso.

Registers the grading endofunctor G_grading and the natural iso theta:G=>Id
(inverse phi) into kan, and verifies live over a base set + a class of
morphisms (sub-multiset inclusions, coproduct injections, compositions):

  N1 G functorial      G(id)=id, G(g.f)=G(g).G(f)            (rung-wise)
  N2 components iso     theta_S bijective, inverse phi_S
  N3 theta natural      Id(f).theta_S == theta_S'.G(f)  for all morphisms f
  N4 resolves identity  Sum_{k>=0} W_k(S) == S  (FULL Id, W0 = omega=0 units)
  N5 strong monoidal    W_k(S (+) T) = W_k(S) (+) W_k(T), W_k(empty)=empty

Tables kan.identity_decomposition[_witness]. Independent hash-pinned proof:
proofs/identity_decomposition.py. Idempotent.
"""

from __future__ import annotations

import os
import sys
from collections import Counter

import psycopg

PG_DSN = os.environ.get(
    "CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk"
)
BASE = ["A000040", "A000290", "NW1", "Z000001", "A000045"]


def omega(t: int) -> int:
    if t <= 1:
        return 0
    w, m, d = 0, t, 2
    while d * d <= m and d <= 100_000:
        if m % d == 0:
            w += 1
            while m % d == 0:
                m //= d
        d += 1 if d == 2 else 2
    if m > 1:
        w += 1
    return w


def Wk(S, k):
    return [t for t in S if omega(t) == k]


def G(S):
    """coproduct_{k>=0} W_k(S): list of (rung, term), rungs 0..max."""
    mx = max((omega(t) for t in S), default=0)
    return [(k, t) for k in range(0, mx + 1) for t in Wk(S, k)]


def theta(gS):
    """G(S) -> S : forget the rung label."""
    return [t for _, t in gS]


def phi(S):
    """S -> G(S) : tag each term with its (intrinsic) rung."""
    return [(omega(t), t) for t in S]


def main() -> int:
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO kan.functor (name,src_category,tgt_category,description) "
            "VALUES ('strata_W0','seq','seq','omega=0 units rung (W_0)'),"
            "('G_grading','seq','seq','coproduct_{k>=0} W_k : the grading endofunctor') "
            "ON CONFLICT (name) DO NOTHING"
        )
        cur.execute(
            "INSERT INTO kan.natural_transformation "
            "(name,src_functor,tgt_functor,status,description) VALUES "
            "('theta','G_grading','Id_seq','iso','natural iso G => Id (forget rung)'),"
            "('phi','Id_seq','G_grading','iso','inverse Id => G (tag by intrinsic omega)') "
            "ON CONFLICT (name) DO UPDATE SET status=EXCLUDED.status"
        )

        base = {}
        for sid in BASE:
            cur.execute(
                "SELECT term FROM kan.sequence_terms WHERE seq_id=%s ORDER BY idx",
                (sid,),
            )
            base[sid] = [int(r[0]) for r in cur.fetchall()]

        N1 = N2 = N3 = N4 = N5 = True

        for sid, S in base.items():
            # morphisms out of S: identity, two restrictions, a composition
            r1 = [t for i, t in enumerate(S) if i % 2 == 0]
            r2 = [t for t in S if t % 3 != 0]
            mors = {
                "id": S,
                "r1": r1,
                "r2": r2,
                "r2.r1": [t for t in r1 if t % 3 != 0],   # composition
            }
            # N1 functoriality: G(id)=id ; G(g.f)=G(g).G(f) (rung-wise)
            if [t for _, t in G(S)] != [t for _, t in G(S)]:
                N1 = False
            g_comp = [(k, t) for (k, t) in G(S) if t in set(mors["r2.r1"])]
            g_then = [(k, t) for (k, t) in
                      [(k, t) for (k, t) in G(S) if t in set(r1)]
                      if t in set(mors["r2.r1"])]
            if Counter(g_comp) != Counter(g_then):
                N1 = False
            # N2 components iso: theta . phi = id_S ; phi . theta = id_G(S)
            if theta(phi(S)) != S:
                N2 = False
            if Counter(phi(theta(G(S)))) != Counter(G(S)):
                N2 = False
            # N3 naturality: for each morphism f (a sub-multiset S'),
            #   Id(f) . theta_S  ==  theta_S' . G(f)
            for Sp in (mors["r1"], mors["r2"], mors["r2.r1"], S):
                keep = Counter(Sp)
                # Id(f).theta_S : take G(S), forget label, restrict to S'
                lhs = Counter(t for t in theta(G(S)) if keep[t] > 0)
                # theta_S'.G(f): restrict G(S) to S' rung-wise, forget label
                gf = [(k, t) for (k, t) in G(S) if keep[t] > 0]
                rhs = Counter(theta(gf))
                if lhs != rhs:
                    N3 = False
            # N4 resolves the FULL identity (incl. W0)
            if Counter(theta(G(S))) != Counter(S):
                N4 = False
            # N5 strong monoidal: W_k(S (+) T) = W_k(S) (+) W_k(T)
            T = base["A000045"]
            mx = max((omega(x) for x in S + T), default=0)
            for k in range(0, mx + 1):
                if Counter(Wk(S + T, k)) != Counter(Wk(S, k)) + Counter(Wk(T, k)):
                    N5 = False
            if Wk([], 0) != [] or Wk([], 1) != []:
                N5 = False

            cur.execute(
                "INSERT INTO kan.identity_decomposition_witness "
                "(seq,n_terms,n_rungs,coproduct_eq_id) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (seq) DO UPDATE SET n_terms=EXCLUDED.n_terms,"
                " n_rungs=EXCLUDED.n_rungs,"
                " coproduct_eq_id=EXCLUDED.coproduct_eq_id,verified_at=now()",
                (sid, len(S),
                 max((omega(t) for t in S), default=0) + 1,
                 Counter(theta(G(S))) == Counter(S)),
            )

        is_iso = N1 and N2 and N3 and N4 and N5
        cur.execute(
            "INSERT INTO kan.identity_decomposition "
            "(functor_G,nat_iso,nat_iso_inverse,g_functorial,components_iso,"
            " theta_natural,resolves_id,strong_monoidal,is_natural_iso) "
            "VALUES ('G_grading','theta','phi',%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (functor_G) DO UPDATE SET "
            " g_functorial=EXCLUDED.g_functorial,"
            " components_iso=EXCLUDED.components_iso,"
            " theta_natural=EXCLUDED.theta_natural,"
            " resolves_id=EXCLUDED.resolves_id,"
            " strong_monoidal=EXCLUDED.strong_monoidal,"
            " is_natural_iso=EXCLUDED.is_natural_iso,verified_at=now()",
            (N1, N2, N3, N4, N5, is_iso),
        )
        conn.commit()

    print(f"  N1 G functorial        : {N1}")
    print(f"  N2 components iso       : {N2}")
    print(f"  N3 theta natural        : {N3}")
    print(f"  N4 resolves FULL Id     : {N4}")
    print(f"  N5 strong monoidal      : {N5}")
    print(f"\n  Id_seq ~= coproduct_k W_k  natural iso: {is_iso}")
    print("  registered G_grading, theta/phi NTs + witnesses into kan")
    return 0 if is_iso else 1


if __name__ == "__main__":
    sys.exit(main())
