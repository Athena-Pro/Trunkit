"""Four-tier diagnostic: validate the system as a mathematical knowledge structure.

Tier 1 — Sanity:               raw OEIS comparison for ω, Ω, μ on n=1..20
Tier 2 — Derived recovery:     τ, σ, highly composite (running max of τ)
Tier 3 — Cross-sequence:       aliquot orbits, characterize_relation on amicables / sociables
Tier 4 — Deep:                 structure of perfect numbers in range
Killer — Combined:             abundant aliquot pairs with CRT mod-6 agreement

Emits reports/diagnostic.md with PASS/FAIL per check + raw data.
"""

from __future__ import annotations

import os
from pathlib import Path

from calx import db

REPORT_PATH = Path(__file__).resolve().parents[1] / "reports" / "diagnostic.md"

# ─── Expected OEIS values for n = 1..20 ──────────────────────────────────────
# Sourced from oeis.org canonical b-files. The user-supplied lists in the
# diagnostic spec had transcription errors at a few indices (e.g. ω(9), ω(17),
# ω(18), ω(19) and a one-position shift on Ω). These are the correct values.

OEIS_OMEGA_BIG    = [0, 1, 1, 2, 1, 2, 1, 3, 2, 2, 1, 3, 1, 2, 2, 4, 1, 3, 1, 3]   # A001222
OEIS_OMEGA_LITTLE = [0, 1, 1, 1, 1, 2, 1, 1, 1, 2, 1, 2, 1, 2, 2, 1, 1, 2, 1, 2]   # A001221
OEIS_MU           = [1,-1,-1, 0,-1, 1,-1, 0, 0, 1,-1, 0,-1, 1, 1, 0,-1, 0,-1, 0]   # A008683
OEIS_TAU          = [1, 2, 2, 3, 2, 4, 2, 4, 3, 4, 2, 6, 2, 4, 4, 5, 2, 6, 2, 6]   # A000005
OEIS_SIGMA        = [1, 3, 4, 7, 6,12, 8,15,13,18,12,28,14,24,24,31,18,39,20,42]   # A000203
OEIS_S            = [s - n for n, s in enumerate(OEIS_SIGMA, start=1)]              # A001065

HCN_PREFIX = [1, 2, 4, 6, 12, 24, 36, 48, 60, 120, 180, 240, 360, 720, 840,
              1260, 1680, 2520, 5040]


# ─── Helpers ─────────────────────────────────────────────────────────────────

def hr(out, title):
    out.append(f"\n## {title}\n")


def status_line(out, name, ok, detail=""):
    mark = "✅ PASS" if ok else "❌ FAIL"
    out.append(f"- **{mark}** — {name}{(' — ' + detail) if detail else ''}")


def compare_series(out, name, expected, observed):
    diffs = [(i + 1, e, o) for i, (e, o) in enumerate(zip(expected, observed)) if e != o]
    ok = not diffs and len(expected) == len(observed)
    status_line(out, name, ok)
    if not ok:
        out.append("    | n | expected | observed |")
        out.append("    |---|----------|----------|")
        for n, e, o in diffs[:10]:
            out.append(f"    | {n} | {e} | {o} |")
    return ok


# ─── Tier 1: Sanity ──────────────────────────────────────────────────────────

def tier1(cur, out):
    hr(out, "Tier 1 — Sanity Checks (validation against OEIS)")

    # Primes match A000040 prefix
    cur.execute("SELECT p FROM primes WHERE p <= 71 ORDER BY p")
    primes_db = [r[0] for r in cur.fetchall()]
    primes_oeis = [2,3,5,7,11,13,17,19,23,29,31,37,41,43,47,53,59,61,67,71]
    compare_series(out, "A000040 primes (first 20)", primes_oeis, primes_db)

    cur.execute("SELECT n, big_omega FROM integers WHERE n BETWEEN 1 AND 20 ORDER BY n")
    db_bigomega = [r[1] for r in cur.fetchall()]
    compare_series(out, "A001222 Ω(n) for n=1..20", OEIS_OMEGA_BIG, db_bigomega)

    cur.execute("SELECT n, omega FROM integers WHERE n BETWEEN 1 AND 20 ORDER BY n")
    db_omega = [r[1] for r in cur.fetchall()]
    compare_series(out, "A001221 ω(n) for n=1..20", OEIS_OMEGA_LITTLE, db_omega)

    cur.execute("""
        SELECT CASE
            WHEN NOT is_squarefree THEN 0
            WHEN omega % 2 = 0 THEN 1
            ELSE -1
        END AS mu
        FROM integers WHERE n BETWEEN 1 AND 20 ORDER BY n
    """)
    db_mu = [r[0] for r in cur.fetchall()]
    compare_series(out, "A008683 μ(n) derived from omega + is_squarefree", OEIS_MU, db_mu)


