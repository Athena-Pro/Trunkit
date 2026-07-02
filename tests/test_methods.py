"""Verdict batteries for the universal method kernels (calx.methods).

Ports of the __main__ batteries the four METHODS.md kernels shipped with at
the repo root, now pinned as tests. Each case asserts the three-valued
verdict through the calx.kernel adapter — True (valid) / False (refuted) /
None (unverified) — so the dispatch path agents actually hit is what's
exercised. DB-free.
"""

from __future__ import annotations

import pytest

from calx.kernel import verify_witness
from calx.methods import arith_check as ac
from calx.methods import parity_puzzle as pp
from calx.methods import quote_carry as qc

# ── arith_check ──────────────────────────────────────────────────────────────


def _arith(expr, relation, claimed, **extra):
    w = {"schema": "arith_check", "expr": expr, "relation": relation, "claimed": claimed}
    w.update(extra)
    return verify_witness(w)


def test_arith_exact_bignum():
    good = _arith(ac.op("*", ac.n_int(987654321), ac.n_int(123456789)),
                  "=", ac.n_int(121932631112635269))
    assert good[0] is True
    slip = _arith(ac.op("*", ac.n_int(987654321), ac.n_int(123456789)),
                  "=", ac.n_int(121932631112635260))
    assert slip[0] is False


def test_arith_rationals():
    ok, _ = _arith(ac.op("+", ac.n_rat(1, 3), ac.n_rat(1, 6)), "=", ac.n_rat(1, 2))
    assert ok is True


def test_arith_units():
    ok, _ = _arith(ac.op("*", ac.n_qty(60, "mile/hour"), ac.n_qty(2, "hour")),
                   "=", ac.n_qty(120, "mile"))
    assert ok is True
    bad_convert, _ = _arith(ac.n_qty(5, "km"), "=", ac.n_qty(3, "mile"))
    assert bad_convert is False
    dim_error, ev = _arith(ac.n_qty(3, "kg"), "=", ac.n_qty(3, "m"))
    assert dim_error is False and "dimension mismatch" in ev["detail"]


def test_arith_interval_transcendentals():
    two_pi = ac.op("*", ac.n_int(2), ac.n_const("pi"))
    loose, _ = _arith(two_pi, "~", ac.n_dec("6.2832"), tol={"abs": "0.001"})
    assert loose is True
    tight, _ = _arith(two_pi, "~", ac.n_dec("6.2832"), tol={"abs": "0.000001"})
    assert tight is False


def test_arith_relations():
    ok, _ = _arith(ac.op("^", ac.n_int(2), ac.n_int(64)),
                   ">", ac.op("^", ac.n_int(10), ac.n_int(19)))
    assert ok is True
    ok, _ = _arith(ac.op("*", ac.n_int(13), ac.n_int(12), ac.n_int(11)),
                   "<", ac.n_int(1700))
    assert ok is False


def test_arith_honest_non_decisions():
    unknown_const, ev = _arith(ac.op("*", ac.n_int(1), ac.n_const("sqrt2")),
                               "=", ac.n_int(2))
    assert unknown_const is None and "sqrt2" in ev["detail"]
    raw_float, ev = _arith({"float": "0.1"}, "=", ac.n_dec("0.1"))
    assert raw_float is None and "non-portable" in ev["detail"]
    missing, ev = verify_witness({"schema": "arith_check", "relation": "="})
    assert missing is None


# ── quote_carry ──────────────────────────────────────────────────────────────

DOC = ("We hold these truths to be self-evident, that all men are created equal, "
       "that they are endowed by their Creator with certain unalienable Rights.")


def _quote_witness(**over):
    store = qc.Store()
    store.add("declaration", DOC)
    start = DOC.index("all men")
    w = qc.make_claim(store, "declaration", start, start + len("all men are created equal"))
    w.update({"schema": "quote_carry", "doc_text": DOC})
    w.update(over)
    return w


def test_quote_exact_span_grounds():
    ok, ev = verify_witness(_quote_witness())
    assert ok is True and ev["status"] == "valid"


def test_quote_right_text_wrong_offset():
    w = _quote_witness()
    w["span"] = [w["span"][0] + 4, w["span"][1] + 4]
    ok, ev = verify_witness(w)
    assert ok is False and "not claimed" in ev["detail"]


def test_quote_fabricated():
    w = _quote_witness()
    w["quote"] = "all men are created unequal"
    w["quote_sha256"] = qc.sha(w["quote"])
    ok, ev = verify_witness(w)
    assert ok is False and "not found" in ev["detail"]


def test_quote_version_mismatch():
    w = _quote_witness(doc_sha256=qc.sha("a different version of the document"))
    ok, ev = verify_witness(w)
    assert ok is False and "does not hash" in ev["detail"]


def test_quote_document_unavailable_is_unverified():
    w = _quote_witness()
    del w["doc_text"]
    w["doc_sha256"] = qc.sha("unseen document body")
    ok, ev = verify_witness(w)
    assert ok is None and ev["status"] == "unverified"


# ── csp_carry ────────────────────────────────────────────────────────────────

COLORING = {
    "vars": {x: [0, 1, 2] for x in "ABCD"},
    "constraints": [{"type": "not_equal", "a": a, "b": b}
                    for a, b in ["AB", "BC", "CA", "CD"]],
}


def _csp(instance, assignment):
    return verify_witness({"schema": "csp_carry",
                           "instance": instance, "assignment": assignment})


