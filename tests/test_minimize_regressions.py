"""Correctness and bounded-work regressions for large sparse DFA construction.

The scale cases intentionally run in ordinary database CI: tiny DFAs did not
expose the construction/minimization failures. PostgreSQL statement timeouts
stop a regression while it is still running, including a CPU-bound backend.
"""

from __future__ import annotations

import random
import string
import time
import uuid
from collections import deque

import pytest

from nerode.automata import export_from_db

SCALE_BUDGET_SECONDS = 120


def _create_dfa(conn, alphabet, states, transitions):
    """Bulk load a scratch DFA without charging import overhead to the budget."""
    name = "min-regression-" + uuid.uuid4().hex
    alphabet_id = conn.execute(
        "INSERT INTO nerode.alphabets(name,symbols) VALUES (%s,%s) RETURNING id",
        (name, alphabet),
    ).fetchone()[0]
    auto_id = conn.execute(
        "INSERT INTO nerode.automata(name,type,alphabet_id,state_count) "
        "VALUES (%s,'DFA',%s,%s) RETURNING id",
        (name, alphabet_id, len(states)),
    ).fetchone()[0]
    with conn.cursor() as cur:
        with cur.copy(
            "COPY nerode.states(automaton_id,state_id,is_initial,is_accepting) FROM STDIN"
        ) as copy:
            for state_id, initial, accepting in states:
                copy.write_row((auto_id, state_id, initial, accepting))
        with cur.copy(
            "COPY nerode.transitions(automaton_id,from_state,symbol,to_state) FROM STDIN"
        ) as copy:
            for source, symbol, target in transitions:
                copy.write_row((auto_id, source, symbol, target))
    return auto_id


def _model(data):
    initial = next(s["id"] for s in data["states"] if s["is_initial"])
    accepting = {s["id"] for s in data["states"] if s["is_accepting"]}
    transitions = {(t["from"], t["symbol"]): t["to"] for t in data["transitions"]}
    return initial, accepting, transitions


def _reachable(data):
    initial, _, transitions = _model(data)
    seen = {initial}
    todo = [initial]
    for state in todo:
        for symbol in data["alphabet"]:
            target = transitions.get((state, symbol))
            if target is not None and target not in seen:
                seen.add(target)
                todo.append(target)
    return seen


def _assert_equivalent(source, result):
    """Exhaust the reachable product, with None as the rejecting missing edge."""
    left, left_accepting, left_edges = _model(source)
    right, right_accepting, right_edges = _model(result)
    seen = {(left, right)}
    todo = deque(seen)
    while todo:
        left, right = todo.popleft()
        assert (left in left_accepting) == (right in right_accepting), (left, right)
        for symbol in source["alphabet"]:
            pair = (left_edges.get((left, symbol)), right_edges.get((right, symbol)))
            if pair not in seen:
                seen.add(pair)
                todo.append(pair)


def _reference_state_count(data):
    """Independent Moore refinement, only for small randomized test inputs."""
    _, accepting, transitions = _model(data)
    states = _reachable(data)
    if any((state, symbol) not in transitions
           for state in states for symbol in data["alphabet"]):
        states.add(None)
    blocks = {state: int(state in accepting) for state in states}
    while True:
        signatures = {}
        refined = {}
        for state in states:
            signature = (state in accepting, tuple(
                blocks[transitions.get((state, symbol))] for symbol in data["alphabet"]
            ))
            refined[state] = signatures.setdefault(signature, len(signatures))
        if len(set(refined.values())) == len(set(blocks.values())):
            return len(signatures)
        blocks = refined


def _assert_result(conn, source_id, result_id, before, expected_count=None):
    result = export_from_db(conn, result_id)
    assert export_from_db(conn, source_id) == before
    ids = [s["id"] for s in result["states"]]
    assert ids == list(range(len(ids)))
    assert sum(s["is_initial"] for s in result["states"]) == 1
    assert len(result["transitions"]) == len(ids) * len(result["alphabet"])
    assert len({(t["from"], t["symbol"]) for t in result["transitions"]}) == len(
        result["transitions"]
    )
    state_count = conn.execute(
        "SELECT state_count FROM nerode.automata WHERE id=%s", (result_id,)
    ).fetchone()[0]
    assert state_count == len(ids)
    if expected_count is not None:
        assert state_count == expected_count
    log = conn.execute(
        "SELECT result FROM nerode.construction_log "
        "WHERE automaton_id=%s AND operation='minimize' ORDER BY id DESC LIMIT 1",
        (result_id,),
    ).fetchone()[0]
    partition = log["partition"]
    members = [member for block in partition.values() for member in block]
    assert len(members) == len(set(members))
    reachable = _reachable(before)
    assert set(members) == reachable
    assert set(map(int, partition)) == set(ids)
    assert log["algorithm"] == "hopcroft"
    assert log["reachable_states"] == len(reachable)
    assert log["unreachable_states"] == len(before["states"]) - len(reachable)
    _, _, edges = _model(before)
    needs_sink = any((state, symbol) not in edges
                     for state in reachable for symbol in before["alphabet"])
    if needs_sink:
        sink = log["implicit_sink"]
        assert sink in ids
        assert not result["states"][sink]["is_accepting"]
        assert all(t["to"] == sink for t in result["transitions"] if t["from"] == sink)
    else:
        assert log["implicit_sink"] is None
    _assert_equivalent(before, result)
    return result


