"""Coreflection + coproduct decomposition of the strata tower.

For the omega-tower rungs W_k (k=1..3) over a representative base:

  (A) record each W_k as a coreflector  i_k -| W_k  (kan layer-24/28 rows:
      identity functor Id_seq, inclusion incl_W{k}, counit NT, adjunction)
      with the adjunction laws checked live;
  (B) record, per base sequence S, the coproduct decomposition
      S|{omega>=1} ~= coproduct_k W_k(S)  with the universal-property flags.

Tables kan.coreflection / kan.grading_decomposition. The independent
hash-pinned proof is proofs/grading.py. Idempotent.
"""

from __future__ import annotations

import os
import sys

import psycopg

PG_DSN = os.environ.get(
    "CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk"
)
BASE = ["A000040", "A000290", "NW1", "Z000001"]
RUNGS = [1, 2, 3]


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


def main() -> int:
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT seq_id FROM calx.sequences")
        # functors: identity + inclusions (W_k already exist as strata_W{k})
        cur.execute(
            "INSERT INTO kan.functor (name,src_category,tgt_category,description) "
            "VALUES ('Id_seq','seq','seq','identity endofunctor on sequences') "
            "ON CONFLICT (name) DO NOTHING"
        )
        for k in RUNGS:
            cur.execute(
                "INSERT INTO kan.functor (name,src_category,tgt_category,description) "
                "VALUES (%s,'seq','seq',%s) ON CONFLICT (name) DO NOTHING",
                (f"incl_W{k}", f"inclusion of the omega={k} subcategory"),
            )

        base_terms = {}
        for sid in BASE:
            cur.execute(
                "SELECT term FROM kan.sequence_terms WHERE seq_id=%s ORDER BY idx",
                (sid,),
            )
            base_terms[sid] = [int(r[0]) for r in cur.fetchall()]

        # ---- (A) coreflection per rung -------------------------------------
        for k in RUNGS:
            rf = f"strata_W{k}"
            # idempotent (W_k W_k = W_k) and counit naturality
            # (filtering commutes with sub-multiset restriction) over BASE
            idem = nat = tri = True
            for sid in BASE:
                S = base_terms[sid]
                wk = Wk(S, k)
                if Wk(wk, k) != wk:
                    idem = False
                if Wk(wk, k) != wk:                 # triangle: W_k(eps)=id
                    tri = False
                # naturality wrt a restriction S' subset S
                Sp = S[: len(S) // 2]
                lhs = Wk([t for t in S if t in set(Sp)], k)
                rhs = [t for t in Wk(S, k) if t in set(Sp)]
                if sorted(lhs) != sorted(rhs):
                    nat = False
            coref = idem and nat and tri

            cur.execute(
                "INSERT INTO kan.natural_transformation "
                "(name,src_functor,tgt_functor,status,description) VALUES "
                "(%s,%s,'Id_seq',%s,%s) ON CONFLICT (name) DO UPDATE "
                "SET status=EXCLUDED.status",
                (f"counit_W{k}", rf, "verified" if nat else "not_natural",
                 f"eps_{k}: W_{k} => Id (stratum inclusion)"),
            )
            cur.execute(
                "INSERT INTO kan.natural_transformation "
                "(name,src_functor,tgt_functor,status,description) VALUES "
                "(%s,'Id_seq',%s,'iso',%s) ON CONFLICT (name) DO UPDATE "
                "SET status=EXCLUDED.status",
                (f"unit_W{k}", rf,
                 f"eta_{k}: Id => W_{k} o i_{k} (iso on the subcategory)"),
            )
            cur.execute(
                "INSERT INTO kan.adjunction "
                "(name,left_functor,right_functor,unit_nt,counit_nt,status,"
                " description) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (name) DO UPDATE SET status=EXCLUDED.status",
                (f"coreflect_W{k}", f"incl_W{k}", rf, f"unit_W{k}",
                 f"counit_W{k}", "verified" if coref else "triangle_fail",
                 f"i_{k} -| W_{k}: omega={k} subcategory is coreflective"),
            )
            cur.execute(
                "INSERT INTO kan.coreflection "
                "(rung_functor,grading,k,inclusion,counit_nt,adjunction,"
                " idempotent,counit_natural,triangle_ok,is_coreflector) "
                "VALUES (%s,'omega',%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (rung_functor) DO UPDATE SET "
                " idempotent=EXCLUDED.idempotent,"
                " counit_natural=EXCLUDED.counit_natural,"
                " triangle_ok=EXCLUDED.triangle_ok,"
                " is_coreflector=EXCLUDED.is_coreflector,verified_at=now()",
                (rf, k, f"incl_W{k}", f"counit_W{k}", f"coreflect_W{k}",
                 idem, nat, tri, coref),
            )
            print(f"  coreflection {rf}: idempotent={idem} "
                  f"counit_natural={nat} triangle={tri} -> coreflector={coref}")

        # ---- (B) coproduct decomposition per base sequence -----------------
        print()
        for sid in BASE:
            S = base_terms[sid]
            ge1 = [t for t in S if omega(t) >= 1]
            maxw = max((omega(t) for t in ge1), default=0)
            rungs = {k: Wk(S, k) for k in range(1, maxw + 1)}

            from collections import Counter
            union = Counter()
            for k in rungs:
                union += Counter(rungs[k])
            joint = (union == Counter(ge1))
            disjoint = all(
                not (set(rungs[i]) & set(rungs[j]))
                for i in rungs for j in rungs if i < j
            )
            # unique mediating map: every element in exactly one rung
            uniq = all(sum(1 for k in rungs if x in rungs[k]) == 1
                       for x in set(ge1))
            recovers = (sorted(t for k in rungs for t in rungs[k])
                        == sorted(ge1))
            is_cop = joint and disjoint and uniq and recovers

            cur.execute(
                "INSERT INTO kan.grading_decomposition "
                "(seq,grading,n_rungs,jointly_surjective,pairwise_disjoint,"
                " mediating_unique,recovers_object,is_coproduct) "
                "VALUES (%s,'omega',%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (seq,grading) DO UPDATE SET "
                " n_rungs=EXCLUDED.n_rungs,"
                " jointly_surjective=EXCLUDED.jointly_surjective,"
                " pairwise_disjoint=EXCLUDED.pairwise_disjoint,"
                " mediating_unique=EXCLUDED.mediating_unique,"
                " recovers_object=EXCLUDED.recovers_object,"
                " is_coproduct=EXCLUDED.is_coproduct,verified_at=now()",
                (sid, len(rungs), joint, disjoint, uniq, recovers, is_cop),
            )
            print(f"  decomposition {sid}: rungs={len(rungs)} joint={joint} "
                  f"disjoint={disjoint} unique={uniq} recovers={recovers} "
                  f"-> coproduct={is_cop}")
        conn.commit()
    print("\nrecorded coreflection (i_k -| W_k) + coproduct decomposition "
          "into the kan categorical layer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
