"""Portable finite-combinatorics proofs: no database, solver, or third party."""

from __future__ import annotations

import json
from copy import deepcopy
from itertools import combinations, product

import pytest

from calx.combinatorial import (
    CNF_ENCODING,
    c5_chromatic_example,
    check_combinatorial,
    check_rup,
    claim_subject_ref,
    disjoint_union_witness,
    export_witness,
    graph_colouring_cnf,
    import_instance,
    instance_digest,
    make_witness,
    normalize_instance,
    relabel_witness,
)


def graph(n, edges):
    return {"kind": "graph", "ground_n": n, "blocks": [list(e) for e in edges]}


C5 = graph(5, [(0, 1), (1, 2), (2, 3), (3, 4), (4, 0)])
K3 = graph(3, [(0, 1), (1, 2), (0, 2)])
P4 = graph(4, [(0, 1), (1, 2), (2, 3)])


def checked(instance, predicate, certificate=None):
    return check_combinatorial(make_witness(instance, predicate, certificate))


def test_normalization_is_detached_and_deterministic(tmp_path):
    raw = graph(3, [(2, 0), (2, 1)])
    snapshot = normalize_instance(raw)
    assert snapshot["blocks"][0]["elements"] == [0, 2]
    assert snapshot["elements"] == [0, 1, 2]
    assert instance_digest(raw) == instance_digest(snapshot)
    raw["blocks"][0].append(1)
    assert snapshot["blocks"][0]["elements"] == [0, 2]
    witness = make_witness(snapshot, "triangle_free")
    path = tmp_path / "certificate.json"
    assert json.loads(export_witness(witness, path)) == witness
    assert import_instance(path) == snapshot
    assert import_instance(export_witness(witness)) == snapshot
    assert claim_subject_ref(witness) == {
        "witness_schema": "comb.v1",
        "instance_digest": instance_digest(snapshot),
        "predicate": {"name": "triangle_free"},
    }


def test_digraph_direction_survives_normalization_and_isomorphism():
    raw = {"kind": "digraph", "ground_n": 2, "blocks": [{"elements": [1, 0], "positions": [0, 1]}]}
    source = normalize_instance(raw)
    assert source["blocks"][0]["positions"] == [1, 0]
    target = {
        "kind": "digraph",
        "ground_n": 2,
        "blocks": [{"elements": [0, 1], "positions": [0, 1]}],
    }
    pred = {"name": "isomorphism", "target_digest": instance_digest(target)}
    assert checked(source, pred, {"target": target, "mapping": [1, 0]})[0] is True
    assert checked(source, pred, {"target": target, "mapping": [0, 1]})[0] is None


@pytest.mark.parametrize(
    "instance",
    [
        {"kind": "graph", "ground_n": 3, "elements": [0, 2], "blocks": []},
        graph(2, [(0,)]),
        graph(2, [(0, 1), (1, 0)]),
        graph(2, [()]),
        graph(2, [(0, 2)]),
    ],
)
def test_wellformed_refutes_total_shape_faults(instance):
    assert checked(instance, "wellformed")[0] is False
    assert checked(instance, "triangle_free")[0] is None


def test_properties_name_counterexamples_and_cover_empty_families():
    ok, evidence = checked(K3, "triangle_free")
    assert ok is False and evidence["triangle"] == [0, 1, 2]
    assert checked(C5, "triangle_free")[0] is True
    assert checked(C5, {"name": "uniform", "k": 2})[0] is True
    assert checked(C5, {"name": "uniform", "k": 3})[0] is False
    family = {"kind": "set_system", "ground_n": 3, "blocks": [[0], [0, 1], [2]]}
    assert checked(family, "primitive_family")[0] is False
    assert checked(family, "intersecting_family")[0] is False
    assert checked(graph(0, []), "triangle_free")[0] is True
    repeated = {"kind": "set_system", "ground_n": 2, "blocks": [[0], [0]]}
    assert checked(repeated, "primitive_family")[0] is True


