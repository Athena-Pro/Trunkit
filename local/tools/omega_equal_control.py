"""2×2 factorial: does the killer test's 64.5% OMEGA_EQUAL rate come from
mod-6 conditioning, both-abundant constraint, or their interaction?

Cells (all over abundant n with aliquot successor s(n) in [2, 10⁶]):
   A.  s(n) abundant     × mod-6 match     → the killer condition
   B.  s(n) abundant     × mod-6 mismatch  → isolates "both abundant" effect
   C.  s(n) NOT abundant × mod-6 match     → isolates "mod-6" effect
   D.  s(n) NOT abundant × mod-6 mismatch  → global base rate

If A ≈ C, the 64.5% is mod-6 conditioning alone (s(n) abundancy irrelevant).
If A >> C and B, the constraints interact (genuine structural finding).
If A ≈ B, the abundancy of s(n) drives it (mod-6 incidental).
"""

from __future__ import annotations

import os
from pathlib import Path

from calx import db

REPORT = Path(__file__).resolve().parents[1] / "reports" / "omega_equal_control.md"
LIM    = 1_000_000


def main():
    dsn = os.environ.get(
        "CALX_DSN",
        "postgresql://trunk:trunk@localhost:5434/trunk",
    )

    # Materialize the abundant set + aliquot pairs to avoid IN-subquery quadratic blowup.
    with db.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS _ab")
        cur.execute(
            "CREATE TEMP TABLE _ab AS SELECT n FROM abundant_numbers WHERE n <= %s",
            (LIM,),
        )
        cur.execute("CREATE UNIQUE INDEX ON _ab(n)")
        cur.execute("ANALYZE _ab")

        cur.execute("DROP TABLE IF EXISTS _ap")
        cur.execute(
            """CREATE TEMP TABLE _ap AS
               SELECT a.n, (ds.sigma - a.n) AS successor
               FROM _ab a
               JOIN divisor_sum ds ON ds.n = a.n
               WHERE (ds.sigma - a.n) BETWEEN 2 AND %s""",
            (LIM,),
        )
        cur.execute("CREATE INDEX ON _ap(successor)")
        cur.execute("ANALYZE _ap")

        cur.execute("""
            SELECT
                (ab2.n IS NOT NULL)                                       AS s_abundant,
                (ap.n % 6 = ap.successor % 6)                             AS mod6_match,
                COUNT(*)                                                  AS pair_count,
                AVG((i1.omega     = i2.omega   )::int)::FLOAT             AS omega_eq_rate,
                AVG((i1.big_omega = i2.big_omega)::int)::FLOAT            AS big_omega_eq_rate,
                AVG((i1.is_squarefree AND i2.is_squarefree)::int)::FLOAT  AS both_sqf_rate,
                AVG((i1.omega = i2.omega AND i1.big_omega = i2.big_omega)::int)::FLOAT
                                                                          AS sig_compat_rate
            FROM _ap ap
            LEFT JOIN _ab ab2 ON ab2.n = ap.successor
            JOIN integers i1 ON i1.n = ap.n
            JOIN integers i2 ON i2.n = ap.successor
            GROUP BY s_abundant, mod6_match
            ORDER BY s_abundant DESC, mod6_match DESC
        """)
        rows = cur.fetchall()

    # Re-key by (s_abundant, mod6_match)
    table = {(r[0], r[1]): r for r in rows}

    def cell(s_ab, m6):
        r = table.get((s_ab, m6))
        if r is None:
            return None
        return {
            "n":          r[2],
            "omega_eq":   r[3],
            "bigomega_eq":r[4],
            "both_sqf":   r[5],
            "sig_compat": r[6],
        }

    A = cell(True,  True)   # killer
    B = cell(True,  False)
    C = cell(False, True)
    D = cell(False, False)

    out = ["# OMEGA_EQUAL — 2×2 control experiment\n",
           f"_Population: abundant n ≤ {LIM:,} with aliquot successor s(n) ∈ [2, {LIM:,}]_  ",
           f"_Total such pairs: {sum(c['n'] for c in (A,B,C,D) if c):,}_\n",
           "Cells:\n",
           "- **A.** s(n) abundant ∧ n ≡ s(n) (mod 6) — the killer condition",
           "- **B.** s(n) abundant ∧ n ≢ s(n) (mod 6)",
           "- **C.** s(n) not abundant ∧ n ≡ s(n) (mod 6)",
           "- **D.** s(n) not abundant ∧ n ≢ s(n) (mod 6) — global base rate",
           "",
           "| cell | s(n) abundant | mod-6 match | pairs | P(ω=ω) | P(Ω=Ω) | P(both sqf) |",
           "|------|:-------------:|:-----------:|------:|------:|------:|-----------:|"]

    for label, s_ab, m6, c in [("A", True, True, A), ("B", True, False, B),
                                ("C", False, True, C), ("D", False, False, D)]:
        if c is None:
            out.append(f"| {label} | {s_ab} | {m6} | 0 | — | — | — |")
            continue
        out.append(
            f"| {label} | {s_ab} | {m6} | {c['n']:,} | "
            f"{c['omega_eq']:.3f} | {c['bigomega_eq']:.3f} | {c['both_sqf']:.3f} |"
        )

    out.append("")
    out.append("## Interpretation\n")

    def diff(x, y):
        return None if (x is None or y is None) else x - y

    if A and C:
        d_ac = A["omega_eq"] - C["omega_eq"]
        out.append(f"- **A − C (isolates 'both abundant' given mod-6 match)**: "
                   f"{A['omega_eq']:.3f} − {C['omega_eq']:.3f} = **{d_ac:+.3f}**")
    if A and B:
        d_ab = A["omega_eq"] - B["omega_eq"]
        out.append(f"- **A − B (isolates 'mod-6 match' given both abundant)**: "
                   f"{A['omega_eq']:.3f} − {B['omega_eq']:.3f} = **{d_ab:+.3f}**")
    if C and D:
        d_cd = C["omega_eq"] - D["omega_eq"]
        out.append(f"- **C − D (mod-6 main effect, controlling for 'both abundant'=False)**: "
                   f"{C['omega_eq']:.3f} − {D['omega_eq']:.3f} = **{d_cd:+.3f}**")
    if B and D:
        d_bd = B["omega_eq"] - D["omega_eq"]
        out.append(f"- **B − D (abundant main effect, controlling for mod-6=False)**: "
                   f"{B['omega_eq']:.3f} − {D['omega_eq']:.3f} = **{d_bd:+.3f}**")

    out.append("")
    out.append("If A−C ≈ 0 → the 64.5% in the killer test is from mod-6 conditioning alone.\n"
               "If A−C > 0 substantially → 'both abundant' adds structural pressure on top.\n"
               "Interaction effect = (A−B) − (C−D). If non-zero, the two constraints multiply.\n")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {REPORT}")

    # Echo to stdout for visibility
    for label, c in [("A", A), ("B", B), ("C", C), ("D", D)]:
        if c:
            print(f"  {label}: n={c['n']:>7,}  P(ω=ω)={c['omega_eq']:.3f}  "
                  f"P(Ω=Ω)={c['bigomega_eq']:.3f}")


if __name__ == "__main__":
    main()
