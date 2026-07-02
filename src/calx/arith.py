"""calx.arith — polynomial-semantics arithmetisation for the cryptographic cert tier.

A faithful, dependency-free implementation of the first-order-logic-to-polynomial
arithmetisation of Gabbay, "Cryptographic certificates of validity for trustworthy
AI" (arXiv:2606.23768). It is the in-repo basis for the ``crypto_succinct`` cert
method (src/calx/sql/97_cert_crypto.sql), driven by tools/cert_crypto.py.

Core principle (Lemma 2.8 + Theorem 2.9): logical validity is the *vanishing* of a
non-negative polynomial residual.

    [phi]_s(x) == 0  <=>  phi valid       (and  [phi]_s(x) >= 0  always)

That single invariant is what makes the scheme sound: because every residual is a
sum / product / square of non-negatives, a conjunction (sum) can be zero only when
every conjunct is, so a false sub-claim can never be cancelled away. Constructs that
are not decidable by pure algebra (negation, disequality, ordering) are made
*witness-carrying*: the residual stays a square and soundness rests on "no witness
can exist" for a false statement.

Like calx.kernel, this module has NO external dependencies (no psycopg, no DB): a
consumer who receives an exported bundle can re-verify its arith_constraint
witnesses with nothing but a Python interpreter. That is proof-carrying code.

Public API:
    evaluate(phi, s, x=0) -> Fraction      # the residual [phi]_s(x)
    residual(phi, s, x=0) -> Fraction       # alias
    verdict(phi, s, x=0)  -> "VALID"|"REFUTED"
    modular_certify(phi, s, primes)         # mixed-characteristic (CRT) verdict
    bundle_admits(claims)                   # compartmentalisation invariant (I1,I2)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

from .kernel import _is_prime, check_crt  # Trunkit primitives (dependency-free)

__all__ = [
    "Interp", "Var", "Const", "Add", "Mul", "Sub", "Lookup", "Len",
    "Eq", "And", "Or", "Lt", "Gt", "Forall",
    "Not", "Neq", "Implies", "Exists", "Bool", "LeqZ", "Divides", "four_squares",
    "evaluate", "residual", "verdict",
    "prime_basis", "modular_certify", "unit_mod", "soundness_error_primes",
    "bundle_admits",
    "NODE_REGISTRY", "phi_from_json", "interp_from_json",
]


# --------------------------------------------------------------------------- #
# Interpretation: a symbol C maps to an integer matrix s(C) (Def 2.7). A row is
# read as a function via Lagrange interpolation through (1, C_i,1), (2, C_i,2)...
# so C_i(t) is defined for any t (Def 2.6).
# --------------------------------------------------------------------------- #
class Interp:
    def __init__(self, matrices: dict[str, list[list[int]]]):
        self.m = matrices

    def length(self, c: str) -> int:               # len(C) = number of columns
        return len(self.m[c][0])

    def row_at(self, c: str, i: int, t) -> Fraction:
        row = self.m[c][i - 1]
        xs = [Fraction(j) for j in range(1, len(row) + 1)]
        ys = [Fraction(v) for v in row]
        t = Fraction(t)
        acc = Fraction(0)
        for j in range(len(xs)):
            term = ys[j]
            for k in range(len(xs)):
                if k != j:
                    term *= (t - xs[k]) / (xs[j] - xs[k])
            acc += term
        return acc


# --------------------------------------------------------------------------- #
# Terms: ev(s, x) -> Fraction
# --------------------------------------------------------------------------- #
@dataclass
class Var:
    def ev(self, s, x): return Fraction(x)


@dataclass
class Const:
    a: int
    def ev(self, s, x): return Fraction(self.a)


@dataclass
class Add:
    lhs: object
    rhs: object
    def ev(self, s, x): return self.lhs.ev(s, x) + self.rhs.ev(s, x)


@dataclass
class Mul:
    lhs: object
    rhs: object
    def ev(self, s, x): return self.lhs.ev(s, x) * self.rhs.ev(s, x)


def Sub(a, b):                                      # a - b
    return Add(a, Mul(Const(-1), b))


@dataclass
class Lookup:                                       # C_i(t)
    c: str
    i: int
    t: object
    def ev(self, s, x): return s.row_at(self.c, self.i, self.t.ev(s, x))


@dataclass
class Len:                                          # len(C)
    c: str
    def ev(self, s, x): return Fraction(s.length(self.c))


# --------------------------------------------------------------------------- #
# Predicates: ev(s, x) -> non-negative Fraction; 0 == valid (Figure 3)
# --------------------------------------------------------------------------- #
@dataclass
class Eq:                                           # ([t] - [t'])^2
    lhs: object
    rhs: object
    def ev(self, s, x):
        d = self.lhs.ev(s, x) - self.rhs.ev(s, x)
        return d * d


@dataclass
class And:                                          # [phi] + [psi]
    lhs: object
    rhs: object
    def ev(self, s, x): return self.lhs.ev(s, x) + self.rhs.ev(s, x)


@dataclass
class Or:                                           # [phi] * [psi]
    lhs: object
    rhs: object
    def ev(self, s, x): return self.lhs.ev(s, x) * self.rhs.ev(s, x)


@dataclass
class Lt:                                           # t < C_i  (0 iff all cols satisfy)
    t: object
    c: str
    i: int
    def ev(self, s, x):
        tv = self.t.ev(s, x)
        for j in range(1, s.length(self.c) + 1):
            if not (s.row_at(self.c, self.i, j) > tv):
                return Fraction(1)
        return Fraction(0)


@dataclass
class Gt:                                           # C_i < t
    c: str
    i: int
    t: object
    def ev(self, s, x):
        tv = self.t.ev(s, x)
        for j in range(1, s.length(self.c) + 1):
            if not (s.row_at(self.c, self.i, j) < tv):
                return Fraction(1)
        return Fraction(0)


@dataclass
class Forall:                                       # root test over [len(C)]
    c: str
    body: object
    def ev(self, s, x):
        acc = Fraction(0)
        for xp in range(1, s.length(self.c) + 1):
            acc += self.body.ev(s, xp)
        return acc


# --------------------------------------------------------------------------- #
# Secure, witness-carrying extensions. Each preserves non-negativity and is 0
# iff valid; the undecidable-by-algebra cases push soundness onto witnesses.
# --------------------------------------------------------------------------- #
@dataclass
class Not:                                          # ¬phi via inverse witness winv
    phi: object                                     # = ([phi]*winv - 1)^2
    winv: object                                    # honest winv = 1/[phi]
    def ev(self, s, x):
        r = self.phi.ev(s, x)
        d = r * self.winv.ev(s, x) - 1
        return d * d


def Neq(lhs, rhs, winv):                            # t != t'  ==  ¬(t = t')
    return Not(Eq(lhs, rhs), winv)


def Implies(phi, psi, winv):                        # phi -> psi  ==  ¬phi ∨ psi
    return Or(Not(phi, winv), psi)


@dataclass
class Exists:                                       # ∃ over columns = product
    c: str
    body: object
    def ev(self, s, x):
        acc = Fraction(1)
        for xp in range(1, s.length(self.c) + 1):
            acc *= self.body.ev(s, xp)
        return acc


@dataclass
class Bool:                                         # b in {0,1}  ==  (b*(b-1))^2
    t: object
    def ev(self, s, x):
        v = self.t.ev(s, x)
        d = v * (v - 1)
        return d * d


@dataclass
class LeqZ:                                         # a <= b via four-square witness
    a: object                                       # = (b - a - Σ si^2)^2
    b: object
    s1: object
    s2: object
    s3: object
    s4: object
    def ev(self, s, x):
        diff = self.b.ev(s, x) - self.a.ev(s, x)
        ss = sum((t.ev(s, x)) ** 2 for t in (self.s1, self.s2, self.s3, self.s4))
        d = diff - ss
        return d * d


def Divides(d, n, q):                               # d | n  ==  ∃q. n = d*q
    return Eq(n, Mul(d, q))


def four_squares(n: int):
    """Honest order witness: n = a^2+b^2+c^2+d^2 for n >= 0 (Lagrange)."""
    if n < 0:
        return None
    r = int(n ** 0.5) + 1
    for a in range(r + 1):
        for b in range(a, r + 1):
            if a * a + b * b > n:
                break
            for c in range(b, r + 1):
                if a * a + b * b + c * c > n:
                    break
                rem = n - a * a - b * b - c * c
                d0 = int(rem ** 0.5)
                for dd in (d0 - 1, d0, d0 + 1):
                    if dd >= 0 and a * a + b * b + c * c + dd * dd == n:
                        return (a, b, c, dd)
    return None


# --------------------------------------------------------------------------- #
# The validity judgement (closed phi evaluated at 0).
# --------------------------------------------------------------------------- #
def evaluate(phi, s: Interp | None = None, x: int = 0) -> Fraction:
    return phi.ev(s, x)


def residual(phi, s: Interp | None = None, x: int = 0) -> Fraction:
    return phi.ev(s, x)


def verdict(phi, s: Interp | None = None, x: int = 0) -> str:
    return "VALID" if phi.ev(s, x) == 0 else "REFUTED"


# --------------------------------------------------------------------------- #
# Mixed-characteristic (CRT) certification on calx primitives. Real SNARK back
# ends work over prime fields; we reduce the vanishing test to mod-p checks over
# a calx prime basis and re-verify with calx.kernel.check_crt.
# --------------------------------------------------------------------------- #
def prime_basis(count: int, start: int = 2) -> list[int]:
    out, n = [], start
    while len(out) < count:
        if _is_prime(n):
            out.append(n)
        n += 1
    return out


def unit_mod(v: int, p: int) -> bool:
    """Is v invertible mod p? (the Not/Neq inverse witness exists)  <=>  p ∤ v."""
    return math.gcd(v % p, p) == 1


def soundness_error_primes(r: int, primes: list[int]) -> list[int]:
    """Basis primes dividing a nonzero residual r — each a modular 'miss'."""
    return [] if r == 0 else [p for p in primes if r % p == 0]


def modular_certify(phi, s, primes):
    """Certify [phi]_s = 0 modularly over a prime basis, then CRT.

    CRT soundness: with M = prod(primes) and 0 <= r, the modular check is EXACT
    iff M > r (then r ≡ 0 mod all p <=> r = 0). Returns (status, info) where
    status is 'valid' (provably 0), 'refuted' (a prime caught a nonzero residue),
    'valid?' (passes modular check but UNDER budget, soundness only probable), or
    'unverified' (non-integer/negative residual)."""
    r = phi.ev(s, 0)
    if r.denominator != 1 or r < 0:
        return "unverified", {"note": "non-integer/negative residual", "r": str(r)}
    r = int(r)
    M = math.prod(primes)
    residues = [r % p for p in primes]
    vanishes_mod = all(v == 0 for v in residues)
    crt_ok, _ = check_crt({"x": r, "congruences": [[r % p, p] for p in primes]})
    budget_ok = r < M
    if vanishes_mod and budget_ok:
        status = "valid"
    elif vanishes_mod:
        status = "valid?"
    else:
        status = "refuted"
    return status, {
        "residual": r, "residues_mod": dict(zip(primes, residues, strict=False)),
        "modulus_M": M, "budget_M_gt_r": budget_ok,
        "crt_kernel_ok": crt_ok, "vanishes_mod": vanishes_mod,
    }


# --------------------------------------------------------------------------- #
# Compartmentalisation invariant for combined bundles (compartment.py analysis).
# Every construct is individually sound, so the only cross-claim risk is symbol
# reuse. A bundle of sealed methods is admissible iff:
#   I1  no symbol is a PRIVATE register of two claims          (direct coupling)
#   I2  no symbol is PRIVATE in one claim and PUBLIC in another (seal break)
# --------------------------------------------------------------------------- #
def bundle_admits(claims):
    """claims: iterable of dicts {name, private:set[str], public:set[str]}.
    Returns (admit: bool, violations: list[tuple])."""
    viol = []
    seen = {}
    for c in claims:
        for sym in c.get("private", ()):  # I1
            if sym in seen:
                viol.append(("I1 register-aliasing", sym, seen[sym], c["name"]))
            seen[sym] = c["name"]
    private_syms = {sym: c["name"] for c in claims for sym in c.get("private", ())}
    for c in claims:                       # I2
        for sym in c.get("public", ()):
            if sym in private_syms and private_syms[sym] != c["name"]:
                viol.append(("I2 classification-conflict", sym,
                             private_syms[sym], c["name"]))
    return (len(viol) == 0), viol


# --------------------------------------------------------------------------- #
# JSON codec for the phi AST — the portable form an agent (or the trunkit-mcp
# arith_verify tool) submits. A node is {"op": <constructor>, "args": [...]};
# each arg is either a nested node (a dict) or a scalar passed through as-is.
# Decoding uses a fixed constructor allowlist — no eval, safe on untrusted
# input. Interp travels as its plain matrices dict: {"C": [[...], ...]}.
# --------------------------------------------------------------------------- #
NODE_REGISTRY = {
    f.__name__: f
    for f in (
        Var, Const, Add, Mul, Sub, Lookup, Len,
        Eq, And, Or, Lt, Gt, Forall,
        Not, Neq, Implies, Exists, Bool, LeqZ, Divides,
    )
}


def phi_from_json(obj):
    """Decode {"op": ..., "args": [...]} (already-parsed JSON) into a phi AST.

    Raises ValueError on unknown ops or malformed nodes so callers can map the
    failure to an honest 'unverified', never a guess.
    """
    if not isinstance(obj, dict) or "op" not in obj:
        raise ValueError(f"not a phi node: {obj!r}")
    ctor = NODE_REGISTRY.get(obj["op"])
    if ctor is None:
        raise ValueError(f"unknown op {obj['op']!r}")
    args = obj.get("args", [])
    if not isinstance(args, list):
        raise ValueError(f"args must be a list, got {type(args).__name__}")
    decoded = [phi_from_json(a) if isinstance(a, dict) else a for a in args]
    try:
        return ctor(*decoded)
    except TypeError as exc:
        raise ValueError(f"bad arity for {obj['op']}: {exc}") from exc


def interp_from_json(obj) -> Interp:
    """Decode {"C": [[int, ...], ...], ...} into an Interp (integer matrices)."""
    if not isinstance(obj, dict):
        raise ValueError("interpretation must be an object of symbol -> matrix")
    matrices: dict[str, list[list[int]]] = {}
    for name, mat in obj.items():
        if (
            not isinstance(mat, list) or not mat
            or not all(isinstance(row, list) and row for row in mat)
            or not all(isinstance(v, int) and not isinstance(v, bool)
                       for row in mat for v in row)
        ):
            raise ValueError(f"matrix for {name!r} must be a non-empty list "
                             "of non-empty integer rows")
        widths = {len(row) for row in mat}
        if len(widths) != 1:
            raise ValueError(f"matrix for {name!r} has ragged rows")
        matrices[name] = mat
    return Interp(matrices)