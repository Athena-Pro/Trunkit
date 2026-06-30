#!/usr/bin/env python3
"""
arith_check - a universal Trunkit method: verify a numeric claim by EXACT recomputation.

Why universal (not pocket):
  * Tiny deterministic kernel - a calculator, no solver. Same verdict on any model/host.
  * Compact portable witness - a typed expression AST (JSON), not natural language.
  * Three honest verdicts - valid / refuted / unverified (never guess).
  * Exact by default - integers and rationals via Fraction; transcendentals via rational
    INTERVALS (Fraction endpoints) so there is zero floating-point nondeterminism.
  * Units first-class - dimensional consistency and conversions are checked.

Verdicts:
  valid       relation provably holds under exact / interval arithmetic
  refuted     relation provably fails (mismatch, claimed outside a rigorous interval,
              or dimensional mismatch)
  unverified  kernel cannot decide deterministically: unsupported symbol, non-portable raw
              float, or an interval too wide to separate the relation. (NOT refuted.)

Witness AST (JSON nodes):
  {"int":"123"} | {"rat":["1","3"]} | {"dec":"6.2832"} | {"qty":["3","mile/hour"]}
  {"const":"pi"|"e"} | {"op":"+|-|*|/|^","args":[node,...]}
Claim:
  {"semantics":"arith/1","expr":node,"relation":"=|<|<=|>|>=|~","claimed":node,"tol":{"abs":"0.001"}?}

Trunkit mapping:
  curry  fn   arith_eval(expr) -> Value        (pure)
  claim       "<expr> <relation> <claimed>"    method = comp_sql / arith_carry
  witness     this JSON AST                     method = witness_carry
  kernel      kernel_verify(claim) re-evaluates and compares  (mirrors trunkit.kernel_verify)
"""
from fractions import Fraction as F

PI_LO = F("3.141592653589793238462643383279")
PI_HI = F("3.141592653589793238462643383280")
E_LO  = F("2.718281828459045235360287471352")
E_HI  = F("2.718281828459045235360287471353")

BASE = ["m", "kg", "s", "A", "K", "mol", "cd"]
def zero_dim(): return tuple([0] * len(BASE))
UNITS = {
    "m":   (F(1),                (1,0,0,0,0,0,0)),
    "km":  (F(1000),             (1,0,0,0,0,0,0)),
    "cm":  (F(1,100),            (1,0,0,0,0,0,0)),
    "mile":(F(1609344,1000),     (1,0,0,0,0,0,0)),
    "ft":  (F(3048,10000),       (1,0,0,0,0,0,0)),
    "in":  (F(254,10000),        (1,0,0,0,0,0,0)),
    "s":   (F(1),                (0,0,1,0,0,0,0)),
    "min": (F(60),               (0,0,1,0,0,0,0)),
    "hour":(F(3600),             (0,0,1,0,0,0,0)),
    "kg":  (F(1),                (0,1,0,0,0,0,0)),
    "g":   (F(1,1000),           (0,1,0,0,0,0,0)),
    "lb":  (F(45359237,100000000),(0,1,0,0,0,0,0)),
}

class Unverified(Exception): pass

class Interval:
    __slots__ = ("lo", "hi")
    def __init__(s, lo, hi): s.lo, s.hi = lo, hi
    @staticmethod
    def of(x): return x if isinstance(x, Interval) else Interval(x, x)
    def __add__(a, b):
        b = Interval.of(b); return Interval(a.lo + b.lo, a.hi + b.hi)
    def __sub__(a, b):
        b = Interval.of(b); return Interval(a.lo - b.hi, a.hi - b.lo)
    def __mul__(a, b):
        b = Interval.of(b); ps = [a.lo*b.lo, a.lo*b.hi, a.hi*b.lo, a.hi*b.hi]
        return Interval(min(ps), max(ps))
    def __truediv__(a, b):
        b = Interval.of(b)
        if b.lo <= 0 <= b.hi: raise Unverified("division by interval containing 0")
        ps = [a.lo/b.lo, a.lo/b.hi, a.hi/b.lo, a.hi/b.hi]
        return Interval(min(ps), max(ps))

