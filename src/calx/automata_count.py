"""Exact accepted-word enumeration with portable, immutable DFA snapshots.

Counts include the empty word at index zero. Missing transitions reject; this
is a deterministic *word* count, never an NFA run count. Optional recurrences
are checked by a matrix polynomial identity, which proves them at every length
rather than merely fitting the supplied prefix. No database or solver required.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import Any

MAX_STATES = 256
MAX_TRANSITIONS = 16_384
MAX_LENGTH = 4096
MAX_MATRIX_WORK = 8_000_000
MAX_POLYNOMIAL_DEGREE = 128
MAX_INTEGER_BITS = 8192
MAX_SYMBOL_LENGTH = 1024
MAX_WITNESS_BYTES = 16_000_000
MAX_AGGREGATE_BITS = 16_000_000
MAX_JSON_DEPTH = 32


def _integer(value: Any) -> bool:
    return type(value) is int and value.bit_length() <= MAX_INTEGER_BITS


def _proof_size(value: Any) -> tuple[int, int]:
    """Bound aggregate wire bytes and integer bits without serializing a proof.

    Individual strings/integers are rendered in isolation; a malicious large
    matrix or label table never causes a second full-sized JSON allocation.
    """
    wire_bytes = integer_bits = 0

    def visit(item, depth):
        nonlocal wire_bytes, integer_bits
        if depth > MAX_JSON_DEPTH:
            raise ValueError("automata witness nesting budget exceeded")
        if item is None:
            wire_bytes += 4
        elif type(item) is bool:
            wire_bytes += 4 if item else 5
        elif type(item) is int:
            if not _integer(item):
                raise ValueError("automata witness integer-size budget exceeded")
            integer_bits += max(1, item.bit_length())
            wire_bytes += len(str(item))
        elif isinstance(item, str):
            if len(item) > MAX_WITNESS_BYTES:
                raise ValueError("automata witness wire-size budget exceeded")
            wire_bytes += len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
        elif isinstance(item, (list, tuple)):
            wire_bytes += 2 + max(0, len(item) - 1)
            for child in item:
                visit(child, depth + 1)
        elif isinstance(item, dict):
            wire_bytes += 2 + max(0, len(item) - 1) + len(item)
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError("automata witness object keys must be strings")
                visit(key, depth + 1)
                visit(child, depth + 1)
        else:
            raise ValueError("automata witnesses require exact JSON values")
        if wire_bytes > MAX_WITNESS_BYTES:
            raise ValueError("automata witness wire-size budget exceeded")
        if integer_bits > MAX_AGGREGATE_BITS:
            raise ValueError("automata witness aggregate integer-bit budget exceeded")

    visit(value, 0)
    return wire_bytes, integer_bits


def canonical_dfa(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate and return an owned canonical copy; never modify the input."""
    if not isinstance(snapshot, dict):
        raise ValueError("dfa must be an object")
    states = snapshot.get("states")
    alphabet = snapshot.get("alphabet")
    accepting = snapshot.get("accepting")
    transitions = snapshot.get("transitions")
    if (not isinstance(states, list) or not states or len(states) > MAX_STATES
            or any(not _integer(s) for s in states) or len(set(states)) != len(states)):
        raise ValueError(f"states must be distinct integers (1..{MAX_STATES} states)")
    if (not isinstance(alphabet, list) or len(alphabet) > MAX_TRANSITIONS
            or any(not isinstance(a, str) or not 1 <= len(a) <= MAX_SYMBOL_LENGTH for a in alphabet)
            or len(set(alphabet)) != len(alphabet)):
        raise ValueError("alphabet must contain distinct nonempty strings")
    state_set = set(states)
    symbol_set = set(alphabet)
    initial = snapshot.get("initial")
    if not _integer(initial) or initial not in state_set:
        raise ValueError("initial must name one state")
    if (not isinstance(accepting, list) or any(not _integer(s) or s not in state_set
                                            for s in accepting)
            or len(set(accepting)) != len(accepting)):
        raise ValueError("accepting must be distinct declared states")
    if not isinstance(transitions, list) or len(transitions) > MAX_TRANSITIONS:
        raise ValueError(f"transitions must be an array of at most {MAX_TRANSITIONS} edges")
    owned = []
    seen = set()
    for edge in transitions:
        if not isinstance(edge, (list, tuple)) or len(edge) != 3:
            raise ValueError("transition must be [from, symbol, to]")
        source, symbol, target = edge
        if (not _integer(source) or not _integer(target)
                or source not in state_set or target not in state_set
                or not isinstance(symbol, str) or symbol not in symbol_set):
            raise ValueError("transition references undeclared state or symbol")
        if (source, symbol) in seen:
            raise ValueError("DFA has duplicate or nondeterministic transitions")
        seen.add((source, symbol))
        owned.append([source, symbol, target])
    return {"alphabet": sorted(alphabet), "states": sorted(states), "initial": initial,
            "accepting": sorted(accepting), "transitions": sorted(owned)}