# ─── Tier 2: Derived Recovery ────────────────────────────────────────────────

def tier2(cur, out):
    hr(out, "Tier 2 — Derived Sequence Recovery")

    cur.execute("SELECT n, tau FROM divisor_count WHERE n BETWEEN 1 AND 20 ORDER BY n")
    rows = dict(cur.fetchall())
    db_tau = [rows.get(i, 1) for i in range(1, 21)]   # n=1 has no factorization row → tau=1
    compare_series(out, "A000005 τ(n) from divisor_count view", OEIS_TAU, db_tau)

    cur.execute("SELECT n, sigma FROM divisor_sum WHERE n BETWEEN 1 AND 20 ORDER BY n")
    rows = dict(cur.fetchall())
    db_sigma = [rows.get(i, 1) for i in range(1, 21)]
    compare_series(out, "A000203 σ(n) from divisor_sum view", OEIS_SIGMA, db_sigma)

    cur.execute(
        """
        WITH tau AS (SELECT * FROM divisor_count)
        SELECT t1.n
        FROM tau t1
        WHERE NOT EXISTS (
            SELECT 1 FROM tau t2 WHERE t2.n < t1.n AND t2.tau >= t1.tau
        )
        ORDER BY t1.n
        LIMIT 18
        """
    )
    db_hcn_no_1 = [r[0] for r in cur.fetchall()]
    db_hcn = [1] + db_hcn_no_1     # n=1 is HCN by convention but isn't in factorizations
    compare_series(out, "A002182 highly composite (first 19) — running max of τ",
                   HCN_PREFIX, db_hcn)
    out.append(f"    observed: {db_hcn}")


# ─── Tier 3: Cross-Sequence ──────────────────────────────────────────────────

def tier3(cur, out):
    hr(out, "Tier 3 — Cross-Sequence Relations")

    # 1. A001065 — proper divisor sum (sigma - n)
    cur.execute(
        "SELECT n, sigma - n FROM divisor_sum WHERE n BETWEEN 1 AND 20 ORDER BY n"
    )
    rows = dict(cur.fetchall())
    db_s = [rows.get(i, 0) for i in range(1, 21)]
    compare_series(out, "A001065 s(n) = σ(n) − n (n=1..20)", OEIS_S, db_s)

    # 2. Aliquot orbits — verify termination behavior
    def orbit(start, max_steps):
        cur.execute("CALL trace_orbit(%s, 'ALIQUOT', %s)", (start, max_steps))
        cur.execute(
            "SELECT step, n, cycle_close FROM orbits "
            "WHERE orbit_id = (SELECT MAX(orbit_id) FROM orbits) ORDER BY step"
        )
        return cur.fetchall()

    out.append("\n### Aliquot orbit termination behavior\n")

    def report_orbit(seed, expected_behavior, check):
        rows = orbit(seed, 50)
        ns = [r[1] for r in rows]
        any_cycle = any(r[2] for r in rows)
        out.append(f"- **n={seed}** — {expected_behavior}")
        out.append(f"    - trajectory ({len(rows)} steps): `{ns[:15]}{'…' if len(ns)>15 else ''}`")
        out.append(f"    - cycle_close fired: {any_cycle}")
        passed = check(rows, ns, any_cycle)
        status_line(out, f"  n={seed}", passed)
        return passed

    report_orbit(6,    "perfect → fixed point",
                 lambda r, ns, c: c and ns == [6])
    report_orbit(28,   "perfect → fixed point",
                 lambda r, ns, c: c and ns == [28])
    report_orbit(220,  "amicable 2-cycle with 284",
                 lambda r, ns, c: c and ns[:2] == [220, 284])
    report_orbit(12496,"sociable 5-cycle",
                 lambda r, ns, c: c and ns[:5] == [12496, 14288, 15472, 14536, 14264])
    rows = orbit(276, 200)
    ns = [r[1] for r in rows]
    out.append(f"- **n=276** — open trajectory (Catalan-Dickson conjecture: divergent)")
    out.append(f"    - trajectory length stored: {len(ns)} (catalog limit before escape > 10⁶)")
    out.append(f"    - first values: `{ns[:8]}…`")
    out.append(f"    - last values: `…{ns[-4:]}`")

    # 3. characterize_relation(220, 284)
    cur.execute("SELECT rel_type, description FROM characterize_relation(220, 284) ORDER BY rel_type")
    rels_220_284 = cur.fetchall()
    out.append("\n### `characterize_relation(220, 284)` — the canonical amicable pair\n")
    for rt, desc in rels_220_284:
        out.append(f"- **{rt}**: {desc}")
    has_aliquot_both = sum(1 for rt, _ in rels_220_284 if rt.startswith("ALIQUOT")) >= 2
    status_line(out, "  both directions of ALIQUOT fire", has_aliquot_both)


