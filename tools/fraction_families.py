"""
tools/fraction_families.py
Solvable families for m/n = 1/a + 1/b + 1/c.

Approach
--------
1. PROVEN FAMILIES  – algebraic identities that cover infinite congruence classes,
   verified by trunkit's check_unit_fraction kernel on finitely many witnesses.
2. DISCOVERED FAMILIES – empirical residue-class search: collect solutions for
   m in 2..M_MAX, n in (m+1)..N_MAX, group by n ≡ r (mod q), fit polynomials
   to the denominators, and report any stable linear/quadratic/cubic pattern.

Polynomial detection uses finite differences on the sequence a(k), b(k), c(k)
where k = (n - r) // q runs 0, 1, 2, … within each residue class.
"""
from __future__ import annotations

import sys, math, io
# Force UTF-8 on Windows consoles that default to cp1252
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from fractions import Fraction
from itertools import combinations_with_replacement
from collections import defaultdict
from typing import NamedTuple

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))
from calx.kernel import check_unit_fraction


# ---------------------------------------------------------------------------
# 1.  Generic search  m/n = 1/a + 1/b + 1/c  (a ≤ b ≤ c)
# ---------------------------------------------------------------------------

def search_3term(m: int, n: int, max_denom: int = 50_000_000) -> tuple[int,int,int] | None:
    """Exhaustive search for (a,b,c) with 1/a+1/b+1/c = m/n, a ≤ b ≤ c > 0."""
    if m <= 0 or n <= 0:
        return None
    target = Fraction(m, n)
    if target >= 1:
        # a = 1 must be first
        rem = target - 1
        if rem == 0:
            return None          # exactly 1 needs two more; skip for now
        r2 = search_3term(rem.numerator, rem.denominator, max_denom)
        if r2:
            a2, b2, c2 = r2
            if a2 >= 1:
                return (1, a2, b2) if a2 != b2 else (1, a2, c2)
        return None

    # a range: 1/a ≤ m/n → a ≥ n/m; 1/a ≥ m/(3n) → a ≤ 3n/m
    a_lo = max(1, math.ceil(n / m))
    a_hi = math.floor(3 * n / m)

    for a in range(a_lo, min(a_hi, max_denom) + 1):
        rem1 = target - Fraction(1, a)
        if rem1 <= 0:
            continue
        # b ≥ a; 1/b ≤ rem1 → b ≥ 1/rem1; 1/b ≥ rem1/2 → b ≤ 2/rem1
        b_lo = max(a, math.ceil(1 / rem1))
        b_hi = math.floor(2 / rem1)
        for b in range(b_lo, min(b_hi, max_denom) + 1):
            rem2 = rem1 - Fraction(1, b)
            if rem2 <= 0:
                continue
            if rem2.numerator == 1:
                c = rem2.denominator
                if c >= b:
                    return (a, b, c)
    return None


# ---------------------------------------------------------------------------
# 2.  Kernel verifier
# ---------------------------------------------------------------------------

def verify(m: int, n: int, denoms: list[int]) -> bool:
    w = {"schema": "unit_fraction", "target": f"{m}/{n}", "denominators": denoms}
    ok, _ = check_unit_fraction(w)
    return ok is True


# ---------------------------------------------------------------------------
# 3.  PROVEN algebraic families
#     Each entry:  (description, congruence_desc, param_fn, sample_ns)
#     param_fn(n) → (a, b, c) using a proven algebraic identity.
# ---------------------------------------------------------------------------

class Family(NamedTuple):
    m: int
    desc: str
    cong: str           # human description of congruence class
    formula: str        # formula for (a, b, c) in terms of n (or k)
    param: object       # callable n → (a,b,c)
    sample: list[int]   # representative n values to verify


