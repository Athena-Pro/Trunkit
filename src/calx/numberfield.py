"""Exact arithmetic in Q(alpha), for the comb geometry layer (112).

Pure-Python mirror of the nf_* functions in `112_comb_geometry.sql`, so a
consumer can re-derive a squared distance from a carried configuration with no
database, and so the two statements of the arithmetic can be tested against
each other. No third-party dependency.

WHY SQUARED DISTANCES AND NOT DISTANCES. The unit-distance condition is
|p - q|^2 = 1, which is a polynomial in the coordinates: no square root is ever
taken, so the metric introduces no irrationality of its own and the whole
computation stays inside whatever field the coordinates live in. That is what
makes exact geometry a bounded problem here rather than a symbolic-algebra
project. A "distance" is never computed, only ever a squared one, and the API
says so.

THE REPRESENTATION. alpha is a root of a monic minimal polynomial m of degree
d, given as integer coefficients in ASCENDING order, so m(x) = x^2 - 3 is
(-3, 0, 1). A field element is a rational vector over the power basis
1, alpha, ..., alpha^(d-1): integer numerators plus one common positive
denominator, kept in lowest terms so that equal elements have equal
representations. Because the power basis is a basis, that canonical form is
unique -- which is what lets equality be a comparison of two tuples rather than
a subtraction and a zero test.

Q ITSELF IS THE DEGREE-1 CASE. Take m(x) = x, i.e. (0, 1): d = 1, the basis is
{1}, and no reduction ever fires. Rational and algebraic coordinates therefore
run the same code path rather than needing a special case, which is one fewer
place for the two to disagree.

Floats appear nowhere in this module, by construction: every operation is on
Python ints. A configuration whose coordinates came from a numerical optimiser
is still storable, but it is declared `float_heuristic` at the ledger boundary
and step 94's shield stops it ever recording a valid certificate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import gcd

__all__ = [
    "Elem",
    "add",
    "mul",
    "rational",
    "reduce_mod",
    "sq_distance",
    "sub",
]


def _check_min_poly(min_poly: Sequence[int]) -> int:
    """Return the degree d, rejecting anything the reduction cannot handle."""
    if len(min_poly) < 2:
        raise ValueError("a minimal polynomial needs degree >= 1")
    if min_poly[-1] != 1:
        raise ValueError(
            f"minimal polynomial must be monic; leading coefficient is {min_poly[-1]}"
        )
    return len(min_poly) - 1


@dataclass(frozen=True)
class Elem:
    """An element of Q(alpha): num / den over the power basis, in lowest terms.

    Build with Elem.of(); the constructor does not normalise, so that
    round-tripping a canonical form never re-does the work.
    """

    num: tuple[int, ...]
    den: int

    @staticmethod
    def of(num: Sequence[int], den: int = 1) -> Elem:
        if den == 0:
            raise ValueError("zero denominator")
        if any(not isinstance(c, int) or isinstance(c, bool) for c in num):
            raise ValueError("coefficients must be integers -- no floats in this field")
        if not isinstance(den, int) or isinstance(den, bool):
            raise ValueError("denominator must be an integer")
        vals = list(num)
        if den < 0:                      # the sign lives in the numerator
            vals = [-c for c in vals]
            den = -den
        g = den
        for c in vals:
            g = gcd(g, abs(c))
        if g > 1:
            vals = [c // g for c in vals]
            den //= g
        return Elem(tuple(vals), den)

    def widen(self, d: int) -> Elem:
        """Pad to d basis coefficients. Never truncates."""
        if len(self.num) > d:
            raise ValueError(f"element has {len(self.num)} coefficients, wider than {d}")
        return Elem(self.num + (0,) * (d - len(self.num)), self.den)

    def is_rational(self) -> bool:
        return all(c == 0 for c in self.num[1:])

    def as_fraction(self) -> tuple[int, int]:
        """(numerator, denominator) -- only meaningful when is_rational()."""
        if not self.is_rational():
            raise ValueError("element is not rational")
        return (self.num[0] if self.num else 0), self.den


def rational(numerator: int, denominator: int = 1, degree: int = 1) -> Elem:
    """The rational numerator/denominator as an element of a degree-d field."""
    return Elem.of([numerator] + [0] * (degree - 1), denominator)


def reduce_mod(coeffs: Sequence[int], min_poly: Sequence[int]) -> list[int]:
    """Reduce a polynomial of any degree modulo a monic min_poly.

    Works top down: x^k for k >= d is rewritten using x^d = -(m_0 + ... +
    m_{d-1} x^{d-1}), shifted up by k - d. Exact integer arithmetic; monicity
    is what keeps it division-free.
    """
    d = _check_min_poly(min_poly)
    out = list(coeffs) + [0] * max(0, d - len(coeffs))
    for k in range(len(out) - 1, d - 1, -1):
        c = out[k]
        if c == 0:
            continue
        out[k] = 0
        for j in range(d):
            out[k - d + j] -= c * min_poly[j]
    return out[:d]


def add(a: Elem, b: Elem, min_poly: Sequence[int]) -> Elem:
    d = _check_min_poly(min_poly)
    x, y = a.widen(d), b.widen(d)
    return Elem.of(
        [x.num[i] * y.den + y.num[i] * x.den for i in range(d)], x.den * y.den
    )


def sub(a: Elem, b: Elem, min_poly: Sequence[int]) -> Elem:
    d = _check_min_poly(min_poly)
    x, y = a.widen(d), b.widen(d)
    return Elem.of(
        [x.num[i] * y.den - y.num[i] * x.den for i in range(d)], x.den * y.den
    )


def mul(a: Elem, b: Elem, min_poly: Sequence[int]) -> Elem:
    """Convolve, then reduce. The only place the field structure is used."""
    d = _check_min_poly(min_poly)
    x, y = a.widen(d), b.widen(d)
    conv = [0] * (2 * d - 1)
    for i, xi in enumerate(x.num):
        if xi:
            for j, yj in enumerate(y.num):
                conv[i + j] += xi * yj
    return Elem.of(reduce_mod(conv, min_poly), x.den * y.den)


def sq_distance(
    p: Sequence[Elem], q: Sequence[Elem], min_poly: Sequence[int] = (0, 1)
) -> Elem:
    """|p - q|^2 for two points given coordinate-wise. Never takes a root.

    Defaults to m(x) = x, i.e. plain rational coordinates.
    """
    if len(p) != len(q):
        raise ValueError("points differ in dimension")
    if not p:
        raise ValueError("a point needs at least one coordinate")
    d = _check_min_poly(min_poly)
    total = Elem.of([0] * d, 1)
    for pi, qi in zip(p, q, strict=True):
        diff = sub(pi, qi, min_poly)
        total = add(total, mul(diff, diff, min_poly), min_poly)
    return total
