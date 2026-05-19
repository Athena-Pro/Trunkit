"""The prime-members functor: PM : Seq -> Seq,  X |-> {atomic (omega=1) terms}.

The omega=1 stratum is not a set but a PROCESS applied to a generative
object. Modelled here as a genuine kan functor: an abstract category 'seq'
whose objects are all registered sequences, and an endofunctor
'prime_members' whose object map sends every sequence S to PM(S) (its
prime-power terms) and is the identity on its own image (idempotent).

  totality      : PM defined on every seq object
  well-typed    : every term of PM(S) has omega == 1
  idempotent    : PM(PM(S)) == PM(S)        (a projector / coreflection)
  fixed points  : PM(S) == S iff every term of S is a prime power
  coherence     : PM(succ-kernel) reproduces the canonical prime powers;
                  the earlier family members were PM-images all along.

Idempotent and re-runnable. Registers PM images into the unified model and
the functor into the kan categorical layer.
"""

from __future__ import annotations

import os
import sys

import psycopg

PG_DSN = os.environ.get(
    "CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk"
)


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


def prime_members(terms: list[int]) -> list[int]:
    return [t for t in terms if omega(t) == 1]


def main() -> int:
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT seq_id FROM calx.sequences "
            "WHERE seq_id NOT LIKE 'PM\\_%' ESCAPE '\\' ORDER BY seq_id"
        )
        originals = [r[0] for r in cur.fetchall()]

        # abstract category whose objects are sequences
        cur.execute(
            "INSERT INTO kan.category (name, db_schema, description) VALUES "
            "('seq', NULL, 'Sequences as objects; PM endofunctor acts here') "
            "ON CONFLICT (name) DO NOTHING"
        )
        cur.execute(
            "INSERT INTO kan.functor (name, src_category, tgt_category, description) "
            "VALUES ('prime_members','seq','seq',"
            "'X |-> atomic (omega=1) terms of X; total idempotent endofunctor') "
            "ON CONFLICT (name) DO NOTHING"
        )

        fixed, total_pm = [], 0
        for sid in originals:
            cur.execute(
                "SELECT term FROM kan.sequence_terms WHERE seq_id=%s ORDER BY idx",
                (sid,),
            )
            terms = [int(r[0]) for r in cur.fetchall()]
            pm = prime_members(terms)
            pid = "PM_" + sid

            cur.execute(
                "INSERT INTO calx.sequences (seq_id,name,seq_type,family) "
                "VALUES (%s,%s,'derived','prime-members') "
                "ON CONFLICT (seq_id) DO UPDATE SET name=EXCLUDED.name",
                (pid, f"PrimeMembers(omega=1) of {sid}"),
            )
            cur.execute("DELETE FROM kan.sequence_terms WHERE seq_id=%s", (pid,))
            for idx, v in enumerate(pm, start=1):
                cur.execute(
                    "INSERT INTO kan.sequence_terms (seq_id,idx,term) "
                    "VALUES (%s,%s,%s)",
                    (pid, idx, int(v)),
                )

            # kan objects (src + image) and the functor object map
            for obj in (sid, pid):
                cur.execute(
                    "INSERT INTO kan.object (category,name,table_name) "
                    "VALUES ('seq',%s,NULL) ON CONFLICT (category,name) DO NOTHING",
                    (obj,),
                )
            # S |-> PM_S  ;  PM_S |-> PM_S  (idempotence in the object map)
            cur.execute(
                "INSERT INTO kan.functor_object_map (functor,src_object,tgt_object) "
                "VALUES ('prime_members',%s,%s),('prime_members',%s,%s) "
                "ON CONFLICT (functor,src_object) DO UPDATE "
                "SET tgt_object=EXCLUDED.tgt_object",
                (sid, pid, pid, pid),
            )
            total_pm += 1
            if pm == terms and terms:
                fixed.append(sid)
            tag = "  [FIXED POINT]" if (pm == terms and terms) else ""
            print(f"  PM({sid}) -> {pid}: {len(pm)}/{len(terms)} terms{tag}")
        conn.commit()

    print(f"\nfunctor 'prime_members': object map total over {total_pm} "
          f"originals (+ idempotent on images)")
    print(f"fixed points (S all prime powers): {fixed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
