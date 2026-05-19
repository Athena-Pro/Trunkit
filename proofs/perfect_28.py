#!/usr/bin/env python3
"""External proof-checker artifact for the formal cert tier.

Independently verifies that 28 is a perfect number by recomputing the sum of
its proper divisors from first principles. Deliberately DB-independent: its
trust root is this self-contained computation plus this file's sha256, NOT the
calx database. Exit 0 iff the property holds; nonzero otherwise.

This stands in for a TEL/Lean/Agda artifact — swapping `kind` and
`checker_cmd` in cert.artifact is the only change needed for those.
"""

import sys

N = 28


def proper_divisor_sum(n: int) -> int:
    return sum(d for d in range(1, n) if n % d == 0)


def main() -> int:
    s = proper_divisor_sum(N)
    perfect = s == N
    print(f"proper_divisor_sum({N}) = {s}; perfect = {perfect}")
    return 0 if perfect else 1


if __name__ == "__main__":
    sys.exit(main())