def test_integer_primitive_sets_have_exact_positive_labels():
    good = {"kind": "primitive_set", "values": ["25", "0006", "10"]}
    assert normalize_instance(good)["values"] == ["6", "10", "25"]
    assert checked(good, "primitive_family")[0] is True
    assert checked({"kind": "primitive_set", "values": ["3", "12"]}, "primitive_family")[0] is False
    for value in ("0", "-2", "1.5", "unknown", "٣", 3, True):
        assert (
            check_combinatorial(
                {
                    "schema": "comb.v1",
                    "instance": {"kind": "primitive_set", "values": [value]},
                    "predicate": {"name": "primitive_family"},
                }
            )[0]
            is None
        )


@pytest.mark.parametrize(
    "predicate,certificate",
    [
        ({"name": "colouring", "k": 3}, {"colouring": [0, 1, 0, 1, 2]}),
        ({"name": "independent_set", "k": 2}, {"vertices": [0, 2]}),
        ({"name": "clique", "k": 2}, {"vertices": [0, 1]}),
        ({"name": "odd_cycle"}, {"vertices": [0, 1, 2, 3, 4]}),
        ({"name": "matching", "k": 2}, {"matching": [[0, 1], [2, 3]]}),
        ({"name": "vertex_cover", "k": 3}, {"vertices": [0, 2, 4]}),
        ({"name": "hitting_set", "k": 3}, {"vertices": [0, 2, 4]}),
    ],
)
def test_positive_substructure_certificates(predicate, certificate):
    ok, evidence = checked(C5, predicate, certificate)
    assert ok is True and evidence["predicate"] == predicate


@pytest.mark.parametrize(
    "predicate,certificate",
    [
        ({"name": "colouring", "k": 2}, {"colouring": [0, 1, 0, 1, 0]}),
        ({"name": "colouring", "k": 5}, {"colouring": [0, 1]}),
        ({"name": "independent_set", "k": 2}, {"vertices": [0, 1]}),
        ({"name": "clique", "k": 3}, {"vertices": [0, 1, 2]}),
        ({"name": "odd_cycle"}, {"vertices": [0, 1, 2]}),
        ({"name": "matching", "k": 2}, {"matching": [[0, 1], [1, 2]]}),
        ({"name": "vertex_cover", "k": 2}, {"vertices": [0, 2]}),
        ({"name": "hitting_set", "k": 2}, {"vertices": [0, 2]}),
    ],
)
def test_bad_candidate_never_refutes_existential_proposition(predicate, certificate):
    assert checked(C5, predicate, certificate)[0] is None


def test_empty_family_members_remain_distinct_from_an_empty_family():
    family = {"kind": "set_system", "ground_n": 2, "blocks": [[], [0]]}
    assert checked(family, "wellformed")[0] is True
    assert checked(family, "primitive_family")[0] is False
    assert checked(family, "intersecting_family")[0] is False
    assert checked(family, {"name": "uniform", "k": 0})[0] is False
    assert checked(family, {"name": "colouring", "k": 2}, {"colouring": [0, 1]})[0] is None
    assert checked(family, {"name": "hitting_set", "k": 2}, {"vertices": [0, 1]})[0] is None
    assert checked(family, {"name": "independent_set", "k": 0}, {"vertices": []})[0] is None
    singleton_empty = {"kind": "hypergraph", "ground_n": 0, "blocks": [[]]}
    assert checked(singleton_empty, "wellformed")[0] is True
    assert checked(singleton_empty, "primitive_family")[0] is True
    assert checked(singleton_empty, "intersecting_family")[0] is False
    assert checked(singleton_empty, {"name": "t_intersecting", "t": 1})[0] is False
    empty_family = {"kind": "set_system", "ground_n": 2, "blocks": []}
    assert checked(empty_family, "intersecting_family")[0] is True


def test_hypergraph_colouring_and_hitting_are_checked_by_whole_blocks():
    instance = {"kind": "hypergraph", "ground_n": 4, "blocks": [[0, 1, 2], [1, 2, 3]]}
    assert checked(instance, {"name": "colouring", "k": 2}, {"colouring": [0, 0, 1, 1]})[0] is True
    assert checked(instance, {"name": "hitting_set", "k": 1}, {"vertices": [1]})[0] is True
    assert (
        checked(instance, {"name": "independent_set", "k": 3}, {"vertices": [0, 1, 3]})[0] is True
    )


