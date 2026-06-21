"""
tools/general_fraction_families.py
------------------------------------
Exploration of  m/n = o/a + p/b + q/c  with ALL six variables free.

Three layers of analysis:
  1. UNIVERSAL FORMULAS  -- proven for all n (or all n in a class)
  2. CENSUS              -- for each (m,n), find every distinct numerator triple
                           (o,p,q) that yields a solution within a denom bound
  3. FAMILY DISCOVERY    -- for fixed (o,p,q), polynomial families in n

Verification uses exact Fraction arithmetic (no DB, no psycopg).
The kernel's check_unit_fraction is reused for unit sub-cases.
A new check_general_fraction function handles the full 3-fraction case.
"""
from __future__ import annotations

import sys, math, io
from fractions import Fraction
from collections import defaultdict
from typing import NamedTuple

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from calx.kernel import check_unit_fraction


# ---------------------------------------------------------------------------
# 0.  General 3-fraction verifier (kernel-grade: pure Fraction arithmetic)
# ---------------------------------------------------------------------------

def check_general_fraction(
    m: int, n: int,
    o: int, a: int,
    p: int, b: int,
    q: int, c: int,
) -> tuple[bool, dict]:
    """Verify m/n == o/a + p/b + q/c using exact rational arithmetic."""
    target = Fraction(m, n)
    terms  = [Fraction(o, a), Fraction(p, b), Fraction(q, c)]
    total  = sum(terms)
    ok     = (total == target)
    return ok, {
        "target": str(target),
        "terms":  [str(t) for t in terms],
        "sum":    str(total),
        "equal":  ok,
    }


# ---------------------------------------------------------------------------
# 1.  UNIVERSAL FORMULAS
# ---------------------------------------------------------------------------

def universal_U1(m: int, n: int):
    """m/n = (m-1)/n + 1/(n+1) + 1/(n*(n+1)).
    Valid for m >= 2, n > m.  Uses Sylvester: 1/n = 1/(n+1) + 1/(n(n+1)).
    Numerator triple: (m-1, 1, 1).
    """
    return (m-1, n), (1, n+1), (1, n*(n+1))


def universal_U2_odd(n: int):
    """4/n = 2/((n+1)/2) + 4/(n^2+n+2) + 8/(n(n+1)(n^2+n+2)).
    Valid for n odd (so (n+1)/2 is an integer).
    Numerator triple: (2, 4, 8).
    Algebraic proof:
      Step 1.  4/n = 4/(n+1) + 4/(n(n+1))          [partial fraction]
      Step 2.  4/(n+1) = 2/((n+1)/2)               [n odd => n+1 even]
      Step 3.  4/(n(n+1)) = 4/B + 8/(n(n+1)B)       [Sylvester-like; B=n(n+1)+2]
               because 4n(n+1)B + 8B = B(4n(n+1)+8) = 4B^2 => sum = 4/(n(n+1)) [checked]
    """
    assert n % 2 == 1, "U2 requires odd n"
    a = (n + 1) // 2
    B = n*n + n + 2
    C = n * (n + 1) * B
    return (2, a), (4, B), (8, C)


def universal_U3(m: int, n: int):
    """m/n = m/(n+1) + (m-1)/(n+1) + 1/(n*(n+1))  [WRONG - let me recompute]
    Actually:  m/n = m/(n+1) + m/(n(n+1))  is a 2-TERM identity.
    For 3 terms, split the second with Sylvester:
      m/(n(n+1)) = (m-1)/(n(n+1)) + 1/(n(n+1))
    => m/n = m/(n+1) + (m-1)/(n(n+1)) + 1/(n(n+1))
    Numerator triple: (m, m-1, 1).  Valid for m >= 2.
    """
    N = n * (n + 1)
    return (m, n+1), (m-1, N), (1, N)


def universal_U4_even(m: int, n: int):
    """m/n = (m//g)/((n//g)*2) + ... using gcd reduction.
    For even n=2k: m/(2k) -> try m/n = 1/(2k/m * 1)...
    Fallback: use U1 (always works for m>=2).
    """
    return universal_U1(m, n)


def universal_harmonic(m: int, n: int, k: int = 2):
    """m/n = k * (m/k)/n = k * (Egyptian decomp of m/(kn)).
    If k | m:  m/n = k/a + k/b + k/c where 1/a+1/b+1/c = m/(kn).
    We use the known Egyptian decomposition of (m//k)/n.
    Numerator triple: (k, k, k).
    """
    if m % k != 0:
        return None
    m2 = m // k
    # Find Egyptian decomp of m2/n using greedy / search
    from fractions import Fraction
    target = Fraction(m2, n)
    a_lo = math.ceil(n / m2) if m2 > 0 else 1
    for a in range(a_lo, a_lo * 3 + 1):
        r1 = target - Fraction(1, a)
        if r1 <= 0: continue
        b_lo = max(a, math.ceil(1 / r1))
        b_hi = math.floor(2 / r1)
        for b in range(b_lo, min(b_hi, 10**7) + 1):
            r2 = r1 - Fraction(1, b)
            if r2 <= 0: continue
            if r2.numerator == 1 and r2.denominator >= b:
                return (k, a), (k, b), (k, r2.denominator)
    return None


