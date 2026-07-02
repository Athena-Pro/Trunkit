"""arith_check — verify a numeric claim by EXACT recomputation.

A calculator, not a solver: integers and rationals via Fraction; pi/e via
rational INTERVALS (Fraction endpoints), so there is zero floating-point
nondeterminism; units are first-class (dimensional consistency + conversion).

Verdicts:
  valid       relation provably holds under exact / interval arithmetic
  refuted     relation provably fails (mismatch, outside a rigorous interval,
              or dimensional mismatch)
  unverified  the kernel cannot decide deterministically: unsupported symbol,
              non-portable raw float, or an interval too wide to separate the
              relation (NOT refuted)

Witness AST (JSON nodes):
  {"int":"123"} | {"rat":["1","3"]} | {"dec":"6.2832"} | {"qty":["3","mile/hour"]}
  {"const":"pi"|"e"} | {"op":"+|-|*|/|^","args":[node,...]}
Claim:
  {"expr":node, "relation":"=|<|<=|>|>=|~", "claimed":node, "tol":{"abs":"0.001"}?}

Kernel-dispatch witness (calx.kernel schema "arith_check"): the claim fields
plus {"schema": "arith_check"}. Spec: docs/methods/arith_check_spec.md.
"""

from __future__ import annotations

from fractions import Fraction as F
from typing import Any

PI_LO = F("3.141592653589793238462643383279")
PI_HI = F("3.141592653589793238462643383280")
E_LO = F("2.718281828459045235360287471352")
E_HI = F("2.718281828459045235360287471353")

BASE = ["m", "kg", "s", "A", "K", "mol", "cd"]

UNITS = {
    "m":    (F(1),                  (1, 0, 0, 0, 0, 0, 0)),
    "km":   (F(1000),               (1, 0, 0, 0, 0, 0, 0)),
    "cm":   (F(1, 100),             (1, 0, 0, 0, 0, 0, 0)),
    "mile": (F(1609344, 1000),      (1, 0, 0, 0, 0, 0, 0)),
    "ft":   (F(3048, 10000),        (1, 0, 0, 0, 0, 0, 0)),
    "in":   (F(254, 10000),         (1, 0, 0, 0, 0, 0, 0)),
    "s":    (F(1),                  (0, 0, 1, 0, 0, 0, 0)),
    "min":  (F(60),                 (0, 0, 1, 0, 0, 0, 0)),
    "hour": (F(3600),               (0, 0, 1, 0, 0, 0, 0)),
    "kg":   (F(1),                  (0, 1, 0, 0, 0, 0, 0)),
    "g":    (F(1, 1000),            (0, 1, 0, 0, 0, 0, 0)),
    "lb":   (F(45359237, 100000000), (0, 1, 0, 0, 0, 0, 0)),
}


def zero_dim() -> tuple[int, ...]:
    return tuple([0] * len(BASE))


class Unverified(Exception):
    pass


class Interval:
    __slots__ = ("lo", "hi")

    def __init__(self, lo: F, hi: F):
        self.lo, self.hi = lo, hi

    @staticmethod
    def of(x):
        return x if isinstance(x, Interval) else Interval(x, x)

    def __add__(self, other):
        other = Interval.of(other)
        return Interval(self.lo + other.lo, self.hi + other.hi)

    def __sub__(self, other):
        other = Interval.of(other)
        return Interval(self.lo - other.hi, self.hi - other.lo)

    def __mul__(self, other):
        other = Interval.of(other)
        ps = [self.lo * other.lo, self.lo * other.hi,
              self.hi * other.lo, self.hi * other.hi]
        return Interval(min(ps), max(ps))

    def __truediv__(self, other):
        other = Interval.of(other)
        if other.lo <= 0 <= other.hi:
            raise Unverified("division by interval containing 0")
        ps = [self.lo / other.lo, self.lo / other.hi,
              self.hi / other.lo, self.hi / other.hi]
        return Interval(min(ps), max(ps))


class Qty:
    __slots__ = ("v", "dim")

    def __init__(self, v, dim: tuple[int, ...]):
        self.v, self.dim = v, dim

    def __add__(self, other):
        if self.dim != other.dim:
            raise ValueError("dimension mismatch in +")
        return Qty(self.v + other.v, self.dim)

    def __sub__(self, other):
        if self.dim != other.dim:
            raise ValueError("dimension mismatch in -")
        return Qty(self.v - other.v, self.dim)

    def __mul__(self, other):
        return Qty(self.v * other.v,
                   tuple(x + y for x, y in zip(self.dim, other.dim, strict=True)))

    def __truediv__(self, other):
        return Qty(self.v / other.v,
                   tuple(x - y for x, y in zip(self.dim, other.dim, strict=True)))


def parse_unit(s: str) -> tuple[F, tuple[int, ...]]:
    factor = F(1)
    dim = [0] * len(BASE)
    num, _, den = s.partition("/")
    for part, sign in ((num, 1), (den, -1)):
        for tok in filter(None, part.split("*")):
            name, _, p = tok.partition("^")
            power = int(p) if p else 1
            if name not in UNITS:
                raise Unverified(f"unknown unit '{name}'")
            f, d = UNITS[name]
            factor *= f ** (sign * power)
            for i in range(len(BASE)):
                dim[i] += sign * power * d[i]
    return factor, tuple(dim)


