"""Self-shadow multiplicity rho_self over the recursively-defined corpus.

For each sequence, count EVERY non-negative representation of a_n over its
own predecessors {a_0,...,a_{n-1}} (the denumerant fiber of the relative
expansion):

    rho_self(n) = #{ (c_0,...,c_{n-1}) in Z>=0^n : SUM_k c_k a_k = a_n }

a_0=1 is the F_1 SUMMATORY/zeta operator: since the unit part is unbounded,
each representation = (units absorb the slack) + (a representation of the
slack by the non-unit parts), so exactly

    rho_self(n) = SUM_{m=0}^{a_n} rho_hat(m)

with rho_hat omitting a_0. Targets explode, so the count is windowed to the
head (a_n <= cap). Tables kan.self_shadow[_term]. Proof:
proofs/self_shadow.py. Idempotent.
"""

from __future__ import annotations

import hashlib
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
WINDOW_CAP = 60_000              # engine head window (covers a non-trivial head)


def denumerant_dp(parts: list[int], target: int) -> list[int]:
    """dp[m] = #{ tuples over `parts` (each used >=0 times) summing to m },
    for 0 <= m <= target. Unbounded-knalithon representation count."""
    dp = [0] * (target + 1)
    dp[0] = 1
    for p in parts:
        if p <= 0 or p > target:
            continue
        for m in range(p, target + 1):
            if dp[m - p]:
                dp[m] += dp[m - p]
    return dp


def main() -> int:
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO kan.functor (name,src_category,tgt_category,description) "
            "VALUES ('selfshadow','seq','seq','self-shadow: a_n |-> the count "
            "of all representations over its own predecessors; F_1 is the "
            "summatory/zeta operator') ON CONFLICT (name) DO NOTHING"
        )
        cur.execute("DELETE FROM kan.self_shadow_term")
        cur.execute("DELETE FROM kan.self_shadow")

        for sid in RECURSIVE:
            cur.execute(
                "SELECT term FROM kan.sequence_terms WHERE seq_id=%s ORDER BY idx",
                (sid,),
            )
            a = [int(r[0]) for r in cur.fetchall()]
            if len(a) < 3:
                continue
            a0_is_one = (a[0] == 1)
            all_ge1 = True
            all_ge2 = True
            f1_ok = True
            windowed = 0
            sig_rows = []

            for n in range(1, len(a)):
                target = a[n]
                in_win = target <= WINDOW_CAP
                rho_self = rho_hat_sum = None
                factored_ok = None
                if in_win:
                    windowed += 1
                    preds = a[:n]                       # [a_0,...,a_{n-1}]
                    # full denumerant (a_0 included): rho_self
                    dp_full = denumerant_dp(preds, target)
                    rho_self = dp_full[target]
                    # F_1 = zeta: rho_hat omits a_0, then SUM_{m<=a_n}
                    dp_hat = denumerant_dp(preds[1:], target)
                    rho_hat_sum = sum(dp_hat)            # cumulative 0..a_n
                    factored_ok = (rho_self == rho_hat_sum)
                    if rho_self < 1:
                        all_ge1 = False
                    if n >= 2 and rho_self < 2:
                        all_ge2 = False
                    if not factored_ok:
                        f1_ok = False
                    sig_rows.append((n, rho_self.bit_length(),
                                     rho_self % 1_000_003))
                cur.execute(
                    "INSERT INTO kan.self_shadow_term "
                    "(seq,n,target,in_window,rho_self,rho_hat_sum,factored_ok) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (seq,n) DO UPDATE SET target=EXCLUDED.target,"
                    " in_window=EXCLUDED.in_window,rho_self=EXCLUDED.rho_self,"
                    " rho_hat_sum=EXCLUDED.rho_hat_sum,"
                    " factored_ok=EXCLUDED.factored_ok",
                    (sid, n, str(target), in_win,
                     None if rho_self is None else str(rho_self),
                     None if rho_hat_sum is None else str(rho_hat_sum),
                     factored_ok),
                )

            head_sha = hashlib.sha256(repr(sig_rows).encode()).hexdigest()
            cur.execute(
                "INSERT INTO kan.self_shadow "
                "(seq,n_terms,window_cap,windowed_terms,a0_is_one,all_ge1,"
                " all_ge2_from_n2,f1_summatory_ok,head_sha) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (seq) DO UPDATE SET n_terms=EXCLUDED.n_terms,"
                " window_cap=EXCLUDED.window_cap,"
                " windowed_terms=EXCLUDED.windowed_terms,"
                " a0_is_one=EXCLUDED.a0_is_one,all_ge1=EXCLUDED.all_ge1,"
                " all_ge2_from_n2=EXCLUDED.all_ge2_from_n2,"
                " f1_summatory_ok=EXCLUDED.f1_summatory_ok,"
                " head_sha=EXCLUDED.head_sha,verified_at=now()",
                (sid, len(a), str(WINDOW_CAP), windowed, a0_is_one,
                 all_ge1, all_ge2, f1_ok, head_sha),
            )
            print(f"  {sid} {NAME[sid]:<10s} windowed={windowed:>2d} "
                  f">=1/{all_ge1} >=2/{all_ge2} F1=zeta/{f1_ok} "
                  f"sig={head_sha[:16]}")
        conn.commit()

    print("\n  F_1 is the summatory/zeta operator: "
          "rho_self(n) = SUM_{m<=a_n} rho_hat(m)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
