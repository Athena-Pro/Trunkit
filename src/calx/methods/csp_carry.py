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

from typing import Any


def _linear_value(terms, assignment):
    return sum(coef * assignment[var] for coef, var in terms)


def _check(con: dict[str, Any], assignment: dict[str, Any]):
    """Return (ok, message). ok is None for unsupported constraint types."""
    t = con["type"]
    if t == "all_different":
        vals = [assignment[v] for v in con["vars"]]
        if len(set(vals)) == len(vals):
            return (True, "")
        dup = next(x for x in vals if vals.count(x) > 1)
        return (False, f"value {dup!r} repeated among {con['vars']}")
    if t == "not_equal":
        a, b = con["a"], con["b"]
        return (assignment[a] != assignment[b],
                f"{a} == {b} (both {assignment[a]!r})")
    if t == "equal":
        a, b = con["a"], con["b"]
        return (assignment[a] == assignment[b],
                f"{a} != {b} ({assignment[a]!r} vs {assignment[b]!r})")
    if t == "linear":
        lhs = _linear_value(con["terms"], assignment)
        rel, rhs = con["rel"], con["rhs"]
        ok = {"=": lhs == rhs, "<=": lhs <= rhs, ">=": lhs >= rhs,
              "<": lhs < rhs, ">": lhs > rhs}[rel]
        return (ok, f"lhs={lhs} {rel} {rhs} is false")
    if t == "clause":
        ok = any(assignment[v] == (1 if sign else 0) for v, sign in con["lits"])
        return (ok, f"no literal satisfied in {con['lits']}")
    return (None, f"unsupported constraint type '{t}'")


def kernel_verify(instance: dict[str, Any], assignment: dict[str, Any]) -> tuple[str, str]:
    for v, dom in instance["vars"].items():
        if v not in assignment:
            return ("unverified", f"assignment omits variable '{v}'")
        if assignment[v] not in dom:
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
    if not isinstance(instance, dict) or "vars" not in instance \
            or "constraints" not in instance:
        return None, {"error": "witness missing instance {vars, constraints}"}
    if not isinstance(assignment, dict):
        return None, {"error": "witness missing assignment map"}
    try:
        status, detail = kernel_verify(instance, assignment)
    except (KeyError, TypeError) as exc:
        return None, {"error": f"malformed instance/assignment: {exc}"}
    ok = True if status == "valid" else False if status == "refuted" else None
    return ok, {"status": status, "detail": detail,
                "constraints": len(instance["constraints"])}
