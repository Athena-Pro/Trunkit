"""csp_carry — verify a SOLUTION to a constraint problem.

The general verify-easy / find-hard frame: finding an assignment is NP-hard
across a huge family (scheduling, coloring, SAT, Sudoku, planning); CHECKING
one is polynomial. The witness is the assignment; the kernel evaluates every
constraint and, on failure, hands back the exact violated constraint as the
refutation certificate. puzzle_parity is the sequential cousin of this
(witness = move list); csp_carry covers the static-assignment case.

Verdicts:
  valid       every variable is in-domain and every constraint holds
  refuted     a variable is out of domain, or a specific constraint is
              violated (named)
  unverified  the witness omits a variable, or uses a constraint type the
              kernel doesn't support

Instance:
  {"vars": {"x": [0,1,2], ...}, "constraints": [{"type": "...", ...}, ...]}
Constraint types:
  all_different  {"type":"all_different","vars":[...]}
  not_equal      {"type":"not_equal","a":"x","b":"y"}
  equal          {"type":"equal","a":"x","b":"y"}
  linear         {"type":"linear","terms":[[coef,var],...],"rel":"=|<=|>=|<|>","rhs":n}
  clause (SAT)   {"type":"clause","lits":[["x",true],["y",false],...]}   # vars in {0,1}

Kernel-dispatch witness (calx.kernel schema "csp_carry"):
  {"schema": "csp_carry", "instance": {...}, "assignment": {...}}
Spec: docs/methods/csp_carry_spec.md.
"""

from __future__ import annotations

from collections import Counter
from fractions import Fraction
from typing import Any


def _exact(value):
    if type(value) is int and value.bit_length() <= 4096:
        return Fraction(value)
    if (
        isinstance(value, dict)
        and set(value) == {"num", "den"}
        and type(value["num"]) is int
        and type(value["den"]) is int
        and value["den"] > 0
        and value["num"].bit_length() <= 4096
        and value["den"].bit_length() <= 4096
    ):
        return Fraction(value["num"], value["den"])
    raise ValueError("linear arithmetic requires integers or {num, den} rationals")


def _scalar(value):
    if type(value) not in (int, str, bool, type(None)):
        raise ValueError("finite domains require exact JSON scalar values")
    if type(value) is int and value.bit_length() > 4096:
        raise ValueError("integer exceeds verification budget")
    return (type(value).__name__, value)


def _validate(instance, assignment):
    variables, constraints = instance["vars"], instance["constraints"]
    if not isinstance(variables, dict) or not isinstance(constraints, list):
        raise ValueError("vars must be an object and constraints a list")
    if len(variables) > 10000 or len(constraints) > 10000:
        raise ValueError("instance exceeds verification budget")
    if set(assignment) - set(variables):
        raise ValueError("assignment contains undeclared variables")
    work = 0
    boolean_domains = set()
    for name, domain in variables.items():
        if not isinstance(name, str) or not isinstance(domain, list):
            raise ValueError("variables need text names and finite list domains")
        work += len(domain)
        if work > 100000:
            raise ValueError("instance exceeds aggregate verification budget")
        for value in domain:
            _scalar(value)
        if all(type(value) is int and value in (0, 1) for value in domain):
            boolean_domains.add(name)
    for con in constraints:
        if not isinstance(con, dict):
            raise ValueError("constraint must be an object")
        kind = con.get("type")
        if kind == "all_different":
            refs = con["vars"]
        elif kind in ("equal", "not_equal"):
            refs = [con["a"], con["b"]]
        elif kind == "linear":
            terms = con["terms"]
            if not isinstance(terms, list) or len(terms) > 10000:
                raise ValueError("linear terms must be a bounded list")
            refs = []
            for term in terms:
                if not isinstance(term, list) or len(term) != 2:
                    raise ValueError("linear terms need [coefficient, variable]")
                _exact(term[0])
                refs.append(term[1])
            _exact(con["rhs"])
            if con["rel"] not in ("=", "<=", ">=", "<", ">"):
                raise ValueError("unsupported linear relation")
        elif kind == "clause":
            literals = con["lits"]
            if not isinstance(literals, list) or len(literals) > 10000:
                raise ValueError("clause literals must be a bounded list")
            refs = []
            for literal in literals:
                if (
                    not isinstance(literal, list)
                    or len(literal) != 2
                    or type(literal[1]) is not bool
                ):
                    raise ValueError("literal needs [variable, boolean sign]")
                refs.append(literal[0])
            for name in refs:
                if name in variables and name not in boolean_domains:
                    raise ValueError("SAT variables require Boolean integer domains")
        else:
            raise ValueError(f"unsupported constraint type {kind!r}")
        if not isinstance(refs, list) or any(
            not isinstance(v, str) or v not in variables for v in refs
        ):
            raise ValueError("constraint references an undeclared variable")
        work += len(refs)
        if work > 100000:
            raise ValueError("instance exceeds aggregate verification budget")
    for value in assignment.values():
        _scalar(value)


