"""Greedy self-syzygy expansion over the recursively-defined corpus.

For each sequence, expand every term a_n in its OWN descending predecessors
(a_{n-1},...,a_1,a_0): q_k=floor(r/a_k), r-=q_k*a_k. a_0=1 is the F_1 closer.
Relative => no window: explosive sequences handled in full. Records the
leading-digit string, termination, faithful reconstruction, and the
bounded-leading-digit <=> geometric-growth dichotomy ("the crack").

Tables kan.self_syzygy[_term]. Proof: proofs/self_syzygy.py. Idempotent.
"""

from __future__ import annotations

import os
import sys

import psycopg

PG_DSN = os.environ.get(
    "CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk"
)
# the recursively-defined ("network-constructed") corpus
RECURSIVE = ["A000108", "A000110", "A001006", "A000045", "A000142"]
NAME = {
    "A000108": "Catalan", "A000110": "Bell", "A001006": "Motzkin",
    "A000045": "Fibonacci", "A000142": "Factorial",
}


def greedy_expand(a_n: int, basis_desc: list[int]):
    """basis_desc = [a_{n-1}, ..., a_1, a_0] descending by index (values may
    repeat). Returns (digits, final_remainder, nonzero_count)."""
    r = a_n
    digits = []
    for b in basis_desc:
        if b <= 0:
            digits.append(0)
            continue
        q = r // b
        digits.append(q)
        r -= q * b
    return digits, r, sum(1 for d in digits if d > 0)


def main() -> int:
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO kan.functor (name,src_category,tgt_category,description) "
            "VALUES ('selfsyz','seq','seq','greedy self-syzygy: a_n |-> its "
            "digit string over its own descending predecessors (F_1 closes)') "
            "ON CONFLICT (name) DO NOTHING"
        )
        cur.execute("DELETE FROM kan.self_syzygy_term")
        cur.execute("DELETE FROM kan.self_syzygy")

        for sid in RECURSIVE:
            cur.execute(
                "SELECT term FROM kan.sequence_terms WHERE seq_id=%s ORDER BY idx",
                (sid,),
            )
            a = [int(r[0]) for r in cur.fetchall()]
            if len(a) < 3:
                continue
            a0_is_one = (a[0] == 1)
            terminates = True
            reconstructs = True
            leads = []
            for n in range(1, len(a)):
                basis = a[n - 1::-1]                 # [a_{n-1},...,a_0]
                digits, rem, nz = greedy_expand(a[n], basis)
                # reconstruction: sum q_k * a_k (a_k for k=n-1..0)
                recon = sum(d * b for d, b in zip(digits, basis))
                ok = (recon == a[n])
                if rem != 0:
                    terminates = False
                if not ok:
                    reconstructs = False
                lead = digits[0]                      # q_{n-1}=floor(a_n/a_{n-1})
                leads.append(lead)
                cur.execute(
                    "INSERT INTO kan.self_syzygy_term "
                    "(seq,n,lead_digit,nonzero_digits,final_remainder,"
                    " reconstructs_ok) VALUES (%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (seq,n) DO UPDATE SET "
                    " lead_digit=EXCLUDED.lead_digit,"
                    " nonzero_digits=EXCLUDED.nonzero_digits,"
                    " final_remainder=EXCLUDED.final_remainder,"
                    " reconstructs_ok=EXCLUDED.reconstructs_ok",
                    (sid, n, str(lead), nz, str(rem), ok),
                )

            # eventual leading digit: constant over the whole tail?
            tail = leads[len(leads) // 2:]            # second half
            bounded = len(set(tail)) == 1
            eventual = tail[0] if bounded else None
            gclass = "geometric" if bounded else "super-exponential"
            head = ",".join(str(x) for x in leads[:16])
            cur.execute(
                "INSERT INTO kan.self_syzygy "
                "(seq,n_terms,a0_is_one,terminates,reconstructs,"
                " leading_digits,eventual_lead,bounded_lead,growth_class) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (seq) DO UPDATE SET n_terms=EXCLUDED.n_terms,"
                " a0_is_one=EXCLUDED.a0_is_one,terminates=EXCLUDED.terminates,"
                " reconstructs=EXCLUDED.reconstructs,"
                " leading_digits=EXCLUDED.leading_digits,"
                " eventual_lead=EXCLUDED.eventual_lead,"
                " bounded_lead=EXCLUDED.bounded_lead,"
                " growth_class=EXCLUDED.growth_class,verified_at=now()",
                (sid, len(a), a0_is_one, terminates, reconstructs,
                 head, eventual, bounded, gclass),
            )
            tag = (f"-> eventual {eventual}" if bounded
                   else "-> UNBOUNDED (super-exp)")
            print(f"  {sid} {NAME[sid]:<10s} term/recon={terminates}/{reconstructs}"
                  f"  lead head=[{head}] {tag}")
        conn.commit()

    print("\n  the crack: bounded leading digit <=> finite geometric growth")
    return 0


if __name__ == "__main__":
    sys.exit(main())