@pytest.mark.parametrize(
    ("alphabet", "states", "transitions", "expected"),
    [
        (["a", "b"], [(-9, True, False)], [], 1),
        (["a", "b"], [(-9, True, True)], [], 2),
        (["a"], [(-9, True, True)], [(-9, "a", -9)], 1),
        ([], [(-9, True, False), (77, False, True)], [], 1),
        ([], [(-9, True, True)], [], 1),
        (
            ["a", "b"],
            [(-9, True, False), (27, False, True), (99, False, True)],
            [(-9, "a", 27), (99, "a", 99), (99, "b", 99)],
            3,
        ),
        (
            ["a"],
            [(-9, True, False), (27, False, False), (99, False, False)],
            [(-9, "a", 27), (27, "a", 99), (99, "a", 99)],
            1,
        ),
    ],
)
def test_sparse_minimize_edge_cases(conn, alphabet, states, transitions, expected):
    source_id = _create_dfa(conn, alphabet, states, transitions)
    before = export_from_db(conn, source_id)
    result_id = conn.execute("SELECT nerode.minimize(%s)", (source_id,)).fetchone()[0]
    _assert_result(conn, source_id, result_id, before, expected)


@pytest.mark.parametrize("seed", range(16))
def test_partial_dfas_match_independent_refinement(conn, seed):
    rng = random.Random(seed)
    alphabet = ["a", "b", "c"]
    ids = [-11, 3, 8, 19, 24, 51, 78, 91, 101, 130, 201, 999]
    states = [(state, i == 0, rng.choice([False, True])) for i, state in enumerate(ids)]
    transitions = [(state, symbol, rng.choice(ids))
                   for state in ids for symbol in alphabet if rng.random() < 0.75]
    source_id = _create_dfa(conn, alphabet, states, transitions)
    before = export_from_db(conn, source_id)
    result_id = conn.execute("SELECT nerode.minimize(%s)", (source_id,)).fetchone()[0]
    _assert_result(conn, source_id, result_id, before, _reference_state_count(before))


@pytest.mark.parametrize(
    ("states", "transitions", "message"),
    [
        ([(0, False, False)], [], "exactly one initial state"),
        ([(0, True, False), (1, True, True)], [], "exactly one initial state"),
        ([], [], "exactly one initial state"),
        (
            [(0, True, False), (1, False, True)],
            [(0, "a", 0), (0, "a", 1)],
            "nondeterministic",
        ),
        ([(0, True, False)], [(0, None, 0)], "epsilon or out-of-alphabet"),
        ([(0, True, False)], [(0, "z", 0)], "epsilon or out-of-alphabet"),
    ],
)
def test_malformed_dfas_are_rejected(conn, states, transitions, message):
    source_id = _create_dfa(conn, ["a"], states, transitions)
    with pytest.raises(Exception, match=message), conn.transaction():
        conn.execute("SELECT nerode.minimize(%s)", (source_id,))


def test_certified_partition_preserves_original_ids_and_virtual_sink(conn):
    source_id = _create_dfa(
        conn, ["a", "b"],
        [(-9, True, False), (27, False, True), (99, False, True)],
        [(-9, "a", 27), (99, "a", 99), (99, "b", 99)],
    )
    before = export_from_db(conn, source_id)
    result_id, claim_id = conn.execute(
        "SELECT automaton_id,claim_id FROM nerode.minimize_certified(%s)", (source_id,)
    ).fetchone()
    result = _assert_result(conn, source_id, result_id, before, 3)
    witness = conn.execute(
        "SELECT w.body FROM cert.witness w JOIN cert.certificate c ON c.id=w.certificate_id "
        "WHERE c.claim_id=%s AND w.kind='nerode_partition' ORDER BY c.seq DESC LIMIT 1",
        (claim_id,),
    ).fetchone()[0]
    assert witness["source_id"] == source_id
    assert witness["automaton_id"] == result_id
    assert witness["reachable_states"] == 2
    assert witness["unreachable_states"] == 1
    assert witness["minimal_states"] == len(result["states"])
    assert set(member for block in witness["partition"].values() for member in block) == {-9, 27}
    assert witness["partition"][str(witness["implicit_sink"])] == []


