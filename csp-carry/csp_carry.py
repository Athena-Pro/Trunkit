#!/usr/bin/env python3
"""
csp_carry - a universal Trunkit method: verify a SOLUTION to a constraint problem.

The general verify-easy / find-hard frame. Finding an assignment is NP-hard across a huge
family (scheduling, coloring, SAT, Sudoku, planning); CHECKING one is polynomial. The witness
is the assignment; the kernel evaluates every constraint and, on failure, hands back the exact
violated constraint as the refutation certificate. The sliding-puzzle parity method is the
sequential cousin of this (witness = move list); csp_carry covers the static-assignment case.

Why universal (not pocket):
  * One method subsumes many puzzles - coloring, Latin squares/Sudoku, SAT, linear systems.
  * Tiny deterministic kernel - evaluate constraints; no solver. Same verdict anywhere.
  * Portable witness - a plain {variable: value} map plus a declared instance.
  * Three honest verdicts, with a NAMED violated constraint on refuted.

Verdicts:
  valid       every variable is in-domain and every constraint holds
  refuted     a variable is out of domain, or a specific constraint is violated (named)
  unverified  the witness omits a variable, or a constraint type the kernel doesn't support

Instance:
  {"vars": {"x": [0,1,2], ...}, "constraints": [ {"type": "...", ...}, ... ]}
Constraint types:
  all_different  {"type":"all_different","vars":[...]}
  not_equal      {"type":"not_equal","a":"x","b":"y"}
  equal          {"type":"equal","a":"x","b":"y"}
  linear         {"type":"linear","terms":[[coef,var],...],"rel":"=|<=|>=|<|>","rhs":n}
  clause (SAT)   {"type":"clause","lits":[["x",true],["y",false],...]}   # vars in {0,1}

Trunkit mapping:
  curry  fn   check_constraint(con, assignment) -> bool   (pure)
  claim       "assignment A solves instance I"  method = comp_sql / csp_carry
  witness     the assignment map                method = witness_carry
  kernel      kernel_verify(instance, A) checks each constraint  (mirrors trunkit.kernel_verify)
"""

def _linear_value(terms, A):
    return sum(coef * A[var] for coef, var in terms)

def _check(con, A):
    """Return (ok, message). ok is None for unsupported constraint types."""
    t = con["type"]
    if t == "all_different":
        vals = [A[v] for v in con["vars"]]
        if len(set(vals)) == len(vals): return (True, "")
        dup = next(x for x in vals if vals.count(x) > 1)
        return (False, "value %r repeated among %s" % (dup, con["vars"]))
    if t == "not_equal":
        return (A[con["a"]] != A[con["b"]], "%s == %s (both %r)" % (con["a"], con["b"], A[con["a"]]))
    if t == "equal":
        return (A[con["a"]] == A[con["b"]], "%s != %s (%r vs %r)" % (con["a"], con["b"], A[con["a"]], A[con["b"]]))
    if t == "linear":
        lhs = _linear_value(con["terms"], A); rel = con["rel"]; rhs = con["rhs"]
        ok = {"=": lhs == rhs, "<=": lhs <= rhs, ">=": lhs >= rhs, "<": lhs < rhs, ">": lhs > rhs}[rel]
        return (ok, "lhs=%s %s %s is false" % (lhs, rel, rhs))
    if t == "clause":
        ok = any(A[v] == (1 if sign else 0) for v, sign in con["lits"])
        return (ok, "no literal satisfied in %s" % con["lits"])
    return (None, "unsupported constraint type '%s'" % t)

def kernel_verify(instance, A):
    for v, dom in instance["vars"].items():
        if v not in A:
            return ("unverified", "assignment omits variable '%s'" % v)
        if A[v] not in dom:
            return ("refuted", "variable %s=%r outside domain %s" % (v, A[v], dom))
    for k, con in enumerate(instance["constraints"]):
        ok, msg = _check(con, A)
        if ok is None:
            return ("unverified", msg)
        if not ok:
            return ("refuted", "constraint #%d (%s) violated: %s" % (k, con["type"], msg))
    return ("valid", "all %d constraints satisfied" % len(instance["constraints"]))


if __name__ == "__main__":
    def show(tag, v):
        print("  %-38s %-11s %s" % (tag, v[0], v[1]))

    print("=" * 78)
    print("csp_carry - verdict battery\n")

    print("GRAPH 3-COLORING  (triangle A-B-C plus D)")
    colors = [0, 1, 2]
    g = {"vars": {x: colors for x in "ABCD"},
         "constraints": [{"type": "not_equal", "a": a, "b": b} for a, b in ["AB", "BC", "CA", "CD"]]}
    show("proper coloring", kernel_verify(g, {"A": 0, "B": 1, "C": 2, "D": 0}))
    show("B and C clash", kernel_verify(g, {"A": 0, "B": 1, "C": 1, "D": 0}))

    print("\nLATIN SQUARE  (3x3, rows & cols all-different)")
    cells = [r + c for r in "123" for c in "123"]
    rows = [{"type": "all_different", "vars": [r + c for c in "123"]} for r in "123"]
    cols = [{"type": "all_different", "vars": [r + c for r in "123"]} for c in "123"]
    ls = {"vars": {cell: [1, 2, 3] for cell in cells}, "constraints": rows + cols}
    good = {"11":1,"12":2,"13":3, "21":2,"22":3,"23":1, "31":3,"32":1,"33":2}
    bad  = dict(good); bad["33"] = 1
    show("valid square", kernel_verify(ls, good))
    show("column 3 duplicate", kernel_verify(ls, bad))

    print("\nSAT  (x1 v ~x2) & (x2 v x3) & (~x1 v ~x3)")
    sat = {"vars": {"x1":[0,1], "x2":[0,1], "x3":[0,1]},
           "constraints": [
               {"type":"clause","lits":[["x1",True],["x2",False]]},
               {"type":"clause","lits":[["x2",True],["x3",True]]},
               {"type":"clause","lits":[["x1",False],["x3",False]]}]}
    show("satisfying assignment", kernel_verify(sat, {"x1":1,"x2":1,"x3":0}))
    show("falsifying assignment", kernel_verify(sat, {"x1":0,"x2":0,"x3":0}))

    print("\nLINEAR  x + 2y = 7,  0<=x,y<=7")
    lin = {"vars": {"x": list(range(8)), "y": list(range(8))},
           "constraints": [{"type":"linear","terms":[[1,"x"],[2,"y"]],"rel":"=","rhs":7}]}
    show("x=3, y=2", kernel_verify(lin, {"x":3,"y":2}))
    show("x=1, y=1", kernel_verify(lin, {"x":1,"y":1}))

    print("\nUNVERIFIED  (honest non-decisions)")
    show("out-of-domain value", kernel_verify(g, {"A":5,"B":1,"C":2,"D":0}))
    show("missing variable", kernel_verify(g, {"A":0,"B":1,"C":2}))
    show("unsupported constraint type",
         kernel_verify({"vars":{"x":[0,1]},"constraints":[{"type":"alldiff_circular","vars":["x"]}]}, {"x":0}))