PROVEN_FAMILIES: list[Family] = [

    # ── m = 4 ──────────────────────────────────────────────────────────────

    # n ≡ 0 (mod 4): k = n/4  →  4/n = 1/k = 1/2k + 1/3k + 1/6k
    Family(4, "Erdős–Straus mod-4=0",
           "n ≡ 0 (mod 4)",
           "a=n/2,  b=3n/4,  c=3n/2",
           lambda n: (n//2, 3*n//4, 3*n//2),
           [4,8,12,16,20,40,100,200,1000]),

    # n ≡ 2 (mod 4): k=(n-2)/4
    # Identity (proven below):
    #   4/n = 1/(k+1) + 1/(2k²+3k+2) + 1/((2k+1)(k+1)(2k²+3k+2))
    # where 2k²+3k+2 = (n²+2n+8)/8 and 2k+1 = n/2
    Family(4, "Erdős–Straus mod-4=2",
           "n ≡ 2 (mod 4)",
           "k=(n-2)/4; a=k+1, b=2k²+3k+2, c=(2k+1)·a·b",
           lambda n: (
               k := (n-2)//4,
               kp1 := k+1,
               b   := 2*k*k + 3*k + 2,
               (kp1, b, (2*k+1)*kp1*b)
           )[-1],
           [6,10,14,18,22,30,46,102,202]),

    # n ≡ 0 (mod 3): k=n/3
    # 4/3k = 1/k + 1/(3k) is only 2 terms; use splitting:
    # 4/n = 1/(k+1) + 1/(k(k+1)) + 1/(3k)
    Family(4, "Erdős–Straus mod-3=0",
           "n ≡ 0 (mod 3)",
           "k=n/3; a=k+1, b=k(k+1), c=3k",
           lambda n: (
               k := n//3,
               (k+1, k*(k+1), 3*k)
           )[-1],
           [3,6,9,12,15,21,33,99,201]),

    # n ≡ 1 (mod 4): k=(n-1)/4
    # 4/(4k+1) - 1/(k+1) = 3/((4k+1)(k+1))
    # If 3|(4k+1) i.e. k≡2(mod3): set j=(4k+1)/3 then rest is 1/(j(k+1))
    # General sub-case: n ≡ 1 (mod 4), using
    #   4/n = 1/⌈n/4⌉ + 1/⌈n/4⌉·(n/(n-⌈n/4⌉·... very messy.
    # Instead: known identity for n odd using n+1:
    #   4/n = 4/(n+1) + 4/(n(n+1))   [split into 2], then split 4/(n+1) further
    # Better sub-class: n ≡ 3 (mod 4), i.e. n=4k+3:
    #   4/(4k+3) = 1/(k+1) + 1/(k+1)(4k+3)/(4k+3-k-1) ... let's try:
    #   rem = 4/(4k+3) - 1/(k+1) = (4(k+1)-(4k+3))/((4k+3)(k+1)) = 1/((4k+3)(k+1))
    #   So 4/(4k+3) = 1/(k+1) + 1/((4k+3)(k+1)) — only 2 terms!
    #   For 3: split last: 1/((4k+3)(k+1)) = 1/((4k+3)(k+1)+1) + 1/((4k+3)(k+1)·((4k+3)(k+1)+1))
    #   Let D=(4k+3)(k+1). Then 4/n = 1/(k+1) + 1/(D+1) + 1/(D(D+1)).
    Family(4, "Erdős–Straus mod-4=3",
           "n ≡ 3 (mod 4)",
           "k=(n-3)/4, D=n(k+1); a=k+1, b=D+1, c=D(D+1)",
           lambda n: (
               k  := (n-3)//4,
               D  := n*(k+1),
               (k+1, D+1, D*(D+1))
           )[-1],
           [3,7,11,15,19,23,31,47,99,199]),

    # n ≡ 1 (mod 4): k=(n-1)/4
    # 4/n - 1/(k+1) = (4(k+1)-n)/(n(k+1)) = (4k+4-4k-1)/(n(k+1)) = 3/(n(k+1))
    # Need 1/b+1/c = 3/(n(k+1)).
    # Use: 3/N = 1/⌈N/3⌉ + ... sub-case N=n(k+1):
    # If 3|n(k+1): say 3|n (n=3j) → covered by mod-3 family.
    # If 3|k+1: k+1=3t → k=3t-1 → n=4k+1=12t-3 ≡ 9 (mod 12)
    #   3/(n·3t) = 1/(nt) → so 4/n = 1/(k+1) + 1/(nt) ... 2 terms only.
    # If n≡1(mod 12): n=12t+1, k=3t, k+1=3t+1, n(k+1)=(12t+1)(3t+1).
    #   3/(n(k+1)): try split 3/N = 1/⌈N/3⌉ + rem.
    #   ⌈N/3⌉ = ⌈(12t+1)(3t+1)/3⌉. (12t+1)(3t+1)=36t²+15t+1 → /3 = 12t²+5t+1/3 → ceil=12t²+5t+1.
    #   rem = 3/N - 1/(12t²+5t+1) = (3(12t²+5t+1)-N)/(N(12t²+5t+1))
    #        = (36t²+15t+3-(36t²+15t+1))/(...) = 2/(N(12t²+5t+1))
    #   Hmm, not a unit fraction. This class is hard — use the empirical result.
    # Instead use a simpler known identity for n ≡ 1 (mod 4):
    # 4/n = 2/(n) + 2/n ... no.
    # General: for any odd n, 4/n = 4/(n+1) + 4/(n(n+1)) (algebraic split).
    # 4/(n+1) is even, so easier. But this is recursive.
    # For n≡1(mod4): n+1≡2(mod4), use mod-4=2 family on (n+1), then handle the extra term.
    # This gives valid but complicated formulas. Let's just use empirical family below.

    # ── m = 2 ──────────────────────────────────────────────────────────────

    # n even: n=2k → 2/n=1/k=1/2k+1/3k+1/6k (same as m=4, n≡0 mod4 but scaled)
    Family(2, "m=2, n even",
           "n ≡ 0 (mod 2)",
           "k=n/2; a=2k, b=3k, c=6k  (i.e. a=n, b=3n/2, c=3n)",
           lambda n: (n, 3*n//2, 3*n),
           [2,4,6,8,10,20,50,100,200]),

    # n odd: n=2k+1 → 2/n: a=⌈n/2⌉=k+1. rem=2/n-1/(k+1)=(2k+2-n)/(n(k+1))=1/(n(k+1))
    # So 2/n = 1/(k+1) + 1/(n(k+1))  [2 terms].  For 3:
    # Split: 1/(n(k+1)) = 1/(n(k+1)+1) + 1/(n(k+1)(n(k+1)+1))
    # Let D = n(k+1). Then 2/n = 1/(k+1) + 1/(D+1) + 1/(D(D+1)).
    Family(2, "m=2, n odd",
           "n ≡ 1 (mod 2)",
           "k=(n-1)/2, D=n(k+1); a=k+1, b=D+1, c=D(D+1)",
           lambda n: (
               k := (n-1)//2,
               D := n*(k+1),
               (k+1, D+1, D*(D+1))
           )[-1],
           [3,5,7,9,11,15,21,99,201]),

    # ── m = 3 ──────────────────────────────────────────────────────────────

    # n ≡ 0 (mod 3): k=n/3 → 3/n=1/k. Same split as m=2,n-even.
    Family(3, "m=3, n ≡ 0 (mod 3)",
           "n ≡ 0 (mod 3)",
           "k=n/3; a=2k, b=3k, c=6k",
           lambda n: (2*(n//3), 3*(n//3), 6*(n//3)),
           [3,6,9,12,15,30,99,201]),

    # n ≡ 1 (mod 3): a=⌈n/3⌉=k+1 where n=3k+1.
    # rem = 3/n - 1/(k+1) = (3(k+1)-n)/(n(k+1)) = (3k+3-3k-1)/(n(k+1)) = 2/(n(k+1))
    # 2/(n(k+1)) = 1/b + 1/c. b=⌈n(k+1)/2⌉. If n(k+1) even:
    #   n=3k+1, k+1: n(k+1)=(3k+1)(k+1)=3k²+4k+1. Parity: if k even → 3k²+4k+1 odd; k odd → even.
    # Sub-case k odd (n≡4 mod6): n(k+1) even → b=n(k+1)/2, c=n(k+1)/2 (equal, ok if not req distinct).
    #   But problem allows repeated? The search enforces a≤b≤c (no distinctness required).
    #   b=c=n(k+1)/2 → valid as long as equal allowed (not distinctness constraint).
    # Sub-case k even (n≡1 mod6): n(k+1) odd → b=⌈n(k+1)/2⌉=(n(k+1)+1)/2
    #   rem2 = 2/(n(k+1)) - 2/(n(k+1)+1) = 2/((n(k+1))(n(k+1)+1))
    #   c = n(k+1)(n(k+1)+1)/2.
    # Unified: use 2/N = 1/⌈N/2⌉ + (2⌈N/2⌉-N)/(N·⌈N/2⌉).
    # If N even: 1/(N/2) + 0 → 2 terms (degenerate). For 3: 1/(N/2) = 1/(N/2+1) + 1/((N/2)(N/2+1))... very large.
    # Simpler: 2/N = 1/(N) + 1/(N) (repeated), which is valid.
    # Actually for our purposes the mod-3=1 case can use:
    # n=3k+1: 3/n = 1/(k+1) + 2/(n(k+1)).
    # Let P = n(k+1). Then 2/P = 1/⌈P/2⌉ + (2-⌈P/2⌉·2/P)·⌈P/2⌉/P·...
    # Use the odd/even split for P:
    # P = n(k+1). n=3k+1: P=(3k+1)(k+1).
    # If k≡0 mod 2: k=2j, n=6j+1, k+1=2j+1 (odd), P=(6j+1)(2j+1)=12j²+8j+1 (odd×odd=odd).
    #   2/P: b=(P+1)/2, c=P(P+1)/2.  → 3/n = 1/(k+1) + 1/((P+1)/2) + 1/(P(P+1)/2).
    # If k≡1 mod 2: k=2j+1, n=6j+4, k+1=2j+2=2(j+1) (even), P=(6j+4)·2(j+1)=4(3j+2)(j+1) (even).
    #   2/P: b=P/2=2(3j+2)(j+1), c=P/2 (repeated) → only 2 distinct terms. Split further.
    # This is getting messy. For the script, we'll let the empirical part handle these.

    # n ≡ 2 (mod 3): a=⌈n/3⌉=k+1 where n=3k+2.
    # rem = 3/n - 1/(k+1) = (3k+3-3k-2)/(n(k+1)) = 1/(n(k+1)).
    # So 3/n = 1/(k+1) + 1/(n(k+1))  [2 terms].
    # For 3: split 1/(n(k+1)) = 1/(n(k+1)+1) + 1/(n(k+1)(n(k+1)+1)).
    Family(3, "m=3, n ≡ 2 (mod 3)",
           "n ≡ 2 (mod 3)",
           "k=(n-2)/3, D=n(k+1); a=k+1, b=D+1, c=D(D+1)",
           lambda n: (
               k := (n-2)//3,
               D := n*(k+1),
               (k+1, D+1, D*(D+1))
           )[-1],
           [2,5,8,11,14,17,20,50,101,200]),

    # ── m = 5 ──────────────────────────────────────────────────────────────

    # n ≡ 0 (mod 5): k=n/5 → 5/n=1/k. Split: 1/2k+1/3k+1/6k.
    Family(5, "m=5, n ≡ 0 (mod 5)",
           "n ≡ 0 (mod 5)",
           "k=n/5; a=2k, b=3k, c=6k",
           lambda n: (2*(n//5), 3*(n//5), 6*(n//5)),
           [5,10,15,20,25,50,100,200]),

    # n ≡ 1 (mod 5): a=⌈n/5⌉=k+1, n=5k+1.
    # rem = 5/n - 1/(k+1) = (5k+5-5k-1)/(n(k+1)) = 4/(n(k+1)).
    # Now solve 4/N with N=n(k+1): use mod-4 families on N.
    # N=(5k+1)(k+1)=5k²+6k+1. N mod 4: 5k²+6k+1 mod 4 = k²+2k+1=(k+1)² mod 4.
    # If k≡0 mod4: N≡1 mod4 → use mod-4=1 (hard case).
    # If k≡1 mod4: N≡4≡0 mod4 → 4/N = 1/(N/2)+1/(3N/4)+1/(3N/2).
    # etc. — complex. Empirical below.

    # n ≡ 4 (mod 5): a=⌈n/5⌉=k+1, n=5k+4.
    # rem = 5/n - 1/(k+1) = (5k+5-5k-4)/(n(k+1)) = 1/(n(k+1)).
    # For 3: split. Same pattern as m=2 odd and m=3 mod-3=2.
    Family(5, "m=5, n ≡ 4 (mod 5)",
           "n ≡ 4 (mod 5)",
           "k=(n-4)/5, D=n(k+1); a=k+1, b=D+1, c=D(D+1)",
           lambda n: (
               k := (n-4)//5,
               D := n*(k+1),
               (k+1, D+1, D*(D+1))
           )[-1],
           [4,9,14,19,24,29,49,99,199]),

    # ── General "unit-remainder" family ───────────────────────────────────
    # For any m,n: if m/n - 1/⌈n/m⌉ = 1/D (unit fraction), then
    #   m/n = 1/⌈n/m⌉ + 1/(D+1) + 1/(D(D+1)).
    # This triggers when (m·⌈n/m⌉ - n) = 1, i.e. n ≡ m-1 (mod m) → n ≡ -1 (mod m).
    # Already encoded above for specific m; here as a combined family.
]


def _families_for_m(m: int) -> list[Family]:
    return [f for f in PROVEN_FAMILIES if f.m == m]


# ---------------------------------------------------------------------------
# 4.  Verify proven families
# ---------------------------------------------------------------------------

def verify_proven_families(verbose: bool = True) -> None:
    all_pass = True
    for fam in PROVEN_FAMILIES:
        if verbose:
            print(f"\n{'-'*60}")
            print(f"  FAMILY [{fam.m}]  {fam.desc}")
            print(f"  Congruence : {fam.cong}")
            print(f"  Formula    : {fam.formula}")
        fails = []
        for n in fam.sample:
            try:
                a, b, c = fam.param(n)
                ok = verify(fam.m, n, [a, b, c])
                if verbose:
                    status = "✓" if ok else "✗"
                    print(f"    n={n:5d}: {fam.m}/{n} = 1/{a}+1/{b}+1/{c}  {status}")
                if not ok:
                    fails.append(n)
                    all_pass = False
            except Exception as exc:
                if verbose:
                    print(f"    n={n:5d}: ERROR {exc}")
                fails.append(n)
                all_pass = False
        if fails and verbose:
            print(f"  FAILURES: {fails}")
    print()
    if all_pass:
        print("All proven families kernel-verified ✓")
    else:
        print("Some families FAILED kernel verification ✗")


# ---------------------------------------------------------------------------
# 5.  Polynomial-pattern discovery across residue classes
# ---------------------------------------------------------------------------

def _finite_differences(seq: list[int]) -> list[list[int]]:
    """Return iterated finite differences until constant or empty."""
    diffs = [seq]
    while len(diffs[-1]) > 1:
        d = diffs[-1]
        nxt = [d[i+1] - d[i] for i in range(len(d)-1)]
        if all(x == nxt[0] for x in nxt):
            diffs.append(nxt)
            break
        diffs.append(nxt)
        if len(diffs) > 5:
            break
    return diffs


def _poly_degree(seq: list[int], min_pts: int = 4) -> int | None:
    """Return polynomial degree (0–4) if sequence fits, else None."""
    if len(seq) < min_pts:
        return None
    diffs = _finite_differences(seq)
    for deg, d in enumerate(diffs):
        if len(d) >= 1 and all(x == d[0] for x in d):
            return deg
    return None


def _poly_formula(seq: list[int], offset: int, step: int) -> str:
    """Describe polynomial fit as a string using variable k."""
    if not seq:
        return "?"
    deg = _poly_degree(seq)
    if deg == 0:
        return f"{seq[0]}"
    if deg == 1:
        a = seq[1] - seq[0]
        b = seq[0] - a * offset // step
        # in terms of k = (n - r) / q
        return f"{a}k + {seq[0] - a*0}"   # k starts at 0
    if deg == 2:
        # second differences are constant
        d2 = seq[2] - 2*seq[1] + seq[0]
        a = d2 // 2
        b = (seq[1] - seq[0]) - a
        c = seq[0]
        return f"{a}k² + {b}k + {c}"
    if deg == 3:
        d3 = seq[3] - 3*seq[2] + 3*seq[1] - seq[0]
        return f"cubic (Δ³={d3})"
    return f"poly(deg>{deg})"


def discover_families(
    m_range: range,
    n_max: int = 300,
    mod_candidates: list[int] | None = None,
    min_pts: int = 5,
    verbose: bool = True,
) -> None:
    if mod_candidates is None:
        mod_candidates = [2, 3, 4, 5, 6, 7, 8, 10, 12, 15, 20, 24]

    for m in m_range:
        solutions: dict[int, tuple[int,int,int] | None] = {}
        if verbose:
            print(f"\n{'='*60}")
            print(f"  Searching m={m},  n = {m+1}..{n_max}")

        for n in range(m + 1, n_max + 1):
            solutions[n] = search_3term(m, n)

        found = sum(1 for v in solutions.values() if v is not None)
        missing = [n for n, v in solutions.items() if v is None]
        if verbose:
            print(f"  Solved: {found}/{n_max-m}  Missing: {missing[:10]}"
                  + (f"… ({len(missing)} total)" if len(missing) > 10 else ""))

        # Group by residue class
        for q in mod_candidates:
            # Collect data per residue r
            by_r: dict[int, list[tuple[int, int, int, int]]] = defaultdict(list)
            for n, trip in solutions.items():
                if trip is not None:
                    by_r[n % q].append((n, *trip))

            for r, pts in sorted(by_r.items()):
                if len(pts) < min_pts:
                    continue
                pts.sort()
                ns = [p[0] for p in pts]
                as_ = [p[1] for p in pts]
                bs = [p[2] for p in pts]
                cs = [p[3] for p in pts]

                # Step within residue class
                steps = [ns[i+1] - ns[i] for i in range(len(ns)-1)]
                if len(set(steps)) == 1 and steps[0] == q:
                    # Perfect arithmetic progression — try polynomial fit
                    da = _poly_degree(as_, min_pts=min_pts)
                    db = _poly_degree(bs, min_pts=min_pts)
                    dc = _poly_degree(cs, min_pts=min_pts)
                    if da is not None and db is not None and dc is not None:
                        deg_max = max(da, db, dc)
                        fa = _poly_formula(as_, r, q)
                        fb = _poly_formula(bs, r, q)
                        fc = _poly_formula(cs, r, q)
                        if verbose:
                            print(f"\n  FAMILY  m={m}, n ≡ {r} (mod {q})  "
                                  f"[poly degree ≤ {deg_max}]")
                            print(f"    a(k) = {fa}")
                            print(f"    b(k) = {fb}")
                            print(f"    c(k) = {fc}")
                            print(f"    (k = (n - {r}) / {q})")
                            print(f"    Sample: ", end="")
                            for n, a, b, c in pts[:4]:
                                ok = verify(m, n, [a, b, c])
                                print(f"n={n}→({a},{b},{c}){'✓' if ok else '✗'} ", end="")
                            print()


# ---------------------------------------------------------------------------
# 6.  CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=["proven", "discover", "both"], default="both",
                    help="proven: verify known families; discover: find empirical patterns")
    ap.add_argument("--m-max", type=int, default=5, help="Max numerator m (default 5)")
    ap.add_argument("--n-max", type=int, default=200, help="Max denominator n (default 200)")
    ap.add_argument("--quiet", action="store_true", help="Less verbose output")
    args = ap.parse_args()

    if args.mode in ("proven", "both"):
        print("=" * 60)
        print("  PROVEN ALGEBRAIC FAMILIES  (kernel-verified)")
        print("=" * 60)
        verify_proven_families(verbose=not args.quiet)

    if args.mode in ("discover", "both"):
        print()
        print("=" * 60)
        print("  DISCOVERED FAMILIES  (empirical polynomial patterns)")
        print("=" * 60)
        discover_families(
            m_range=range(2, args.m_max + 1),
            n_max=args.n_max,
            verbose=not args.quiet,
        )
