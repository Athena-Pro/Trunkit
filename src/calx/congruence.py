"""Chinese Remainder Theorem certificates (cert congruence layer).

Pure-Python mirror of the SQL in `101_cert_congruence.sql`, so the two agree
and the math is unit-testable without a database. No third-party dependency —
the calx core stays psycopg-only.

A congruence certificate is a system of congruences

    x ≡ r_1 (mod m_1), x ≡ r_2 (mod m_2), ..., x ≡ r_k (mod m_k)

with pairwise coprime moduli, together with a claimed solution x. Verifying
the certificate reconstructs the unique solution in [0, M) — M = product(m_i)
— via CRT and compares it to the claimed x. Exact integer arithmetic
throughout; a non-coprime modulus pair (no inverse exists) refutes rather
than raising, same stance as a vanishing leading coefficient in the
recurrence layer.
"""

from __future__ import annotations

from collections.abc import Sequence


def ext_gcd(a: int, b: int) -> tuple[int, int, int]:
    """Extended Euclidean algorithm: returns (g, s, t) with a*s + b*t = g = gcd(a, b)."""
    old_r, r = a, b
    old_s, s = 1, 0
    old_t, t = 0, 1
    while r != 0:
        q = old_r // r
        old_r, r = r, old_r - q * r
        old_s, s = s, old_s - q * s
        old_t, t = t, old_t - q * t
    return old_r, old_s, old_t


def mod_inverse(a: int, m: int) -> int:
    """Modular inverse of a mod m. Raises ValueError if gcd(a, m) != 1."""
    g, s, _ = ext_gcd(a % m, m)
    if g != 1:
        raise ValueError(f"gcd({a}, {m}) = {g} != 1; inverse does not exist")
    return s % m


def crt_combine(a: int, m: int, b: int, n: int) -> tuple[int, int]:
    """Pairwise CRT step: merges (a mod m, b mod n) into one congruence mod m*n."""
    inv_m = mod_inverse(m, n)
    mn = m * n
    x = (a + m * (((b - a) * inv_m) % n)) % mn
    return x, mn


def crt(remainders: Sequence[int], moduli: Sequence[int]) -> int:
    """Full CRT over k congruences. Returns the unique x in [0, product(moduli)).

    Raises ValueError if the arrays disagree in length, are empty, contain a
    non-positive modulus, or the moduli are not pairwise coprime.
    """
    if len(remainders) != len(moduli):
        raise ValueError("remainders and moduli must be the same length")
    if not moduli:
        raise ValueError("need at least one congruence")
    if any(m <= 0 for m in moduli):
        raise ValueError("moduli must be positive")
    x, m = remainders[0] % moduli[0], moduli[0]
    for r, mod in zip(remainders[1:], moduli[1:], strict=True):
        x, m = crt_combine(x, m, r % mod, mod)
    return x


def matches(remainders: Sequence[int], moduli: Sequence[int], x: int) -> bool:
    """True iff x is the unique CRT solution of the congruence system.

    Non-coprime moduli, mismatched array lengths, or a wrong x all -> False
    (never raises — mirrors cert.congruence_matches).
    """
    try:
        solution = crt(remainders, moduli)
    except ValueError:
        return False
    modulus = 1
    for m in moduli:
        modulus *= m
    return solution == x % modulus