@pytest.mark.parametrize("complete", [False, True], ids=["partial", "complete"])
def test_canonical_output_is_independent_of_input_state_ids(conn, complete):
    states = [(-9, True, False), (27, False, True), (99, False, True), (150, False, False)]
    transitions = [(-9, "a", 27), (-9, "b", 99), (27, "a", 150),
                   (99, "a", 99), (150, "b", 27)]
    if complete:
        states.append((501, False, False))
        edges = {(source, symbol): target for source, symbol, target in transitions}
        transitions = [(state, symbol, edges.get((state, symbol), 501))
                       for state, _, _ in states for symbol in ["a", "b"]]
    source_id = _create_dfa(conn, ["a", "b"], states, transitions)
    labels = {-9: 1001, 27: -220, 99: 3, 150: -70, 501: 640}
    other_id = _create_dfa(
        conn, ["b", "a"],
        [(labels[state], initial, accepting) for state, initial, accepting in reversed(states)],
        [(labels[source], symbol, labels[target])
         for source, symbol, target in reversed(transitions)],
    )
    minimized = []
    for auto_id in [source_id, other_id]:
        result_id = conn.execute("SELECT nerode.minimize(%s)", (auto_id,)).fetchone()[0]
        result = export_from_db(conn, result_id)
        initial = [state for state in result["states"] if state["is_initial"]]
        assert len(initial) == 1
        assert initial[0]["id"] == 0
        assert initial[0]["label"] == "q0"
        result.pop("name")
        minimized.append(result)
    assert minimized[0] == minimized[1]


def _vocabulary():
    rng = random.Random(2826)
    words = set()
    while len(words) < 3000:
        length = rng.choice([5, 6])
        words.add("".join(rng.choices(string.ascii_lowercase, k=length)))
    return sorted(words)


def _trie(words):
    edges = {}
    accepting = set()
    state_count = 1
    for word in words:
        state = 0
        for symbol in word:
            if (state, symbol) not in edges:
                edges[state, symbol] = state_count
                state_count += 1
            state = edges[state, symbol]
        accepting.add(state)
    states = [(state, state == 0, state in accepting) for state in range(state_count)]
    transitions = [(state, symbol, target) for (state, symbol), target in edges.items()]
    # Acyclic DFAs admit an independent bottom-up right-language signature.
    children = [[] for _ in states]
    for state, symbol, target in transitions:
        children[state].append((symbol, target))
    classes = {}
    signature_ids = {}
    for state in reversed(range(state_count)):
        signature = (state in accepting, tuple(
            (symbol, signature_ids[target]) for symbol, target in sorted(children[state])
        ))
        signature_ids[state] = classes.setdefault(signature, len(classes))
    return states, transitions, len(classes) + 1  # one implicit rejecting sink


def test_ten_thousand_state_trie_minimizes_within_budget(conn, request):
    words = _vocabulary()
    states, transitions, expected = _trie(words)
    assert 10000 <= len(states) <= 12000
    source_id = _create_dfa(conn, list(string.ascii_lowercase), states, transitions)
    before = export_from_db(conn, source_id)
    conn.execute("SET LOCAL statement_timeout = '120s'")
    started = time.monotonic()
    result_id = conn.execute("SELECT nerode.minimize(%s)", (source_id,)).fetchone()[0]
    elapsed = time.monotonic() - started
    request.node.user_properties.extend([
        ("minimize_seconds", elapsed), ("input_states", len(states)),
        ("output_states", expected), ("output_transitions", expected * 26),
    ])
    assert elapsed < SCALE_BUDGET_SECONDS
    _assert_result(conn, source_id, result_id, before, expected)


def test_three_thousand_word_regex_builds_within_budget(conn, request):
    words = _vocabulary()
    _, _, expected = _trie(words)
    pattern = "(" + "|".join(words) + ")"
    conn.execute("SET LOCAL statement_timeout = '120s'")
    started = time.monotonic()
    result_id = conn.execute("SELECT nerode.from_regex(%s)", (pattern,)).fetchone()[0]
    elapsed = time.monotonic() - started
    request.node.user_properties.extend([
        ("build_seconds", elapsed), ("output_states", expected),
        ("output_transitions", expected * 26),
    ])
    assert elapsed < SCALE_BUDGET_SECONDS
    result = export_from_db(conn, result_id)
    assert len(result["states"]) == expected
    initial, accepting, edges = _model(result)
    for word in words:
        state = initial
        for symbol in word:
            state = edges[state, symbol]
        assert state in accepting
    # Exhaustive equivalence also excludes unwanted words and prefixes.
    states, transitions, _ = _trie(words)
    source = {
        "alphabet": list(string.ascii_lowercase),
        "states": [{"id": state, "is_initial": initial, "is_accepting": accepting}
                   for state, initial, accepting in states],
        "transitions": [{"from": state, "symbol": symbol, "to": target}
                        for state, symbol, target in transitions],
    }
    _assert_equivalent(source, result)
