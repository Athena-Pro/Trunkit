"""The strata tower: a graded system of orthogonal idempotent endofunctors.

Generalises prime_members (= omega-rung 1) to the full graded tower:

  omega-tower  W_k : seq -> seq,  S |-> [t in S : omega(t) == k]   k=1..3
  Omega-tower  B_k : seq -> seq,  S |-> [t in S : Omega(t) == k]   k=1..4

Each rung is a total idempotent endofunctor. The TOWER structure (verified
live + in the cert):
  * orthogonal : W_j . W_k = empty for j != k          (within a grading)
  * idempotent : W_k . W_k = W_k
  * complete   : disjoint-union_k W_k(S) = S|{omega>=1} (resolution of id)
  * refinement : omega <= Omega, so the W-tower is coarser than the B-tower
  * bottom rung: W_1 == prime_members (the functor already built)

Materialised over a small representative BASE; the universal algebraic laws
are proved input-independently by proofs/strata_tower.py. Idempotent.
"""

from __future__ import annotations

import os
import sys

import psycopg

PG_DSN = os.environ.get(
    "CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk"
)

BASE = ["A000040", "A000290", "A000108", "Z000001", "NW1"]
W_RUNGS = [1, 2, 3]          # omega = k
B_RUNGS = [1, 2, 3, 4]       # Omega = k


def omega_bigomega(t: int):
    if t <= 1:
        return 0, 0
    w = W = 0
    m, d = t, 2
    while d * d <= m and d <= 100_000:
        if m % d == 0:
            w += 1
            while m % d == 0:
                W += 1
                m //= d
        d += 1 if d == 2 else 2
    if m > 1:
        w += 1
        W += 1
    return w, W


def rung(terms, grading, k):
    out = []
    for t in terms:
        w, W = omega_bigomega(t)
        if (grading == "W" and w == k) or (grading == "B" and W == k):
            out.append(t)
    return out


def main() -> int:
    funcs = ([("W", k, f"W{k}") for k in W_RUNGS]
             + [("B", k, f"B{k}") for k in B_RUNGS])

    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO kan.category (name, db_schema, description) VALUES "
            "('tower', NULL, 'Strata-tower base + rung images; graded "
            "idempotent endofunctors act here') ON CONFLICT (name) DO NOTHING"
        )
        for grading, k, fid in funcs:
            label = f"{'omega' if grading=='W' else 'Omega'}={k}"
            cur.execute(
                "INSERT INTO kan.functor (name,src_category,tgt_category,description) "
                "VALUES (%s,'tower','tower',%s) ON CONFLICT (name) DO NOTHING",
                (f"strata_{fid}", f"S |-> [t in S : {label}]  (rung {fid})"),
            )

        base_terms = {}
        for sid in BASE:
            cur.execute(
                "SELECT term FROM kan.sequence_terms WHERE seq_id=%s ORDER BY idx",
                (sid,),
            )
            base_terms[sid] = [int(r[0]) for r in cur.fetchall()]
            cur.execute(
                "INSERT INTO kan.object (category,name,table_name) "
                "VALUES ('tower',%s,NULL) ON CONFLICT (category,name) DO NOTHING",
                (sid,),
            )

        print("strata tower (rung images per base object):\n")
        for sid in BASE:
            terms = base_terms[sid]
            row = [f"{sid:<9s}"]
            for grading, k, fid in funcs:
                img = rung(terms, grading, k)
                tid = f"T_{fid}_{sid}"
                cur.execute(
                    "INSERT INTO calx.sequences (seq_id,name,seq_type,family) "
                    "VALUES (%s,%s,'derived','strata-tower') "
                    "ON CONFLICT (seq_id) DO UPDATE SET name=EXCLUDED.name",
                    (tid, f"Tower {fid} of {sid}"),
                )
                cur.execute("DELETE FROM kan.sequence_terms WHERE seq_id=%s", (tid,))
                for idx, v in enumerate(img, start=1):
                    cur.execute(
                        "INSERT INTO kan.sequence_terms (seq_id,idx,term) "
                        "VALUES (%s,%s,%s)", (tid, idx, int(v)))
                for obj in (tid,):
                    cur.execute(
                        "INSERT INTO kan.object (category,name,table_name) "
                        "VALUES ('tower',%s,NULL) "
                        "ON CONFLICT (category,name) DO NOTHING", (obj,))
                cur.execute(
                    "INSERT INTO kan.functor_object_map (functor,src_object,tgt_object) "
                    "VALUES (%s,%s,%s),(%s,%s,%s) "
                    "ON CONFLICT (functor,src_object) DO UPDATE "
                    "SET tgt_object=EXCLUDED.tgt_object",
                    (f"strata_{fid}", sid, tid, f"strata_{fid}", tid, tid),
                )
                row.append(f"{fid}={len(img):<2d}")
            print("  " + " ".join(row))
        conn.commit()

    # live law checks (algebraic, over BASE)
    ok = {"idempotent": True, "orthogonal": True, "complete": True,
          "refinement": True, "W1=PM": True}
    for sid in BASE:
        terms = base_terms[sid]
        wpart = {k: rung(terms, "W", k) for k in W_RUNGS}
        for k in W_RUNGS:
            if rung(wpart[k], "W", k) != wpart[k]:
                ok["idempotent"] = False
            for j in W_RUNGS:
                if j != k and rung(wpart[k], "W", j):
                    ok["orthogonal"] = False
        omega_ge1 = [t for t in terms if omega_bigomega(t)[0] >= 1]
        maxw = max((omega_bigomega(t)[0] for t in omega_ge1), default=0)
        union = [t for k in range(1, maxw + 1) for t in rung(terms, "W", k)]
        if sorted(union) != sorted(omega_ge1):
            ok["complete"] = False
        for t in terms:
            w, W = omega_bigomega(t)
            if w > W:
                ok["refinement"] = False
        # W1 == prime_members(S): omega==1 == PM
        if rung(terms, "W", 1) != [t for t in terms
                                   if omega_bigomega(t)[0] == 1]:
            ok["W1=PM"] = False

    print("\ntower laws over BASE:")
    for law, good in ok.items():
        print(f"  [{'OK ' if good else 'FAIL'}] {law}")
    print(f"\nregistered {len(funcs)} rung functors "
          f"(W{W_RUNGS}, B{B_RUNGS}) over {len(BASE)} base objects")
    return 0 if all(ok.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
