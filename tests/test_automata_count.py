"""Word enumeration certificates are checked independently of a database."""

from __future__ import annotations

from copy import deepcopy
from itertools import product
from random import Random

import pytest

from calx import automata_count
from calx.automata_count import (
    canonical_dfa,
    check_automata_count,
    check_automata_extremal,
    claim_subject_ref,
    count_words,
    dfa_digest,
    export_count_witness,
    export_extremal_witness,
    extremal_claim_subject_ref,
    product_dfa,
)


def _no_adjacent_ones():
    return {"alphabet": ["0", "1"], "states": [0, 1], "initial": 0,
            "accepting": [0, 1], "transitions": [[0, "0", 0], [0, "1", 1], [1, "0", 0]]}


def _accepts(dfa, word):
    edges = {(a, symbol): b for a, symbol, b in dfa["transitions"]}
    state = dfa["initial"]
    for symbol in word:
        state = edges.get((state, symbol))
        if state is None:
            return False
    return state in dfa["accepting"]


def _brute_counts(dfa, length):
    return [sum(_accepts(dfa, word) for word in product(dfa["alphabet"], repeat=n))
            for n in range(length + 1)]


def test_fibonacci_language_and_all_length_recurrence():
    dfa = _no_adjacent_ones()
    witness = export_count_witness(dfa, 20, annihilator=[-1, -1, 1])
    assert witness["counts"][:8] == [1, 2, 3, 5, 8, 13, 21, 34]
    assert witness["counts"][:7] == _brute_counts(dfa, 6)
    ok, evidence = check_automata_count(witness)
    assert ok is True and evidence["recurrence_identity"] is True
    assert claim_subject_ref(witness) == {
        "witness_schema": "dfa_count.v1", "instance_digest": witness["dfa_digest"],
        "predicate": {"name": "accepted_word_counts", "max_length": 20},
    }


def test_counting_symbols_is_not_counting_repeated_symbol_walks():
    dfa = {"alphabet": ["a", "b"], "states": [42], "initial": 42,
           "accepting": [42], "transitions": [[42, "a", 42], [42, "b", 42]]}
    assert count_words(dfa, 200)[-1] == 2 ** 200
    assert check_automata_count(export_count_witness(dfa, 200, annihilator=[-2, 1]))[0]


def test_snapshot_order_does_not_change_digest_and_input_is_owned():
    dfa = _no_adjacent_ones()
    original = deepcopy(dfa)
    reordered = {**dfa, "alphabet": list(reversed(dfa["alphabet"])),
                 "transitions": list(reversed(dfa["transitions"]))}
    assert dfa_digest(dfa) == dfa_digest(reordered)
    canonical_dfa(dfa)["transitions"][0][0] = 999
    witness = export_count_witness(dfa, 2)
    witness["dfa"]["states"].append(3)
    assert dfa == original


@pytest.mark.parametrize("field", ["dfa_digest", "matrix", "counts", "recurrence"])
def test_tampering_is_refuted(field):
    witness = export_count_witness(_no_adjacent_ones(), 5, annihilator=[-1, -1, 1])
    if field == "dfa_digest":
        witness[field] = "0" * 64
    elif field == "matrix":
        witness[field][0][0] += 1
    elif field == "counts":
        witness[field][3] += 1
    else:
        witness[field]["polynomial"] = [-2, 1]
    assert check_automata_count(witness)[0] is False


def test_prefix_fitting_is_insufficient_for_a_recurrence():
    # Prefix [1,2] fits c(n+1)=2*c(n), but the DFA matrix does not.
    witness = export_count_witness(_no_adjacent_ones(), 1)
    witness["recurrence"] = {"polynomial": [-2, 1]}
    assert check_automata_count(witness)[0] is False


@pytest.mark.parametrize("change", [
    {"initial": True}, {"states": [0, 0]}, {"alphabet": ["0", "0"]},
    {"accepting": [3]}, {"transitions": [[0, "0", 0], [0, "0", 1]]},
    {"transitions": [[0, "other", 0]]},
])
def test_malformed_or_nondeterministic_snapshot_is_unverified(change):
    witness = export_count_witness(_no_adjacent_ones(), 3)
    witness["dfa"].update(change)
    assert check_automata_count(witness)[0] is None


def test_rejects_float_counts_and_false_as_matrix_integer():
    witness = export_count_witness(_no_adjacent_ones(), 3)
    witness["counts"][0] = 1.0
    assert check_automata_count(witness)[0] is None
    witness = export_count_witness(_no_adjacent_ones(), 3)
    witness["matrix"][0][0] = True
    assert check_automata_count(witness)[0] is None