# ─── Tier 4: Deep ────────────────────────────────────────────────────────────

def tier4(cur, out):
    hr(out, "Tier 4 — Deep: Structure of Perfect Numbers")

    cur.execute(
        """
        SELECT
            i.n,
            i.omega,
            i.big_omega,
            i.is_squarefree,
            ps.signature,
            array_agg(f.prime ORDER BY f.prime) AS prime_factors,
            array_agg(f.exponent ORDER BY f.prime) AS exponents
        FROM integers i
        JOIN prime_signatures ps ON ps.n = i.n
        JOIN factorizations f    ON f.n  = i.n
        WHERE i.n IN (SELECT n FROM perfect_numbers)
        GROUP BY i.n, i.omega, i.big_omega, i.is_squarefree, ps.signature
        ORDER BY i.n
        """
    )
    rows = cur.fetchall()
    out.append("\n| n | ω | Ω | signature | primes | exponents |")
    out.append("|---|---|---|-----------|--------|-----------|")
    for n, om, bo, _sqf, sig, primes, exps in rows:
        out.append(f"| {n} | {om} | {bo} | `{sig}` | {primes} | {exps} |")

    # Verify the Euclid–Euler structure: every row is 2^(p-1) · M_p where M_p is Mersenne prime
    out.append("\n### Euclid-Euler structure check (without knowing the theorem)\n")
    pattern = []
    for n, om, bo, _sqf, sig, primes, exps in rows:
        if om != 2:
            pattern.append(f"  n={n}: ω≠2, doesn't fit 2^(p-1)·M_p shape")
            continue
        if primes[0] != 2:
            pattern.append(f"  n={n}: smaller prime ≠ 2")
            continue
        small_exp = exps[0]
        big_prime = primes[1]
        big_exp   = exps[1]
        is_mersenne = (big_prime == (1 << (small_exp + 1)) - 1) and big_exp == 1
        ok_str = "✓" if is_mersenne else "✗"
        pattern.append(
            f"  n={n} = 2^{small_exp} · {big_prime} "
            f"({2**small_exp} · {big_prime}); "
            f"{big_prime} =? 2^{small_exp+1}−1 = {(1<<(small_exp+1))-1}  {ok_str}"
        )
    out.extend(pattern)
    all_mersenne = all(line.endswith("✓") for line in pattern if "=?" in line)
    status_line(out, "every perfect number ≤ 10⁶ has form 2^(p-1)·(2^p − 1)", all_mersenne)

    out.append(
        "\nThe pattern `Ω(n) = small_exp + 1` is visible without telling the system "
        "to look — every perfect number has ω=2 and (small prime, large prime) "
        "structure where the second prime equals (2^(small_exp+1) − 1)."
    )