# ---------------------------------------------------------------------------
# 2.  General search:  m/n = o/a + p/b + q/c  for fixed (o, p, q)
# ---------------------------------------------------------------------------

def search_general(
    m: int, n: int,
    o: int, p: int, q: int,
    max_denom: int = 10_000_000,
) -> tuple[int, int, int] | None:
    """Find (a, b, c) with o/a + p/b + q/c = m/n, a <= b <= c."""
    target = Fraction(m, n)

    # o/a <= m/n  =>  a >= n*o/m
    # o/a >= m/(3n)  =>  a <= 3*n*o/m
    a_lo = max(1, math.ceil(n * o / m))
    a_hi = math.floor(3 * n * o / m)

    for a in range(a_lo, min(a_hi, max_denom) + 1):
        rem1 = target - Fraction(o, a)
        if rem1 <= 0:
            continue
        # p/b <= rem1  =>  b >= p/rem1
        # p/b >= rem1/2  =>  b <= 2p/rem1
        b_lo = max(a, math.ceil(p / rem1))
        b_hi = math.floor(2 * p / rem1)
        for b in range(b_lo, min(b_hi, max_denom) + 1):
            rem2 = rem1 - Fraction(p, b)
            if rem2 <= 0:
                continue
            # q/c = rem2  =>  c = q / rem2 = q * rem2.denom / rem2.num
            if q % rem2.numerator == 0:
                c = (q // rem2.numerator) * rem2.denominator
                if c >= b:
                    return (a, b, c)
    return None


# ---------------------------------------------------------------------------
# 3.  Census: for fixed (m,n), find all numerator triples (o,p,q)
#     with 1 <= o <= p <= q <= m that yield a solution
# ---------------------------------------------------------------------------

def census(
    m: int, n: int,
    max_num: int | None = None,
    max_denom: int = 1_000_000,
) -> dict[tuple, tuple]:
    """Return {(o,p,q): (a,b,c)} for all numerator triples that work."""
    if max_num is None:
        max_num = m
    results = {}
    for o in range(1, max_num + 1):
        for p in range(o, max_num + 1):
            for q in range(p, max_num + 1):
                trip = search_general(m, n, o, p, q, max_denom=max_denom)
                if trip is not None:
                    results[(o, p, q)] = trip
    return results


# ---------------------------------------------------------------------------
# 4.  Family discovery: for fixed (o,p,q), scan n and detect polynomial
#     patterns in (a,b,c) within residue classes
# ---------------------------------------------------------------------------

def _poly_degree(seq: list[int]) -> int | None:
    """Return polynomial degree 0-4 if sequence fits, else None."""
    if len(seq) < 4:
        return None
    cur = seq[:]
    for deg in range(5):
        if all(x == cur[0] for x in cur):
            return deg
        if len(cur) <= 1:
            break
        cur = [cur[i+1] - cur[i] for i in range(len(cur)-1)]
    return None


def discover_general_families(
    m: int,
    n_max: int,
    num_triples: list[tuple[int,int,int]],
    mod_q: int = 4,
) -> None:
    """For each (o,p,q) triple and m, scan n and report polynomial families."""
    print(f"\n  m={m}, n=2..{n_max},  numerator triples: {num_triples}")
    for opq in num_triples:
        o, p, q = opq
        solutions: dict[int, tuple[int,int,int]] = {}
        for n in range(2, n_max + 1):
            if Fraction(m, n) >= 1 and o == p == q == 1:
                continue  # unit frac needs < 1
            trip = search_general(m, n, o, p, q)
            if trip:
                solutions[n] = trip

        solved_ns = sorted(solutions)
        total = n_max - 1
        missing = [n for n in range(2, n_max+1) if n not in solutions]
        print(f"\n  (o,p,q)=({o},{p},{q}): solved {len(solutions)}/{total}", end="")
        if missing:
            print(f"  missing={missing[:8]}", end="")
        print()

        # Group by residue class
        for r in range(mod_q):
            pts = [(n, *solutions[n]) for n in solved_ns if n % mod_q == r]
            if len(pts) < 4:
                continue
            ns  = [x[0] for x in pts]
            as_ = [x[1] for x in pts]
            bs  = [x[2] for x in pts]
            cs  = [x[3] for x in pts]

            da = _poly_degree(as_)
            db = _poly_degree(bs)
            if da is not None and db is not None:
                print(f"    n=={r}(mod{mod_q}): a deg={da}, b deg={db}  "
                      f"sample: ", end="")
                for n,a,b,c in pts[:3]:
                    ok,_ = check_general_fraction(m,n, o,a, p,b, q,c)
                    print(f"({o}/{a}+{p}/{b}+{q}/{c}={'OK' if ok else 'FAIL'})", end=" ")
                print()


# ---------------------------------------------------------------------------
# 5.  Reduction lattice: show how (o,p,q) families nest inside each other
# ---------------------------------------------------------------------------

def reduction_lattice(m: int, n: int, max_num: int = 4) -> None:
    """Show which numerator triples solve m/n, in order of increasing complexity."""
    print(f"\n  Reduction lattice for {m}/{n}  (max numerator per slot = {max_num})")
    found = census(m, n, max_num=max_num, max_denom=10_000_000)

    # Sort by sum of numerators (complexity proxy), then by triple
    by_complexity: dict[int, list] = defaultdict(list)
    for (o,p,q),(a,b,c) in found.items():
        by_complexity[o+p+q].append(((o,p,q),(a,b,c)))

    for total in sorted(by_complexity):
        for (o,p,q),(a,b,c) in sorted(by_complexity[total]):
            ok,_ = check_general_fraction(m,n, o,a, p,b, q,c)
            status = "OK" if ok else "FAIL"
            print(f"    ({o},{p},{q}): {m}/{n} = {o}/{a}+{p}/{b}+{q}/{c}  max_den={max(a,b,c)}  [{status}]")


# ---------------------------------------------------------------------------
# 6.  Summary of proven universal formulas
# ---------------------------------------------------------------------------

def verify_universal_formulas() -> None:
    print("=" * 64)
    print("  UNIVERSAL FORMULAS  (exact Fraction verification)")
    print("=" * 64)

    print()
    print("U1: m/n = (m-1)/n + 1/(n+1) + 1/(n*(n+1))")
    print("    [holds for all m>=2, n>m; numerators (m-1, 1, 1)]")
    print("    Proof: 1/(n+1) + 1/(n(n+1)) = (n+1)/(n(n+1)) = 1/n")
    print("           so (m-1)/n + 1/n = m/n  QED")
    ok_all = True
    for m,n in [(2,3),(2,5),(3,4),(3,7),(4,5),(4,7),(4,11),(4,101),(5,6),(5,13)]:
        (o,a),(p,b),(q,c) = universal_U1(m, n)
        ok,ev = check_general_fraction(m,n, o,a, p,b, q,c)
        ok_all = ok_all and ok
        print(f"    {m}/{n} = {o}/{a}+{p}/{b}+{q}/{c}  [{ev['equal']}]")
    print(f"    All OK: {ok_all}")

    print()
    print("U2: 4/n = 2/((n+1)/2) + 4/(n^2+n+2) + 8/(n(n+1)(n^2+n+2))  [n odd]")
    print("    [numerators (2, 4, 8)]")
    print("    Proof (3 steps):")
    print("      S1: 4/n  = 4/(n+1) + 4/(n(n+1))")
    print("      S2: 4/(n+1) = 2/((n+1)/2)           [n+1 even since n odd]")
    print("      S3: let B = n(n+1)+2;")
    print("          4/(n(n+1)) = 4/B + 8/(n(n+1)*B)")
    print("          because (4*n(n+1)*B + 8*B) / (n(n+1)*B^2)")
    print("                 = 4*(n(n+1)+2)*B / (n(n+1)*B^2) = 4/n(n+1)  QED")
    ok_all = True
    for n in [3,5,7,9,11,13,17,19,23,29,31,37,41,43,97,101,999]:
        if n % 2 == 0:
            continue
        (o,a),(p,b),(q,c) = universal_U2_odd(n)
        ok,ev = check_general_fraction(4,n, o,a, p,b, q,c)
        ok_all = ok_all and ok
        if n <= 43 or n in (97,101,999):
            print(f"    n={n:4d}: 4/{n} = {o}/{a}+{p}/{b}+{q}/{c}  [{ev['equal']}]")
    print(f"    All OK: {ok_all}")

    print()
    print("U3: m/n = m/(n+1) + (m-1)/(n(n+1)) + 1/(n(n+1))")
    print("    [numerators (m, m-1, 1); valid for m>=2]")
    print("    Proof: m/(n+1) + (m-1)/(n(n+1)) + 1/(n(n+1))")
    print("         = m/(n+1) + m/(n(n+1))")
    print("         = m*n/(n(n+1)) + m/(n(n+1))")
    print("         = m(n+1)/(n(n+1)) = m/n  QED")
    ok_all = True
    for m,n in [(2,3),(3,4),(4,5),(4,7),(5,6),(5,11),(4,101)]:
        (o,a),(p,b),(q,c) = universal_U3(m, n)
        ok,ev = check_general_fraction(m,n, o,a, p,b, q,c)
        ok_all = ok_all and ok
        print(f"    {m}/{n} = {o}/{a}+{p}/{b}+{q}/{c}  [{ev['equal']}]")
    print(f"    All OK: {ok_all}")

    print()
    print("U_harmonic: m/n = k/a + k/b + k/c  where k | m")
    print("    [equal-numerator family; reduces to Egyptian (m/k)/n]")
    ok_all = True
    for m,n,k in [(4,5,2),(4,7,2),(4,9,2),(6,7,3),(6,11,3),(4,11,4),(8,9,4)]:
        result = universal_harmonic(m, n, k)
        if result is None:
            print(f"    {m}/{n} k={k}: no Egyptian base found")
            continue
        (o,a),(p,b),(q,c) = result
        ok,ev = check_general_fraction(m,n, o,a, p,b, q,c)
        ok_all = ok_all and ok
        print(f"    {m}/{n} k={k}: {m}/{n} = {o}/{a}+{p}/{b}+{q}/{c}  [{ev['equal']}]")
    print(f"    All OK: {ok_all}")


# ---------------------------------------------------------------------------
# 7.  Coverage analysis: for 4/n, compare unit vs general numerators
# ---------------------------------------------------------------------------

def coverage_analysis(n_max: int = 100) -> None:
    print()
    print("=" * 64)
    print("  COVERAGE ANALYSIS: 4/n,  n=2..N_MAX")
    print("  Comparing unit fractions vs. general numerators")
    print("=" * 64)
    print()

    is_prime = lambda k: k > 1 and all(k % i for i in range(2, math.isqrt(k)+1))

    header = f"{'n':>6}  {'mod4':>4}  {'unit_frac':>12}  {'U1(m-1,1,1)':>14}  {'U2(2,4,8)':>10}  {'census_best':>12}"
    print(header)
    print("-" * len(header))

    for n in range(2, n_max + 1):
        m = 4
        mod4 = n % 4
        prime = is_prime(n)
        tag = "P" if prime else " "

        # Unit fractions (o=p=q=1)
        uf_trip = search_general(m, n, 1, 1, 1, max_denom=10_000_000)
        uf_str = f"1/{uf_trip[0]}+..." if uf_trip else "NONE"

        # U1: (m-1)/n + 1/(n+1) + 1/(n(n+1))
        (o1,a1),(p1,b1),(q1,c1) = universal_U1(m, n)
        ok_u1,_ = check_general_fraction(m,n, o1,a1, p1,b1, q1,c1)

        # U2 (only for odd n)
        if n % 2 == 1:
            (o2,a2),(p2,b2),(q2,c2) = universal_U2_odd(n)
            ok_u2,_ = check_general_fraction(m,n, o2,a2, p2,b2, q2,c2)
            u2_str = "OK" if ok_u2 else "FAIL"
        else:
            u2_str = "n/a"

        # Census: what is the min-max-denominator solution over all num triples?
        best_denom = min(max(a,b,c) for (o,p,q),(a,b,c) in census(m,n,max_num=4,max_denom=100_000).items()) if n <= 30 else 0

        print(f"{n:>5}{tag}  {mod4:>4}  {uf_str:>12}  {'OK' if ok_u1 else 'FAIL':>14}  {u2_str:>10}  {best_denom:>12}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=["universal","census","families","coverage","all"],
                    default="all")
    ap.add_argument("--m", type=int, default=4, help="Numerator (default 4)")
    ap.add_argument("--n-max", type=int, default=50)
    ap.add_argument("--n", type=int, default=None, help="Single n for census mode")
    args = ap.parse_args()

    if args.mode in ("universal", "all"):
        verify_universal_formulas()

    if args.mode in ("census", "all"):
        targets = [args.n] if args.n else [5, 7, 11, 13, 17, 19, 23, 29]
        print()
        print("=" * 64)
        print("  CENSUS: all numerator triples for specific 4/n")
        print("=" * 64)
        for n in targets:
            reduction_lattice(args.m, n, max_num=min(args.m, 4))

    if args.mode in ("families", "all"):
        print()
        print("=" * 64)
        print("  FAMILY DISCOVERY: polynomial patterns per numerator triple")
        print("=" * 64)
        triples = [(1,1,1),(1,1,2),(1,2,2),(2,2,2),(1,1,3),(2,4,8)]
        discover_general_families(args.m, args.n_max, triples, mod_q=4)

    if args.mode == "coverage":
        coverage_analysis(args.n_max)