class Qty:
    __slots__ = ("v", "dim")
    def __init__(s, v, dim): s.v, s.dim = v, dim
    def __add__(a, b):
        if a.dim != b.dim: raise ValueError("dimension mismatch in +")
        return Qty(a.v + b.v, a.dim)
    def __sub__(a, b):
        if a.dim != b.dim: raise ValueError("dimension mismatch in -")
        return Qty(a.v - b.v, a.dim)
    def __mul__(a, b): return Qty(a.v*b.v, tuple(x+y for x, y in zip(a.dim, b.dim)))
    def __truediv__(a, b): return Qty(a.v/b.v, tuple(x-y for x, y in zip(a.dim, b.dim)))

def parse_unit(s):
    factor = F(1); dim = [0]*len(BASE)
    num, _, den = s.partition("/")
    for part, sign in ((num, 1), (den, -1)):
        for tok in filter(None, part.split("*")):
            name, _, p = tok.partition("^"); p = int(p) if p else 1
            if name not in UNITS: raise Unverified("unknown unit '%s'" % name)
            f, d = UNITS[name]; factor *= f ** (sign*p)
            for i in range(len(BASE)): dim[i] += sign*p*d[i]
    return factor, tuple(dim)

def combine(o, a, b):
    if isinstance(a, Qty) or isinstance(b, Qty):
        if not isinstance(a, Qty): a = Qty(a, zero_dim())
        if not isinstance(b, Qty): b = Qty(b, zero_dim())
    elif isinstance(a, Interval) or isinstance(b, Interval):
        a, b = Interval.of(a), Interval.of(b)
    return (a+b) if o == "+" else (a-b) if o == "-" else (a*b) if o == "*" else (a/b)

def ev(node):
    if "int" in node: return F(int(node["int"]))
    if "rat" in node: return F(int(node["rat"][0]), int(node["rat"][1]))
    if "dec" in node: return F(node["dec"])
    if "float" in node: raise Unverified("raw IEEE float is non-portable")
    if "qty" in node:
        factor, dim = parse_unit(node["qty"][1]); return Qty(F(node["qty"][0])*factor, dim)
    if "const" in node:
        c = node["const"]
        if c == "pi": return Interval(PI_LO, PI_HI)
        if c == "e":  return Interval(E_LO, E_HI)
        raise Unverified("unknown constant '%s'" % c)
    if "op" in node:
        a = [ev(x) for x in node["args"]]; o = node["op"]
        if o == "^":
            base, exp = a[0], a[1]
            if not isinstance(exp, F) or exp.denominator != 1: raise Unverified("non-integer exponent")
            e = int(exp)
            if e == 0: return F(1)
            r = base
            for _ in range(e-1): r = combine("*", r, base)
            return r
        r = a[0]
        for x in a[1:]: r = combine(o, r, x)
        return r
    raise Unverified("unsupported node %s" % list(node))

def to_interval(x):
    if isinstance(x, Interval): return x
    if isinstance(x, F): return Interval(x, x)
    if isinstance(x, Qty): return Interval(x.v, x.v)
    raise Unverified("uncomparable value")