def test_finite_intersection_codegree_and_design_probes():
    fano = {
        "kind": "hypergraph",
        "ground_n": 7,
        "blocks": [
            [0, 1, 2],
            [0, 3, 4],
            [0, 5, 6],
            [1, 3, 5],
            [1, 4, 6],
            [2, 3, 6],
            [2, 4, 5],
        ],
    }
    assert checked(fano, {"name": "t_design", "t": 2, "k": 3, "lambda": 1})[0] is True
    assert checked(fano, {"name": "t_intersecting", "t": 1})[0] is True
    assert checked(fano, {"name": "t_intersecting", "t": 2})[0] is False
    singleton = {"kind": "set_system", "ground_n": 2, "blocks": [[0]]}
    assert checked(singleton, {"name": "t_intersecting", "t": 2})[0] is False
    assert checked(fano, {"name": "codegree", "vertices": [0, 1], "value": 1})[0] is True
    assert checked(fano, {"name": "codegree", "vertices": [0], "value": 3})[0] is True
    assert checked(fano, {"name": "codegree_at_most", "t": 2, "max": 1})[0] is True
    assert checked(fano, {"name": "codegree_at_most", "t": 1, "max": 2})[0] is False
    missing = deepcopy(fano)
    missing["blocks"].pop()
    assert checked(missing, {"name": "t_design", "t": 2, "k": 3, "lambda": 1})[0] is False
    repeated = deepcopy(fano)
    repeated["blocks"].append([0, 1, 2])
    assert checked(repeated, {"name": "t_design", "t": 2, "k": 3, "lambda": 1})[0] is False
    assert checked(graph(4, []), {"name": "codegree", "vertices": [], "value": 0})[0] is True
    assert checked(graph(4, []), {"name": "t_design", "t": 2, "k": 3, "lambda": 0})[0] is True
    assert checked(fano, {"name": "t_design", "t": 4, "k": 3, "lambda": 1})[0] is None
    excessive = make_witness(graph(100, []), {"name": "t_design", "t": 50, "k": 50, "lambda": 0})
    assert check_combinatorial(excessive)[0] is None
    wide_subsets = make_witness(
        graph(10000, []), {"name": "t_design", "t": 9999, "k": 9999, "lambda": 0}
    )
    assert check_combinatorial(wide_subsets)[0] is None


def test_c5_chromatic_example_has_both_checked_sides():
    witness = c5_chromatic_example()
    assert check_combinatorial(witness)[0] is True
    broken = deepcopy(witness)
    broken["certificate"]["lower_bound"]["vertices"] = [0, 1, 3]
    assert check_combinatorial(broken)[0] is None
    broken = deepcopy(witness)
    broken["predicate"]["k"] = 4
    assert check_combinatorial(broken)[0] is None


def test_matching_and_vertex_cover_optimality_use_checked_dual_bounds():
    assert (
        checked(
            P4,
            {"name": "matching_exact", "k": 2},
            {"matching": [[0, 1], [2, 3]], "upper_bound": {"kind": "vertex_count"}},
        )[0]
        is True
    )
    assert (
        checked(
            P4, {"name": "vertex_cover_exact", "k": 2}, {"matching": [0, 2], "vertices": [1, 2]}
        )[0]
        is True
    )
    assert (
        checked(
            K3,
            {"name": "matching_exact", "k": 1},
            {"matching": [0], "upper_bound": {"kind": "tutte", "removed": []}},
        )[0]
        is True
    )
    assert (
        checked(
            P4,
            {"name": "matching_exact", "k": 1},
            {"matching": [0], "upper_bound": {"kind": "vertex_cover", "vertices": [1]}},
        )[0]
        is None
    )
    assert (
        checked(
            C5,
            {"name": "vertex_cover_exact", "k": 3},
            {
                "vertices": [0, 2, 4],
                "lower_bound": {"kind": "odd_cycle", "vertices": [0, 1, 2, 3, 4]},
            },
        )[0]
        is True
    )


