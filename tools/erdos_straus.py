"""Erdős–Straus conjecture search: 4/n = 1/a + 1/b + 1/c.

Uses trunkit's check_unit_fraction kernel to verify every witness found.
The conjecture (open for primes) says this decomposition always exists for n >= 2.
"""
from __future__ import annotations

import math
import sys
from fractions import Fraction

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))
from calx.kernel import check_unit_fraction


def _search_3term(n: int, max_denom: int = 10_000_000) -> tuple[int, int, int] | None:
    """Find (a, b, c) with 1/a + 1/b + 1/c == 4/n, a <= b <= c, all positive."""
    target = Fraction(4, n)

    # a ranges: 1/a >= 4/(3n)  => a <= 3n/4; also 1/a <= 4/n => a >= n/4
    a_lo = math.ceil(n / 4)
    a_hi = math.floor(3 * n / 4)

    for a in range(a_lo, min(a_hi, max_denom) + 1):
        rem1 = target - Fraction(1, a)
        if rem1 <= 0:
            continue
        # b >= a (enforce ordering), 1/b <= rem1 => b >= 1/rem1
        b_lo = max(a, math.ceil(1 / rem1))
        # 1/b >= rem1/2 (b gets at least half the rest) => b <= 2/rem1
        b_hi = math.floor(2 / rem1)
        for b in range(b_lo, min(b_hi, max_denom) + 1):
            rem2 = rem1 - Fraction(1, b)
            if rem2 <= 0:
                continue
            # need rem2 == 1/c for integer c
            if rem2.numerator == 1:
                c = rem2.denominator
                if c >= b:
                    return (a, b, c)
    return None


def _verify(n: int, a: int, b: int, c: int) -> dict:
    witness = {
        "schema": "unit_fraction",
        "target": f"4/{n}",
        "denominators": [a, b, c],
    }
    ok, evidence = check_unit_fraction(witness)
    return {"verdict": ok, "evidence": evidence}


def run(n_max: int = 1000, primes_only: bool = False) -> None:
    def is_prime(k: int) -> bool:
        if k < 2:
            return False
        if k % 2 == 0:
            return k == 2
        return all(k % i != 0 for i in range(3, math.isqrt(k) + 1, 2))

    failures: list[int] = []
    total = 0

    for n in range(2, n_max + 1):
        if primes_only and not is_prime(n):
            continue
        total += 1
        trip = _search_3term(n)
        if trip is None:
            failures.append(n)
            print(f"  n={n:6d}  NO WITNESS FOUND (search exhausted)")
            continue

        a, b, c = trip
        result = _verify(n, a, b, c)
        ok = result["verdict"]
        tag = "OK" if ok else "KERNEL_REJECT"
        label = "prime" if is_prime(n) else "composite"
        print(f"  n={n:6d} [{label:9s}]  4/{n} = 1/{a}+1/{b}+1/{c}  -> {tag}")
        if not ok:
            failures.append(n)

    print()
    print(f"Checked {total} values of n up to {n_max}"
          + (" (primes only)" if primes_only else "") + ".")
    if failures:
        print(f"FAILURES / no-witness: {failures}")
    else:
        print("Conjecture holds for all tested n — every decomposition verified by kernel.")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-max", type=int, default=200,
                    help="Check 4/n for n=2..N_MAX (default 200)")
    ap.add_argument("--primes-only", action="store_true",
                    help="Only check prime n (the hard case)")
    ap.add_argument("--single", type=int, metavar="N",
                    help="Check a single n and show full kernel evidence")
    args = ap.parse_args()

    if args.single is not None:
        n = args.single
        trip = _search_3term(n)
        if trip is None:
            print(f"No 3-term decomposition found for n={n} within search bounds.")
        else:
            a, b, c = trip
            print(f"4/{n} = 1/{a} + 1/{b} + 1/{c}")
            r = _verify(n, a, b, c)
            print(f"Kernel verdict: {r['verdict']}")
            import json
            print(json.dumps(r["evidence"], indent=2))
    else:
        run(n_max=args.n_max, primes_only=args.primes_only)