def _linear_value(terms, assignment):
    return sum((_exact(coef) * _exact(assignment[var]) for coef, var in terms), Fraction(0))


def _check(con: dict[str, Any], assignment: dict[str, Any]):
    """Return (ok, message). ok is None for unsupported constraint types."""
    t = con["type"]
    if t == "all_different":
        vals = [_scalar(assignment[v]) for v in con["vars"]]
        counts = Counter(vals)
        if len(counts) == len(vals):
            return (True, "")
        dup = next(x for x, count in counts.items() if count > 1)
        return (False, f"value {dup!r} repeated among {con['vars']}")
    if t == "not_equal":
        a, b = con["a"], con["b"]
        return (
            _scalar(assignment[a]) != _scalar(assignment[b]),
            f"{a} == {b} (both {assignment[a]!r})",
        )
    if t == "equal":
        a, b = con["a"], con["b"]
        return (
            _scalar(assignment[a]) == _scalar(assignment[b]),
            f"{a} != {b} ({assignment[a]!r} vs {assignment[b]!r})",
        )
    if t == "linear":
        lhs = _linear_value(con["terms"], assignment)
        rel, rhs = con["rel"], _exact(con["rhs"])
        ok = {"=": lhs == rhs, "<=": lhs <= rhs, ">=": lhs >= rhs, "<": lhs < rhs, ">": lhs > rhs}[
            rel
        ]
        return (ok, f"lhs={lhs} {rel} {rhs} is false")
    if t == "clause":
        ok = any(assignment[v] == (1 if sign else 0) for v, sign in con["lits"])
        return (ok, f"no literal satisfied in {con['lits']}")
    return (None, f"unsupported constraint type '{t}'")


def kernel_verify(instance: dict[str, Any], assignment: dict[str, Any]) -> tuple[str, str]:
    try:
        _validate(instance, assignment)
    except (KeyError, TypeError, ValueError) as exc:
        return ("unverified", f"malformed instance/assignment: {exc}")
    for v, dom in instance["vars"].items():
        if v not in assignment:
            return ("unverified", f"assignment omits variable '{v}'")
        if _scalar(assignment[v]) not in [_scalar(value) for value in dom]:
            return ("refuted", f"variable {v}={assignment[v]!r} outside domain {dom}")
    for k, con in enumerate(instance["constraints"]):
        ok, msg = _check(con, assignment)
        if ok is None:
            return ("unverified", msg)
        if not ok:
            return ("refuted", f"constraint #{k} ({con['type']}) violated: {msg}")
    return ("valid", f"all {len(instance['constraints'])} constraints satisfied")


# ── calx.kernel adapter (schema "csp_carry") ────────────────────────────────


def check_csp_carry(w: dict[str, Any]) -> tuple[bool | None, dict[str, Any]]:
    instance = w.get("instance")
    assignment = w.get("assignment")
    if not isinstance(instance, dict) or "vars" not in instance or "constraints" not in instance:
        return None, {"error": "witness missing instance {vars, constraints}"}
    if not isinstance(assignment, dict):
        return None, {"error": "witness missing assignment map"}
    try:
        status, detail = kernel_verify(instance, assignment)
    except (KeyError, TypeError, ValueError) as exc:
        return None, {"error": f"malformed instance/assignment: {exc}"}
    ok = True if status == "valid" else False if status == "refuted" else None
    return ok, {"status": status, "detail": detail, "constraints": len(instance["constraints"])}