@pytest.mark.parametrize("n", range(5))
def test_all_small_graphs_against_exhaustive_independent_oracles(n):
    """All 76 labelled simple graphs through four vertices; no solver oracle.

    Exhaust all subsets, all two-colourings and all edge subsets. Independently
    compute the optima; checked dual obstructions then establish the exact
    values. This also checks both graph operations on every instance.
    """
    universe = list(combinations(range(n), 2))
    subsets = [[v for v in range(n) if mask & (1 << v)] for mask in range(1 << n)]
    for mask in range(1 << len(universe)):
        edges = [edge for i, edge in enumerate(universe) if mask & (1 << i)]
        edge_set = set(edges)
        instance = graph(n, edges)
        cliques, covers, matchings = [], [], []
        for vertices in subsets:
            chosen = set(vertices)
            clique = all(pair in edge_set for pair in combinations(vertices, 2))
            independent = all(not ({u, v} <= chosen) for u, v in edges)
            cover = all(u in chosen or v in chosen for u, v in edges)
            if clique:
                cliques.append(vertices)
            if cover:
                covers.append(vertices)
            for name, expected in (
                ("clique", clique),
                ("independent_set", independent),
                ("vertex_cover", cover),
            ):
                result = checked(
                    instance, {"name": name, "k": len(vertices)}, {"vertices": vertices}
                )[0]
                assert (result is True) == expected, (n, mask, name, vertices)
                assert result is not False
        for colouring in product(range(2), repeat=n):
            proper = all(colouring[u] != colouring[v] for u, v in edges)
            result = checked(
                instance, {"name": "colouring", "k": 2}, {"colouring": list(colouring)}
            )[0]
            assert (result is True) == proper
        for selected_mask in range(1 << len(edges)):
            selected = [edge for i, edge in enumerate(edges) if selected_mask & (1 << i)]
            endpoints = [v for edge in selected for v in edge]
            matching = len(endpoints) == len(set(endpoints))
            if matching:
                matchings.append(selected)
            result = checked(
                instance,
                {"name": "matching", "k": len(selected)},
                {"matching": [list(edge) for edge in selected]},
            )[0]
            assert (result is True) == matching
        maximum_matching = max(matchings, key=len)
        minimum_cover = min(covers, key=len)
        maximum_clique = max(cliques, key=len)
        chromatic, optimal_colouring = 0, []
        for k in range(n + 1):
            optimum = next(
                (
                    values
                    for values in product(range(k), repeat=n)
                    if all(values[u] != values[v] for u, v in edges)
                ),
                None,
            )
            if optimum is not None:
                chromatic, optimal_colouring = k, list(optimum)
                break
        assert (
            checked(
                instance,
                {"name": "chromatic_exact", "k": chromatic},
                {
                    "colouring": optimal_colouring,
                    "lower_bound": {"kind": "clique", "vertices": maximum_clique},
                },
            )[0]
            is True
        )
        lower = {"kind": "matching", "matching": [list(e) for e in maximum_matching]}
        if len(maximum_matching) != len(minimum_cover):
            lower = {"kind": "clique", "vertices": maximum_clique}
        assert (
            checked(
                instance,
                {"name": "vertex_cover_exact", "k": len(minimum_cover)},
                {
                    "vertices": minimum_cover,
                    "lower_bound": lower,
                },
            )[0]
            is True
        )
        # Enumerate deletions independently to find a tight Tutte obstruction.
        for removed in subsets:
            unseen, odd = set(range(n)) - set(removed), 0
            while unseen:
                pending = [unseen.pop()]
                component = set(pending)
                while pending:
                    current = pending.pop()
                    adjacent = {v for v in unseen if tuple(sorted((v, current))) in edge_set}
                    unseen -= adjacent
                    component |= adjacent
                    pending.extend(adjacent)
                odd += len(component) % 2
            if (n + len(removed) - odd) // 2 == len(maximum_matching):
                break
        assert (
            checked(
                instance,
                {"name": "matching_exact", "k": len(maximum_matching)},
                {
                    "matching": [list(e) for e in maximum_matching],
                    "upper_bound": {"kind": "tutte", "removed": removed},
                },
            )[0]
            is True
        )
        premise = make_witness(instance, {"name": "uniform", "k": 2})
        assert check_combinatorial(relabel_witness(premise, list(reversed(range(n)))))[0] is True
        assert check_combinatorial(disjoint_union_witness([premise, premise]))[0] is True