def kernel_verify(claim):
    try:
        comp = ev(claim["expr"]); clm = ev(claim["claimed"])
    except Unverified as u: return ("unverified", str(u))
    except ValueError as v:  return ("refuted", str(v))
    rel = claim["relation"]
    if isinstance(comp, Qty) or isinstance(clm, Qty):
        if not (isinstance(comp, Qty) and isinstance(clm, Qty)):
            return ("refuted", "one side dimensionless, other has units")
        if comp.dim != clm.dim:
            return ("refuted", "dimension mismatch %s vs %s" % (comp.dim, clm.dim))
    ci, ki = to_interval(comp), to_interval(clm)
    if rel == "~":
        tol = F(claim.get("tol", {}).get("abs", "0"))
        far_lo = ci.lo - ki.hi; far_hi = ci.hi - ki.lo
        maxdiff = max(abs(far_lo), abs(far_hi))
        mindiff = F(0) if (far_lo <= 0 <= far_hi) else min(abs(far_lo), abs(far_hi))
        if maxdiff <= tol: return ("valid", "|delta|<=%s (max=%.3g)" % (tol, float(maxdiff)))
        if mindiff > tol:  return ("refuted", "|delta|>%s (min=%.3g)" % (tol, float(mindiff)))
        return ("unverified", "interval straddles tolerance boundary")
    if rel == "=":
        if ci.lo == ci.hi == ki.lo == ki.hi: return ("valid", "exact equality")
        if ci.hi < ki.lo or ki.hi < ci.lo:   return ("refuted", "intervals disjoint")
        return ("unverified", "intervals overlap; cannot certify equality")
    if rel == "<":  return ("valid", "<")  if ci.hi <  ki.lo else (("refuted", "not <")  if ci.lo >= ki.hi else ("unverified", "overlap"))
    if rel == "<=": return ("valid", "<=") if ci.hi <= ki.lo else (("refuted", "not <=") if ci.lo >  ki.hi else ("unverified", "overlap"))
    if rel == ">":  return ("valid", ">")  if ci.lo >  ki.hi else (("refuted", "not >")  if ci.hi <= ki.lo else ("unverified", "overlap"))
    if rel == ">=": return ("valid", ">=") if ci.lo >= ki.hi else (("refuted", "not >=") if ci.hi <  ki.lo else ("unverified", "overlap"))
    return ("unverified", "unknown relation %s" % rel)


def n_int(x): return {"int": str(x)}
def n_rat(p, q): return {"rat": [str(p), str(q)]}
def n_dec(x): return {"dec": str(x)}
def n_qty(v, u): return {"qty": [str(v), u]}
def n_const(c): return {"const": c}
def op(o, *args): return {"op": o, "args": list(args)}

def claim(expr, relation, claimed, **extra):
    c = {"semantics": "arith/1", "expr": expr, "relation": relation, "claimed": claimed}
    c.update(extra); return c

if __name__ == "__main__":
    def show(tag, c):
        v = kernel_verify(c); print("  %-36s %-11s %s" % (tag, v[0], v[1]))

    print("=" * 74)
    print("arith_check - verdict battery\n")

    print("EXACT (bignum / rational)")
    show("987654321 * 123456789 (correct)",
         claim(op("*", n_int(987654321), n_int(123456789)), "=", n_int(121932631112635269)))
    show("987654321 * 123456789 (LLM slip)",
         claim(op("*", n_int(987654321), n_int(123456789)), "=", n_int(121932631112635260)))
    show("1/3 + 1/6 = 1/2",
         claim(op("+", n_rat(1, 3), n_rat(1, 6)), "=", n_rat(1, 2)))

    print("\nUNITS (dimensional + conversion)")
    show("60 mph * 2 hour = 120 mile",
         claim(op("*", n_qty(60, "mile/hour"), n_qty(2, "hour")), "=", n_qty(120, "mile")))
    show("5 km = 3 mile (bad convert)",
         claim(n_qty(5, "km"), "=", n_qty(3, "mile")))
    show("3 kg = 3 m (dimension error)",
         claim(n_qty(3, "kg"), "=", n_qty(3, "m")))

    print("\nAPPROX (rational-interval transcendentals)")
    show("2*pi ~ 6.2832  (abs 0.001)",
         claim(op("*", n_int(2), n_const("pi")), "~", n_dec("6.2832"), tol={"abs": "0.001"}))
    show("2*pi ~ 6.2832  (abs 0.000001)",
         claim(op("*", n_int(2), n_const("pi")), "~", n_dec("6.2832"), tol={"abs": "0.000001"}))

    print("\nORDER-OF-MAGNITUDE & RELATIONS")
    show("2^64 > 10^19 (correct)",
         claim(op("^", n_int(2), n_int(64)), ">", op("^", n_int(10), n_int(19))))
    show("13*12*11 < 1700 (false)",
         claim(op("*", n_int(13), n_int(12), n_int(11)), "<", n_int(1700)))

    print("\nUNVERIFIED (honest non-decisions)")
    show("uses unknown const sqrt2",
         claim(op("*", n_int(1), n_const("sqrt2")), "=", n_int(2)))
    show("raw IEEE float literal",
         claim({"float": "0.1"}, "=", n_dec("0.1")))