def combine(o: str, a, b):
    if isinstance(a, Qty) or isinstance(b, Qty):
        if not isinstance(a, Qty):
            a = Qty(a, zero_dim())
        if not isinstance(b, Qty):
            b = Qty(b, zero_dim())
    elif isinstance(a, Interval) or isinstance(b, Interval):
        a, b = Interval.of(a), Interval.of(b)
    if o == "+":
        return a + b
    if o == "-":
        return a - b
    if o == "*":
        return a * b
    return a / b


def ev(node: dict[str, Any]):
    """Evaluate one AST node to a Fraction, Interval, or Qty (exact)."""
    if "int" in node:
        return F(int(node["int"]))
    if "rat" in node:
        return F(int(node["rat"][0]), int(node["rat"][1]))
    if "dec" in node:
        return F(node["dec"])
    if "float" in node:
        raise Unverified("raw IEEE float is non-portable")
    if "qty" in node:
        factor, dim = parse_unit(node["qty"][1])
        return Qty(F(node["qty"][0]) * factor, dim)
    if "const" in node:
        c = node["const"]
        if c == "pi":
            return Interval(PI_LO, PI_HI)
        if c == "e":
            return Interval(E_LO, E_HI)
        raise Unverified(f"unknown constant '{c}'")
    if "op" in node:
        args = [ev(x) for x in node["args"]]
        o = node["op"]
        if o == "^":
            base, exp = args[0], args[1]
            if not isinstance(exp, F) or exp.denominator != 1:
                raise Unverified("non-integer exponent")
            e = int(exp)
            if e == 0:
                return F(1)
            r = base
            for _ in range(e - 1):
                r = combine("*", r, base)
            return r
        r = args[0]
        for x in args[1:]:
            r = combine(o, r, x)
        return r
    raise Unverified(f"unsupported node {list(node)}")


def to_interval(x) -> Interval:
    if isinstance(x, Interval):
        return x
    if isinstance(x, F):
        return Interval(x, x)
    if isinstance(x, Qty):
        return Interval(x.v, x.v)
    raise Unverified("uncomparable value")


def kernel_verify(claim: dict[str, Any]) -> tuple[str, str]:
    """Three-valued verdict for {"expr", "relation", "claimed", "tol"?}."""
    try:
        comp = ev(claim["expr"])
        clm = ev(claim["claimed"])
    except Unverified as u:
        return ("unverified", str(u))
    except ValueError as v:
        return ("refuted", str(v))
    rel = claim["relation"]
    if isinstance(comp, Qty) or isinstance(clm, Qty):
        if not (isinstance(comp, Qty) and isinstance(clm, Qty)):
            return ("refuted", "one side dimensionless, other has units")
        if comp.dim != clm.dim:
            return ("refuted", f"dimension mismatch {comp.dim} vs {clm.dim}")
    ci, ki = to_interval(comp), to_interval(clm)
    if rel == "~":
        tol = F(claim.get("tol", {}).get("abs", "0"))
        far_lo = ci.lo - ki.hi
        far_hi = ci.hi - ki.lo
        maxdiff = max(abs(far_lo), abs(far_hi))
        mindiff = F(0) if (far_lo <= 0 <= far_hi) else min(abs(far_lo), abs(far_hi))
        if maxdiff <= tol:
            return ("valid", f"|delta|<={tol}")
        if mindiff > tol:
            return ("refuted", f"|delta|>{tol}")
        return ("unverified", "interval straddles tolerance boundary")
    if rel == "=":
        if ci.lo == ci.hi == ki.lo == ki.hi:
            return ("valid", "exact equality")
        if ci.hi < ki.lo or ki.hi < ci.lo:
            return ("refuted", "intervals disjoint")
        return ("unverified", "intervals overlap; cannot certify equality")
    if rel == "<":
        if ci.hi < ki.lo:
            return ("valid", "<")
        return ("refuted", "not <") if ci.lo >= ki.hi else ("unverified", "overlap")
    if rel == "<=":
        if ci.hi <= ki.lo:
            return ("valid", "<=")
        return ("refuted", "not <=") if ci.lo > ki.hi else ("unverified", "overlap")
    if rel == ">":
        if ci.lo > ki.hi:
            return ("valid", ">")
        return ("refuted", "not >") if ci.hi <= ki.lo else ("unverified", "overlap")
    if rel == ">=":
        if ci.lo >= ki.hi:
            return ("valid", ">=")
        return ("refuted", "not >=") if ci.hi < ki.lo else ("unverified", "overlap")
    return ("unverified", f"unknown relation {rel}")


# ── AST construction helpers (producer side) ────────────────────────────────

def n_int(x) -> dict:
    return {"int": str(x)}


def n_rat(p, q) -> dict:
    return {"rat": [str(p), str(q)]}


def n_dec(x) -> dict:
    return {"dec": str(x)}


def n_qty(v, u) -> dict:
    return {"qty": [str(v), u]}


def n_const(c) -> dict:
    return {"const": c}


def op(o, *args) -> dict:
    return {"op": o, "args": list(args)}


def claim(expr, relation, claimed, **extra) -> dict:
    c = {"semantics": "arith/1", "expr": expr, "relation": relation, "claimed": claimed}
    c.update(extra)
    return c


# ── calx.kernel adapter (schema "arith_check") ──────────────────────────────

def check_arith_check(w: dict[str, Any]) -> tuple[bool | None, dict[str, Any]]:
    for field in ("expr", "relation", "claimed"):
        if field not in w:
            return None, {"error": f"witness missing '{field}'"}
    status, detail = kernel_verify(w)
    ok = True if status == "valid" else False if status == "refuted" else None
    return ok, {"status": status, "detail": detail, "relation": w["relation"]}