def test_relabel_rules_verify_the_complete_bijection_and_nested_proof():
    witness = relabel_witness(c5_chromatic_example(), [4, 3, 2, 1, 0])
    assert check_combinatorial(witness)[0] is True
    broken = deepcopy(witness)
    broken["certificate"]["mapping"] = [0, 1, 2, 3, 3]
    assert check_combinatorial(broken)[0] is None
    broken = deepcopy(witness)
    broken["certificate"]["premise"]["certificate"]["colouring"] = [0] * 5
    assert check_combinatorial(broken)[0] is None


def test_disjoint_union_composes_chromatic_maximum_and_matching_sum():
    union = disjoint_union_witness([c5_chromatic_example(), c5_chromatic_example()])
    assert union["predicate"] == {"name": "chromatic_exact", "k": 3}
    assert check_combinatorial(union)[0] is True
    matching = make_witness(
        P4,
        {"name": "matching_exact", "k": 2},
        {"matching": [0, 2], "upper_bound": {"kind": "vertex_count"}},
    )
    union = disjoint_union_witness([matching, matching])
    assert union["predicate"]["k"] == 4 and check_combinatorial(union)[0] is True
    broken = deepcopy(union)
    broken["predicate"]["k"] = 2
    assert check_combinatorial(broken)[0] is None
    broken = deepcopy(union)
    broken["instance"]["blocks"].append({"idx": 99, "elements": [0, 7], "positions": None})
    broken["instance_digest"] = instance_digest(broken["instance"])
    assert check_combinatorial(broken)[0] is None


def test_cnf_encoding_matches_colouring_exhaustively_for_small_graphs():
    cnf = graph_colouring_cnf(graph(3, [(0, 1), (1, 2)]), 2)
    assert cnf == graph_colouring_cnf(graph(3, [(0, 1), (1, 2)]), 2)
    for values in product((False, True), repeat=cnf["num_vars"]):
        sat = all(
            any(values[abs(lit) - 1] == (lit > 0) for lit in clause) for clause in cnf["clauses"]
        )
        semantic = all(sum(values[v * 2 : v * 2 + 2]) == 1 for v in range(3)) and all(
            not (values[u * 2 + c] and values[v * 2 + c])
            for u, v in [(0, 1), (1, 2)]
            for c in range(2)
        )
        assert sat == semantic


def test_rup_negative_proof_is_pinned_and_every_step_is_checked():
    cnf = graph_colouring_cnf(C5, 2)
    proof = {"format": "rup.v1", "steps": [[-1], []]}
    assert check_rup(cnf, proof)[0] is True
    cert = {"encoding": CNF_ENCODING, "cnf_digest": cnf["cnf_digest"], "proof": proof}
    assert checked(C5, {"name": "not_k_colourable", "k": 2}, cert)[0] is True
    assert checked(C5, {"name": "not_k_colourable", "k": 3}, cert)[0] is None
    assert checked(P4, {"name": "not_k_colourable", "k": 2}, cert)[0] is None
    bad = deepcopy(cert)
    bad["proof"]["steps"] = [[]]
    assert checked(C5, {"name": "not_k_colourable", "k": 2}, bad)[0] is None
    bad = deepcopy(cert)
    bad["proof"]["command"] = "echo trust-me"
    assert checked(C5, {"name": "not_k_colourable", "k": 2}, bad)[0] is None
    bad = deepcopy(cert)
    bad["proof"]["format"] = "drat"
    assert checked(C5, {"name": "not_k_colourable", "k": 2}, bad)[0] is None
    assert (
        check_rup({"num_vars": 1, "clauses": [[1]]}, {"format": "rup.v1", "steps": [[]]})[0]
        is False
    )