def test_empty_alphabet_and_empty_language():
    empty_alphabet = {"states": [-4], "alphabet": [], "initial": -4,
                      "accepting": [-4], "transitions": []}
    assert count_words(empty_alphabet, 3) == [1, 0, 0, 0]
    empty_alphabet["accepting"] = []
    assert count_words(empty_alphabet, 3) == [0, 0, 0, 0]


@pytest.mark.parametrize("op", ["intersection", "union"])
def test_product_counts_match_independent_brute_force_including_partial_edges(op):
    left = _no_adjacent_ones()
    right = {"alphabet": ["1", "0"], "states": [8, 9], "initial": 8,
             "accepting": [9], "transitions": [[8, "1", 9], [9, "1", 9]]}
    combined = product_dfa(left, right, op)
    expected = []
    for n in range(7):
        flags = [(_accepts(left, word), _accepts(right, word))
                 for word in product(left["alphabet"], repeat=n)]
        expected.append(sum(all(f) if op == "intersection" else any(f) for f in flags))
    assert count_words(combined, 6) == expected
    assert check_automata_count(export_count_witness(combined, 6))[0]


def test_product_budgets_fail_instead_of_truncating():
    dfa = _no_adjacent_ones()
    with pytest.raises(ValueError, match="state budget"):
        product_dfa(dfa, dfa, state_budget=1)
    with pytest.raises(ValueError, match="transition budget"):
        product_dfa(dfa, dfa, transition_budget=1)
    with pytest.raises(ValueError, match="max_length"):
        count_words(dfa, 4097)
    with pytest.raises(ValueError, match="work budget"):
        dense = {"alphabet": [str(x) for x in range(64)], "states": list(range(64)),
                 "initial": 0, "accepting": [0],
                 "transitions": [[i, str(s), (i+s) % 64] for i in range(64) for s in range(64)]}
        count_words(dense, 4096)


def _weighted(dfa=None):
    dfa = dfa or _no_adjacent_ones()
    return {"dfa": dfa, "weights": [[a, symbol, 2 if symbol == "1" else -1]
                                     for a, symbol, _ in dfa["transitions"]],
            "initial_weight": 3,
            "final_weights": [[state, state - 1] for state in dfa["accepting"]]}


def _brute_optimum(automaton, horizon, sense):
    dfa = automaton["dfa"]
    edges = {(a, symbol): b for a, symbol, b in dfa["transitions"]}
    weights = {(a, symbol): value for a, symbol, value in automaton["weights"]}
    finals = dict(automaton.get("final_weights", [[s, 0] for s in dfa["accepting"]]))
    values = []
    for n in range(horizon + 1):
        for word in product(dfa["alphabet"], repeat=n):
            state, value = dfa["initial"], automaton.get("initial_weight", 0)
            for symbol in word:
                key = state, symbol
                if key not in edges:
                    break
                value += weights[key]
                state = edges[key]
            else:
                if state in finals:
                    values.append(value + finals[state])
    return (max(values) if sense == "best" else min(values)) if values else None


@pytest.mark.parametrize("sense", ["best", "worst"])
def test_finite_horizon_bellman_certificate_matches_brute_force(sense):
    random = Random(427)
    for _example in range(20):
        dfa = {"alphabet": ["a", "b"], "states": [0, 1, 2], "initial": 0,
               "accepting": [s for s in range(3) if random.randrange(2)],
               "transitions": [[s, symbol, random.randrange(3)] for s in range(3)
                               for symbol in ["a", "b"] if random.randrange(3)]}
        automaton = {"dfa": dfa, "weights": [[a, symbol, random.randrange(-5, 6)]
                                             for a, symbol, _ in dfa["transitions"]]}
        witness = export_extremal_witness(automaton, 4, sense)
        assert witness["value"] == _brute_optimum(automaton, 4, sense)
        ok, evidence = check_automata_extremal(witness)
        assert ok is True and evidence["bellman_checked"]
        assert extremal_claim_subject_ref(witness)["predicate"] == evidence["predicate"]


@pytest.mark.parametrize("field", ["value", "labels", "history", "instance_digest", "feasible"])
def test_bellman_or_attainment_tampering_is_refuted(field):
    witness = export_extremal_witness(_weighted(), 4)
    if field == "value":
        witness[field] -= 1
    elif field == "labels":
        witness[field][1][1] = None
    elif field == "history":
        witness[field] = []
    elif field == "instance_digest":
        witness[field] = "0" * 64
    else:
        witness[field], witness["value"], witness["history"] = False, None, []
    assert check_automata_extremal(witness)[0] is False