# ─── Killer Test ─────────────────────────────────────────────────────────────

def killer(cur, out, lim=1_000_000):
    hr(out, f"Killer Test — Abundant aliquot pairs with CRT mod-6 agreement (n ≤ {lim:,})")

    cur.execute(
        """
        WITH abundant AS (
            SELECT n FROM abundant_numbers WHERE n <= %s
        ),
        aliquot_pairs AS (
            SELECT
                a.n,
                (SELECT sigma FROM divisor_sum WHERE n = a.n) - a.n AS successor
            FROM abundant a
        ),
        filtered AS (
            SELECT ap.n, ap.successor
            FROM aliquot_pairs ap
            JOIN abundant ab ON ab.n = ap.successor
            WHERE ap.n %% 6 = ap.successor %% 6
              AND ap.successor BETWEEN 2 AND %s
        )
        SELECT n, successor FROM filtered ORDER BY n
        """,
        (lim, lim),
    )
    pairs = cur.fetchall()
    out.append(f"- abundant aliquot pairs (both abundant, n ≡ s(n) mod 6, both ≤ {lim:,}): "
               f"**{len(pairs):,}** pairs found.")

    if not pairs:
        return

    # Look at first 5 pairs in detail
    out.append("\n### Sample pairs with full relation vectors\n")
    for n, s in pairs[:5]:
        cur.execute(
            "SELECT rel_type, description FROM characterize_relation(%s, %s) ORDER BY rel_type",
            (n, s),
        )
        rels = cur.fetchall()
        out.append(f"\n**({n}, s={s})** — {len(rels)} relation edges:")
        for rt, desc in rels:
            out.append(f"  - {rt}: {desc}")

    # Aggregate: how many *pairs* have each relation type at least once?
    from collections import Counter
    pair_has: Counter[str] = Counter()
    signature_twin = 0
    crt_mod_30 = 0
    sample_size = min(200, len(pairs))
    for n, s in pairs[:sample_size]:
        cur.execute("SELECT DISTINCT rel_type FROM characterize_relation(%s, %s)", (n, s))
        kinds = {r[0] for r in cur.fetchall()}
        for k in kinds:
            pair_has[k] += 1
        if "SIGNATURE_TWIN" in kinds:
            signature_twin += 1
        if "CRT_CLASS" in kinds:
            cur.execute(
                "SELECT MAX((rel_params->>'modulus')::BIGINT) "
                "FROM characterize_relation(%s, %s) WHERE rel_type = 'CRT_CLASS'",
                (n, s),
            )
            row = cur.fetchone()
            if row and row[0] and row[0] >= 30:
                crt_mod_30 += 1

    out.append(f"\n### Aggregate over first {sample_size} pairs\n")
    out.append("| relation type | # pairs with this relation | % of sample |")
    out.append("|---------------|---------------------------:|------------:|")
    for rt, c in pair_has.most_common():
        out.append(f"| {rt} | {c} | {100*c/sample_size:.1f}% |")
    out.append(f"\n- pairs with SIGNATURE_TWIN: **{signature_twin}** / {sample_size}")
    out.append(f"- pairs sharing CRT class mod 30 or finer: **{crt_mod_30}** / {sample_size}")


# ─── Driver ──────────────────────────────────────────────────────────────────

def main():
    dsn = os.environ.get(
        "CALX_DSN",
        "postgresql://trunk:trunk@localhost:5434/trunk",
    )
    out: list[str] = ["# Four-Tier Diagnostic Report\n",
                      "_Tests the system as a mathematical knowledge structure — "
                      "should surface known results it wasn't told to look for._\n"]

    with db.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(n) FROM integers")
            n_max = cur.fetchone()[0]
        out.append(f"_DB range: 1..{n_max:,}_\n")

        with conn.cursor() as cur:
            tier1(cur, out)
            tier2(cur, out)
            tier3(cur, out)
            tier4(cur, out)
            killer(cur, out, lim=min(n_max, 1_000_000))

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