def test_rup_lower_bound_can_close_exact_chromatic_proof():
    cnf = graph_colouring_cnf(K3, 2)
    cert = {
        "colouring": [0, 1, 2],
        "lower_bound": {
            "kind": "rup",
            "encoding": CNF_ENCODING,
            "cnf_digest": cnf["cnf_digest"],
            "proof": {"format": "rup.v1", "steps": [[-1], []]},
        },
    }
    assert checked(K3, {"name": "chromatic_exact", "k": 3}, cert)[0] is True


def test_rup_never_accepts_a_satisfiable_small_cnf():
    # Independent truth-table oracle, including empty clauses and negative
    # literals: any accepted one-clause derivation followed by contradiction
    # must begin from an actually unsatisfiable CNF.
    clauses = [[], [1], [-1], [2], [-2], [1, 2], [1, -2], [-1, 2], [-1, -2]]
    for left, right in product(clauses, repeat=2):
        initial = [left, right]
        satisfiable = any(
            all(any(bits[abs(lit) - 1] == (lit > 0) for lit in c) for c in initial)
            for bits in product((False, True), repeat=2)
        )
        for addition in clauses:
            accepted, _ = check_rup(
                {"num_vars": 2, "clauses": initial},
                {"format": "rup.v1", "steps": [addition, []]},
            )
            if accepted is True:
                assert not satisfiable


def realisation(poly=(0, 1), points=((0, 0), (1, 0)), edges=((0, 1),), domain="rational"):
    return {
        "kind": "realisation",
        "structure": graph(len(points), edges),
        "configuration": {
            "dim": 2,
            "coord_domain": domain,
            "min_poly": list(poly),
            "points": [
                {
                    "idx": i,
                    "coords": [
                        {"axis": a, "num": [x] if type(x) is int else x, "den": 1}
                        for a, x in enumerate(point)
                    ],
                }
                for i, point in enumerate(points)
            ],
        },
    }


def test_exact_realisation_and_real_field_gate():
    assert checked(realisation(), {"name": "unit_distance", "faithful": True})[0] is True
    assert checked(realisation(points=((0, 0), (2, 0))), "unit_distance")[0] is False
    assert checked(realisation(points=((0, 0), (0, 0)), edges=()), "unit_distance")[0] is False
    # A nonreal quotient could make x^2 + y^2 = 1 with non-Euclidean points.
    assert (
        checked(
            realisation(poly=(1, 0, 1), points=((0, 0), ([0, 1], [0, 0])), domain="algebraic"),
            "unit_distance",
        )[0]
        is None
    )
    assert checked(realisation(domain="float_heuristic"), "unit_distance")[0] is None
    triangle = realisation(
        poly=(-3, 0, 1),
        domain="algebraic",
        points=((0, 0), (1, 0), ([1, 0], [0, 1])),
        edges=((0, 1), (0, 2), (1, 2)),
    )
    for coord in triangle["configuration"]["points"][2]["coords"]:
        coord["den"] = 2
    assert checked(triangle, "unit_distance")[0] is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda w: w.update(instance_digest="0" * 64),
        lambda w: w["instance"].update(ground_n=True),
        lambda w: w["certificate"].update(colouring=[0.0, 1, 0, 1, 2]),
        lambda w: w["predicate"].update(k=True),
        lambda w: w["predicate"].update(unchecked_assumption=True),
        lambda w: w.update(max_work=1),
        lambda w: w["instance"]["blocks"][0].update(elements=[0, 0]),
    ],
)
def test_adversarial_mutations_stay_unverified(mutate):
    witness = c5_chromatic_example()
    mutate(witness)
    assert check_combinatorial(witness)[0] is None


def test_composition_is_depth_bounded_and_unknown_rules_are_not_trusted():
    witness = c5_chromatic_example()
    for _ in range(10):
        # Build without make_witness's own JSON-depth validation to exercise
        # the untrusted consumer boundary directly.
        witness = {
            **c5_chromatic_example(),
            "certificate": {"rule": "relabel", "mapping": [0, 1, 2, 3, 4], "premise": witness},
        }
    assert check_combinatorial(witness)[0] is None
    witness = c5_chromatic_example()
    witness["certificate"] = {"rule": "trust_solver", "claimed_result": True}
    assert check_combinatorial(witness)[0] is None