def dfa_digest(snapshot: dict[str, Any]) -> str:
    dfa = canonical_dfa(snapshot)
    _proof_size(dfa)
    data = json.dumps(dfa, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def transfer_matrix(snapshot: dict[str, Any]) -> list[list[int]]:
    """A[i,j] counts symbols taking state i to j, in sorted state order."""
    dfa = canonical_dfa(snapshot)
    index = {s: i for i, s in enumerate(dfa["states"])}
    matrix = [[0] * len(index) for _ in index]
    for source, _symbol, target in dfa["transitions"]:
        matrix[index[source]][index[target]] += 1
    return matrix


def count_words(snapshot: dict[str, Any], max_length: int) -> list[int]:
    """Return exact accepted-word counts for every length in [0,max_length]."""
    dfa = canonical_dfa(snapshot)
    if not _integer(max_length) or not 0 <= max_length <= MAX_LENGTH:
        raise ValueError(f"max_length must be an integer in [0,{MAX_LENGTH}]")
    if max_length * len(dfa["transitions"]) > MAX_MATRIX_WORK:
        raise ValueError("word enumeration work budget exceeded")
    index = {s: i for i, s in enumerate(dfa["states"])}
    vector = [0] * len(index)
    vector[index[dfa["initial"]]] = 1
    finals = [index[s] for s in dfa["accepting"]]
    edges = [(index[a], index[b]) for a, _, b in dfa["transitions"]]
    counts = []
    count_bits = 0
    for length in range(max_length + 1):
        count = sum(vector[i] for i in finals)
        if not _integer(count):
            raise ValueError("word count integer-size budget exceeded")
        count_bits += max(1, count.bit_length())
        if count_bits > MAX_AGGREGATE_BITS:
            raise ValueError("word counts aggregate integer-bit budget exceeded")
        counts.append(count)
        if length == max_length:
            break
        following = [0] * len(index)
        for source, target in edges:
            following[target] += vector[source]
        vector = following
    return counts


def _annihilates(matrix: list[list[int]], polynomial: Any) -> bool:
    if (not isinstance(polynomial, list) or not 2 <= len(polynomial)
            <= MAX_POLYNOMIAL_DEGREE + 1 or any(not _integer(c) for c in polynomial)
            or polynomial[-1] == 0):
        raise ValueError("recurrence polynomial must have positive degree and integer coefficients")
    n = len(matrix)
    if n ** 3 * (len(polynomial) - 1) > MAX_MATRIX_WORK:
        raise ValueError("matrix polynomial work budget exceeded")
    # Horner: P(A); rows are multiplied by A using sparse nonzero edges.
    edges = [(i, j, c) for i, row in enumerate(matrix) for j, c in enumerate(row) if c]
    result = [[0] * n for _ in range(n)]
    for coefficient in reversed(polynomial):
        following = [[0] * n for _ in range(n)]
        for row in range(n):
            for source, target, weight in edges:
                following[row][target] += result[row][source] * weight
            following[row][row] += coefficient
        result = following
    return not any(value for row in result for value in row)


def export_count_witness(
    snapshot: dict[str, Any], max_length: int, *, annihilator: list[int] | None = None,
) -> dict[str, Any]:
    dfa = canonical_dfa(snapshot)
    _proof_size(dfa)
    matrix = transfer_matrix(dfa)
    witness = {"schema": "dfa_count.v1", "dfa": dfa, "dfa_digest": dfa_digest(dfa),
               "matrix": matrix, "counts": count_words(dfa, max_length)}
    if annihilator is not None:
        if not _annihilates(matrix, annihilator):
            raise ValueError("polynomial does not annihilate the transfer matrix")
        witness["recurrence"] = {"polynomial": list(annihilator)}
    _proof_size(witness)
    return witness


def claim_subject_ref(w: dict[str, Any]) -> dict[str, Any]:
    """Typed statement parameters for binding a count claim to its snapshot."""
    counts = w.get("counts")
    if not isinstance(counts, list) or not counts:
        raise ValueError("counts must be a nonempty array")
    return {"witness_schema": "dfa_count.v1", "instance_digest": dfa_digest(w["dfa"]),
            "predicate": {"name": "accepted_word_counts", "max_length": len(counts) - 1}}


def check_automata_count(w: dict[str, Any]) -> tuple[bool | None, dict[str, Any]]:
    """Check the snapshot, matrix, every count, and optional all-length recurrence.

    A false count/digest/matrix/identity refutes the carried assertion. Malformed
    snapshots and inputs outside resource limits are unverified.
    """
    try:
        if not isinstance(w, dict) or w.get("schema") != "dfa_count.v1":
            raise ValueError("unsupported automata count schema")
        _proof_size(w)
        dfa = canonical_dfa(w.get("dfa"))
        counts = w.get("counts")
        if (not isinstance(counts, list) or not counts or len(counts) > MAX_LENGTH + 1
                or any(not _integer(c) or c < 0 for c in counts)):
            raise ValueError("counts must be a nonempty array of nonnegative integers")
        digest = dfa_digest(dfa)
        matrix = transfer_matrix(dfa)
        supplied_matrix = w.get("matrix")
        if (not isinstance(supplied_matrix, list) or len(supplied_matrix) != len(matrix)
                or any(not isinstance(row, list) or len(row) != len(matrix)
                       or any(not _integer(c) or c < 0 for c in row)
                       for row in supplied_matrix)):
            raise ValueError("matrix must be a square array of nonnegative integers")
        recomputed = count_words(dfa, len(counts) - 1)
        matches = {"digest_matches": w.get("dfa_digest") == digest,
                   "matrix_matches": supplied_matrix == matrix,
                   "counts_match": counts == recomputed}
        if "recurrence" in w:
            recurrence = w["recurrence"]
            if not isinstance(recurrence, dict):
                raise ValueError("recurrence must be an object")
            matches["recurrence_identity"] = _annihilates(matrix, recurrence.get("polynomial"))
        return all(matches.values()), {"kernel": "dfa_count.v1", "dfa_digest": digest,
                                      "instance_digest": digest,
                                      "predicate": claim_subject_ref(w)["predicate"],
                                      "max_length": len(counts) - 1,
                                      "recomputed_counts": recomputed, **matches}
    except (ValueError, TypeError, KeyError) as exc:
        return None, {"error": str(exc)}


def product_dfa(
    left: dict[str, Any], right: dict[str, Any], op: str = "intersection", *,
    state_budget: int = MAX_STATES, transition_budget: int = MAX_TRANSITIONS,
) -> dict[str, Any]:
    """Reachable synchronous product; implicit rejecting sinks handle partial DFAs.

    Budgets are checked *before* adding states/edges; overflow raises rather
    than returning a truncated automaton that changes the counted language.
    """
    left, right = canonical_dfa(left), canonical_dfa(right)
    _proof_size(left)
    _proof_size(right)
    if left["alphabet"] != right["alphabet"]:
        raise ValueError("product alphabet mismatch")
    if op not in ("intersection", "union"):
        raise ValueError("product op must be intersection or union")
    if (not _integer(state_budget) or not 1 <= state_budget <= MAX_STATES
            or not _integer(transition_budget) or not 0 <= transition_budget <= MAX_TRANSITIONS):
        raise ValueError("product budgets exceed supported limits")
    maps = [{(a, s): b for a, s, b in dfa["transitions"]} for dfa in (left, right)]
    finals = [set(dfa["accepting"]) for dfa in (left, right)]
    initial = (left["initial"], right["initial"])
    ids = {initial: 0}
    queue = deque([initial])
    accepting, transitions = [], []
    while queue:
        pair = queue.popleft()
        source = ids[pair]
        is_final = [pair[i] in finals[i] for i in range(2)]
        if all(is_final) if op == "intersection" else any(is_final):
            accepting.append(source)
        for symbol in left["alphabet"]:
            target = tuple(maps[i].get((pair[i], symbol)) for i in range(2))
            if target not in ids:
                if len(ids) >= state_budget:
                    raise ValueError("product state budget exceeded")
                ids[target] = len(ids)
                queue.append(target)
            if len(transitions) >= transition_budget:
                raise ValueError("product transition budget exceeded")
            transitions.append([source, symbol, ids[target]])
    result = canonical_dfa({"alphabet": left["alphabet"], "states": list(ids.values()),
                            "initial": 0, "accepting": accepting, "transitions": transitions})
    _proof_size(result)
    return result


def canonical_weighted_dfa(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Integer-weighted DFA. Weights are [source,symbol,integer] triples."""
    if not isinstance(snapshot, dict):
        raise ValueError("weighted automaton must be an object")
    dfa = canonical_dfa(snapshot.get("dfa"))
    expected = {(a, symbol) for a, symbol, _ in dfa["transitions"]}
    weights = snapshot.get("weights")
    if not isinstance(weights, list) or len(weights) != len(expected):
        raise ValueError("weights must cover every DFA transition exactly once")
    found = set()
    owned_weights = []
    for item in weights:
        if (not isinstance(item, (list, tuple)) or len(item) != 3
                or not _integer(item[0]) or not isinstance(item[1], str)
                or not _integer(item[2]) or (item[0], item[1]) not in expected
                or (item[0], item[1]) in found):
            raise ValueError("invalid or duplicate integer edge weight")
        found.add((item[0], item[1]))
        owned_weights.append(list(item))
    initial_weight = snapshot.get("initial_weight", 0)
    if not _integer(initial_weight):
        raise ValueError("initial_weight must be an integer")
    finals = snapshot.get("final_weights", [[s, 0] for s in dfa["accepting"]])
    if not isinstance(finals, list) or len(finals) != len(dfa["accepting"]):
        raise ValueError("final_weights must cover every accepting state")
    seen_finals = set()
    owned_finals = []
    for item in finals:
        if (not isinstance(item, (list, tuple)) or len(item) != 2
                or not _integer(item[0]) or not _integer(item[1])
                or item[0] not in dfa["accepting"] or item[0] in seen_finals):
            raise ValueError("invalid or duplicate final weight")
        seen_finals.add(item[0])
        owned_finals.append(list(item))
    return {"dfa": dfa, "weights": sorted(owned_weights), "initial_weight": initial_weight,
            "final_weights": sorted(owned_finals)}


def weighted_dfa_digest(snapshot: dict[str, Any]) -> str:
    automaton = canonical_weighted_dfa(snapshot)
    _proof_size(automaton)
    data = json.dumps(automaton, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _weighted_problem(snapshot: dict[str, Any], horizon: Any, sense: Any):
    automaton = canonical_weighted_dfa(snapshot)
    if not _integer(horizon) or not 0 <= horizon <= MAX_LENGTH:
        raise ValueError(f"horizon must be an integer in [0,{MAX_LENGTH}]")
    if sense not in ("best", "worst"):
        raise ValueError("sense must be best or worst")
    dfa = automaton["dfa"]
    if horizon * len(dfa["transitions"]) > MAX_MATRIX_WORK:
        raise ValueError("Bellman certificate work budget exceeded")
    index = {s: i for i, s in enumerate(dfa["states"])}
    weights = {(a, symbol): weight for a, symbol, weight in automaton["weights"]}
    edges = [(index[a], symbol, index[b], weights[a, symbol])
             for a, symbol, b in dfa["transitions"]]
    finals = [(index[state], weight) for state, weight in automaton["final_weights"]]
    return automaton, index, edges, finals


def _bellman_preflight(automaton: dict[str, Any], horizon: int) -> None:
    """Conservative resource bound before allocating any horizon label table."""
    snapshot_bytes, snapshot_bits = _proof_size(automaton)
    max_edge = max((abs(weight) for _, _, weight in automaton["weights"]), default=0)
    label_magnitude = abs(automaton["initial_weight"]) + horizon * max_edge
    max_terminal = max((abs(weight) for _, weight in automaton["final_weights"]), default=0)
    if not _integer(label_magnitude + max_terminal):
        raise ValueError("Bellman value integer-size preflight budget exceeded")
    cells = (horizon + 1) * len(automaton["dfa"]["states"])
    label_bits = max(1, label_magnitude.bit_length())
    if snapshot_bits + cells * label_bits > MAX_AGGREGATE_BITS:
        raise ValueError("Bellman labels aggregate integer-bit preflight budget exceeded")
    # Each integer includes a possible minus sign and delimiter; null uses
    # five bytes with its delimiter. Account for rows, history, and headers.
    cell_bytes = max(5, len(str(label_magnitude)) + 2)
    symbol_bytes = max((len(json.dumps(s, ensure_ascii=False).encode("utf-8")) + 1
                        for s in automaton["dfa"]["alphabet"]), default=0)
    estimated_bytes = (snapshot_bytes + cells * cell_bytes + (horizon + 1) * 2
                       + horizon * symbol_bytes + 4096)
    if estimated_bytes > MAX_WITNESS_BYTES:
        raise ValueError("Bellman witness wire-size preflight budget exceeded")


def extremal_claim_subject_ref(w: dict[str, Any]) -> dict[str, Any]:
    return {"witness_schema": "dfa_extremal.v1",
            "instance_digest": weighted_dfa_digest(w["automaton"]),
            "predicate": {"name": "finite_horizon_extremum", "horizon": w["horizon"],
                          "sense": w["sense"], "value": w["value"], "feasible": w["feasible"]}}


def export_extremal_witness(
    snapshot: dict[str, Any], horizon: int, sense: str = "best",
) -> dict[str, Any]:
    """Find a finite-horizon integer-weighted optimum and carry Bellman labels.

    The consumer checks local inequalities on these labels and replays the
    attaining word. It does not rerun this optimization algorithm.
    """
    automaton, index, edges, finals = _weighted_problem(snapshot, horizon, sense)
    _bellman_preflight(automaton, horizon)
    initial = index[automaton["dfa"]["initial"]]
    labels = [[None] * len(index)]
    labels[0][initial] = automaton["initial_weight"]
    previous = [{}]
    for _step in range(horizon):
        following = [None] * len(index)
        predecessors = {}
        for source, symbol, target, weight in edges:
            if labels[-1][source] is None:
                continue
            value = labels[-1][source] + weight
            if not _integer(value):
                raise ValueError("Bellman label integer-size budget exceeded")
            current = following[target]
            if current is None or (value > current if sense == "best" else value < current):
                following[target] = value
                predecessors[target] = (source, symbol)
        labels.append(following)
        previous.append(predecessors)
    optimum = None
    end = None
    for step, row in enumerate(labels):
        for state, final_weight in finals:
            if row[state] is None:
                continue
            candidate = row[state] + final_weight
            if not _integer(candidate):
                raise ValueError("extremal value integer-size budget exceeded")
            if optimum is None or (candidate > optimum if sense == "best" else candidate < optimum):
                optimum, end = candidate, (step, state)
    history = []
    if end is not None:
        step, state = end
        while step:
            state, symbol = previous[step][state]
            history.append(symbol)
            step -= 1
        history.reverse()
    witness = {"schema": "dfa_extremal.v1", "automaton": automaton,
               "instance_digest": weighted_dfa_digest(automaton),
               "horizon": horizon, "sense": sense, "value": optimum,
               "feasible": optimum is not None, "labels": labels, "history": history}
    _proof_size(witness)
    return witness


def check_automata_extremal(w: dict[str, Any]) -> tuple[bool | None, dict[str, Any]]:
    """Check a finite-horizon optimum by Bellman inequalities plus attainment.

    A null label declares a state unreachable at that length. Closure over
    every edge checks those declarations. With no labelled accepting state at
    any layer, the same finite proof certifies infeasibility within the horizon.
    """
    try:
        if not isinstance(w, dict) or w.get("schema") != "dfa_extremal.v1":
            raise ValueError("unsupported automata extremal schema")
        _proof_size(w)
        horizon, sense = w.get("horizon"), w.get("sense")
        automaton, index, edges, finals = _weighted_problem(w.get("automaton"), horizon, sense)
        labels, history = w.get("labels"), w.get("history")
        if (not isinstance(labels, list) or len(labels) != horizon + 1
                or any(not isinstance(row, list) or len(row) != len(index)
                       or any(v is not None and not _integer(v) for v in row) for row in labels)):
            raise ValueError("labels must have one integer-or-null vector per horizon layer")
        if (not isinstance(history, list) or len(history) > horizon
                or any(not isinstance(symbol, str) for symbol in history)):
            raise ValueError("history must be a word within the horizon")
        feasible, value = w.get("feasible"), w.get("value")
        if type(feasible) is not bool or (feasible and not _integer(value)) \
                or (not feasible and value is not None):
            raise ValueError("feasible extrema require an integer value; infeasible requires null")
        digest = weighted_dfa_digest(automaton)
        evidence = {"kernel": "dfa_extremal.v1", "instance_digest": digest,
                    "predicate": extremal_claim_subject_ref(w)["predicate"]}

        def rejected(reason):
            return False, {**evidence, "reason": reason}

        if w.get("instance_digest") != digest:
            return rejected("weighted snapshot digest mismatch")
        initial = index[automaton["dfa"]["initial"]]
        expected = [None] * len(index)
        expected[initial] = automaton["initial_weight"]
        if labels[0] != expected:
            return rejected("initial Bellman labels do not match the initial state")
        for step in range(horizon):
            for source, _symbol, target, weight in edges:
                if labels[step][source] is None:
                    continue
                following = labels[step + 1][target]
                if following is None:
                    return rejected("reachable successor has a null label")
                candidate = labels[step][source] + weight
                if (following < candidate if sense == "best" else following > candidate):
                    return rejected("Bellman edge inequality violated")
        for row in labels:
            for state, final_weight in finals:
                if row[state] is None:
                    continue
                if not feasible:
                    return rejected("infeasibility certificate labels an accepting state reachable")
                bound = row[state] + final_weight
                if (value < bound if sense == "best" else value > bound):
                    return rejected("claimed optimum violates a terminal Bellman bound")
        if feasible:
            dfa = automaton["dfa"]
            transitions = {(a, symbol): b for a, symbol, b in dfa["transitions"]}
            weights = {(a, symbol): v for a, symbol, v in automaton["weights"]}
            state, total = dfa["initial"], automaton["initial_weight"]
            for symbol in history:
                key = state, symbol
                if key not in transitions:
                    return rejected("attaining history has a missing transition")
                total += weights[key]
                state = transitions[key]
            final_weights = dict(automaton["final_weights"])
            if state not in final_weights or total + final_weights[state] != value:
                return rejected("attaining history does not realise the claimed value")
        elif history:
            return rejected("infeasibility has no attaining word")
        return True, {**evidence, "bellman_checked": True, "attainment_checked": feasible,
                      "infeasibility_checked": not feasible}
    except (ValueError, TypeError, KeyError) as exc:
        return None, {"error": str(exc)}