def test_csp_coloring():
    assert _csp(COLORING, {"A": 0, "B": 1, "C": 2, "D": 0})[0] is True
    ok, ev = _csp(COLORING, {"A": 0, "B": 1, "C": 1, "D": 0})
    assert ok is False and "not_equal" in ev["detail"]


def test_csp_latin_square():
    cells = [r + c for r in "123" for c in "123"]
    rows = [{"type": "all_different", "vars": [r + c for c in "123"]} for r in "123"]
    cols = [{"type": "all_different", "vars": [r + c for r in "123"]} for c in "123"]
    ls = {"vars": {cell: [1, 2, 3] for cell in cells}, "constraints": rows + cols}
    good = {"11": 1, "12": 2, "13": 3, "21": 2, "22": 3, "23": 1,
            "31": 3, "32": 1, "33": 2}
    assert _csp(ls, good)[0] is True
    bad = dict(good)
    bad["33"] = 1
    assert _csp(ls, bad)[0] is False


def test_csp_sat():
    sat = {"vars": {"x1": [0, 1], "x2": [0, 1], "x3": [0, 1]},
           "constraints": [
               {"type": "clause", "lits": [["x1", True], ["x2", False]]},
               {"type": "clause", "lits": [["x2", True], ["x3", True]]},
               {"type": "clause", "lits": [["x1", False], ["x3", False]]}]}
    assert _csp(sat, {"x1": 1, "x2": 1, "x3": 0})[0] is True
    ok, ev = _csp(sat, {"x1": 0, "x2": 0, "x3": 0})
    assert ok is False and "clause" in ev["detail"]


def test_csp_linear():
    lin = {"vars": {"x": list(range(8)), "y": list(range(8))},
           "constraints": [{"type": "linear", "terms": [[1, "x"], [2, "y"]],
                            "rel": "=", "rhs": 7}]}
    assert _csp(lin, {"x": 3, "y": 2})[0] is True
    assert _csp(lin, {"x": 1, "y": 1})[0] is False


def test_csp_honest_non_decisions():
    out_of_domain, _ = _csp(COLORING, {"A": 5, "B": 1, "C": 2, "D": 0})
    assert out_of_domain is False                     # provable violation
    missing_var, ev = _csp(COLORING, {"A": 0, "B": 1, "C": 2})
    assert missing_var is None and "omits" in ev["detail"]
    unsupported, ev = _csp({"vars": {"x": [0, 1]},
                            "constraints": [{"type": "alldiff_circular", "vars": ["x"]}]},
                           {"x": 0})
    assert unsupported is None and "unsupported" in ev["detail"]
    malformed = verify_witness({"schema": "csp_carry", "assignment": {}})
    assert malformed[0] is None


# ── puzzle_parity ────────────────────────────────────────────────────────────


def _parity(state, rows=3, cols=3, moves=None):
    w = {"schema": "puzzle_parity", "rows": rows, "cols": cols, "state": list(state)}
    if moves is not None:
        w["moves"] = moves
    return verify_witness(w)


def test_parity_three_verdicts():
    p = pp.Puzzle(3, 3)
    scrambled = p.scramble(40, seed=7)

    no_witness, ev = _parity(scrambled)
    assert no_witness is None and ev["invariant"] == 1

    moves = p.solve(scrambled)
    with_witness, ev = _parity(scrambled, moves=list(moves))
    assert with_witness is True

    bad = moves[:-2] if len(moves) > 2 else ["U"]
    wrong, ev = _parity(scrambled, moves=list(bad))
    assert wrong is False


def test_parity_unsolvable_is_the_certificate():
    unsolvable = list(pp.Puzzle(3, 3).goal)
    unsolvable[0], unsolvable[1] = unsolvable[1], unsolvable[0]
    ok, ev = _parity(unsolvable)
    assert ok is False and ev["invariant"] == -1


def test_parity_15_puzzle_instant_verdict():
    p = pp.Puzzle(4, 4)
    unsolvable = list(p.goal)
    unsolvable[0], unsolvable[1] = unsolvable[1], unsolvable[0]
    ok, ev = _parity(unsolvable, rows=4, cols=4)
    assert ok is False and ev["invariant"] == -1


def test_parity_malformed_state():
    assert _parity([1, 2, 3])[0] is None                 # not a permutation
    assert _parity(list(range(9)), rows=1, cols=9)[0] is None


@pytest.mark.slow
def test_parity_invariant_equals_reachability_8_puzzle():
    """The parity feature is exactly BFS reachability over all 9! states."""
    from collections import deque
    from itertools import permutations

    p = pp.Puzzle(3, 3)
    seen = {p.goal}
    q = deque([p.goal])
    while q:
        cur = q.popleft()
        for ni, _mv in p._neighbors(cur.index(0)):
            lst = list(cur)
            bi = cur.index(0)
            lst[bi], lst[ni] = lst[ni], lst[bi]
            nx = tuple(lst)
            if nx not in seen:
                seen.add(nx)
                q.append(nx)
    positive = {perm for perm in permutations(range(9)) if p.feature(perm) == 1}
    assert len(seen) == 362880 // 2
    assert seen == positive


# ── dispatch surface ─────────────────────────────────────────────────────────


def test_all_four_schemas_are_dispatchable():
    from calx.kernel import _KERNELS
    for schema in ("arith_check", "quote_carry", "csp_carry", "puzzle_parity"):
        assert schema in _KERNELS
    unknown = verify_witness({"schema": "chess_carry"})
    assert unknown[0] is None