def test_empty_word_is_a_candidate_and_horizon_is_part_of_proposition():
    automaton = {"dfa": {"alphabet": ["a"], "states": [0], "initial": 0,
                         "accepting": [0], "transitions": [[0, "a", 0]]},
                 "weights": [[0, "a", -2]]}
    best = export_extremal_witness(automaton, 7, "best")
    worst = export_extremal_witness(automaton, 7, "worst")
    assert best["value"] == 0 and best["history"] == []
    assert worst["value"] == -14 and worst["history"] == ["a"] * 7
    assert check_automata_extremal(best)[0] is True
    assert check_automata_extremal(worst)[0] is True
    worst["horizon"] = 0
    assert check_automata_extremal(worst)[0] is None


def test_infeasibility_requires_closed_reachability_proof():
    automaton = {"dfa": {"alphabet": ["a"], "states": [0, 1], "initial": 0,
                         "accepting": [1], "transitions": [[0, "a", 1]]},
                 "weights": [[0, "a", 3]]}
    empty = export_extremal_witness(automaton, 0)
    assert empty["feasible"] is False and check_automata_extremal(empty)[0] is True
    forged = export_extremal_witness(automaton, 1)
    forged.update(feasible=False, value=None, history=[], labels=[[0, None], [None, None]])
    ok, evidence = check_automata_extremal(forged)
    assert ok is False and "reachable successor" in evidence["reason"]


def test_nondeterministic_or_float_weighted_problem_is_unverified():
    witness = export_extremal_witness(_weighted(), 3)
    witness["automaton"]["weights"][0][2] = 1.5
    assert check_automata_extremal(witness)[0] is None
    with pytest.raises(ValueError, match="integer"):
        export_extremal_witness({"dfa": _no_adjacent_ones(),
                                 "weights": [[0, "0", 1.5], [0, "1", 0], [1, "0", 0]]}, 1)


def test_adversarial_integer_and_symbol_sizes_are_bounded():
    witness = export_count_witness(_no_adjacent_ones(), 2)
    witness["counts"][1] = 1 << 8192
    assert check_automata_count(witness)[0] is None
    witness = export_extremal_witness(_weighted(), 2)
    witness["labels"][1][0] = 1 << 8192
    assert check_automata_extremal(witness)[0] is None
    with pytest.raises(ValueError, match="alphabet"):
        canonical_dfa({"states": [0], "alphabet": ["a" * 1025], "initial": 0,
                       "accepting": [], "transitions": []})


def _wide_weighted(initial_weight=0):
    n = 256
    transitions = [[0, str(i), i] for i in range(n)] + [[i, "0", i] for i in range(1, n)]
    return {"dfa": {"states": list(range(n)), "alphabet": [str(i) for i in range(n)],
                    "initial": 0, "accepting": list(range(n)), "transitions": transitions},
            "weights": [[source, symbol, 0] for source, symbol, _ in transitions],
            "initial_weight": initial_weight}


def test_large_bellman_proof_is_rejected_before_allocating_dp(monkeypatch):
    problem = automata_count._weighted_problem

    class NoAllocation(dict):
        def __getitem__(self, _key):
            raise AssertionError("producer started DP before rejecting the aggregate budget")

    def guarded_problem(*args):
        snapshot, index, edges, finals = problem(*args)
        return snapshot, NoAllocation(index), edges, finals

    monkeypatch.setattr(automata_count, "_weighted_problem", guarded_problem)
    # Without aggregate preflight this allowed input would carry ~2.5GB JSON.
    with pytest.raises(ValueError, match="aggregate integer-bit preflight"):
        export_extremal_witness(_wide_weighted(1 << 8000), 4096)


def test_maximum_state_horizon_with_small_labels_fits_preflight():
    snapshot = automata_count.canonical_weighted_dfa(_wide_weighted())
    automata_count._bellman_preflight(snapshot, 4096)


def test_aggregate_counts_and_bellman_labels_are_bounded_without_serializing():
    count = export_count_witness(_no_adjacent_ones(), 1)
    count["counts"] = [1 << 8000] * 2100
    ok, evidence = check_automata_count(count)
    assert ok is None and "aggregate integer-bit" in evidence["error"]
    bound = export_extremal_witness(_weighted(), 1)
    bound["labels"] = [[1 << 8000] * 256] * 9
    ok, evidence = check_automata_extremal(bound)
    assert ok is None and "aggregate integer-bit" in evidence["error"]


def test_wire_size_budget_is_checked_without_serializing_the_whole_object(monkeypatch):
    witness = export_count_witness(_no_adjacent_ones(), 1)
    witness["display"] = "a" * (automata_count.MAX_WITNESS_BYTES + 1)
    original = automata_count.json.dumps

    def no_large_serialization(value, **kwargs):
        if isinstance(value, str) and len(value) > automata_count.MAX_WITNESS_BYTES:
            raise AssertionError("attempted to serialize oversized witness field")
        return original(value, **kwargs)

    monkeypatch.setattr(automata_count.json, "dumps", no_large_serialization)
    ok, evidence = check_automata_count(witness)
    assert ok is None and "wire-size" in evidence["error"]
