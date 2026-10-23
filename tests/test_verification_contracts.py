"""Adversarial regressions for semantic attribution and exact kernels."""

from copy import deepcopy

from calx.bundle import verify_bundle
from calx.kernel import verify_witness
from calx.ledger import certificate_row_hash
from calx.proposition import witness_subject_ref


def entry(cid=1):
    body = {"schema": "factorization", "n": 12, "factors": [[2, 2], [3, 1]]}
    return {
        "claim": {
            "id": cid,
            "statement": "proof object is valid",
            "subject_ref": witness_subject_ref(body),
        },
        "certificate": {"seq": 1, "status": "valid"},
        "witness": {"kind": "term", "body": body},
    }


def test_unrelated_true_witness_does_not_verify_graph_claim():
    item = entry()
    item["claim"]["subject_ref"] = {"structure": "K3", "property": "triangle_free"}
    item["claim"]["statement"] = "K3 is triangle-free"
    assert verify_bundle({"trunk_bundle_version": 1, "claims": [item]})[0].ok is None


def test_committed_premise_survives_removed_derivation_metadata():
    leaf, top = entry(), entry(2)
    for item in (leaf, top):
        cert = item["certificate"]
        cert["premise_hashes"] = [] if item is leaf else [leaf["certificate"]["row_hash"]]
        cert["row_hash"] = certificate_row_hash(
            item["claim"]["id"], 1, "valid", None, None, None, None, cert["premise_hashes"]
        )
    leaf["revocation"] = {"reason": "withdrawn"}
    top["derivation"] = {"premise_ids": [1], "rule": "test"}
    before = {"trunk_bundle_version": 2, "claims": [leaf, top]}
    after = deepcopy(before)
    del after["claims"][1]["derivation"]
    assert verify_bundle(before)[1].ok is None
    assert verify_bundle(after)[1].ok is None


def test_repeated_primes_cannot_forge_divisor_sum():
    assert (
        verify_witness(
            {
                "schema": "factorization",
                "n": 36,
                "factors": [[2, 1], [2, 1], [3, 2]],
                "asserts": {"sigma": 117},
            }
        )[0]
        is False
    )


def test_factorization_assertions_cannot_silently_coerce_numbers_or_booleans():
    for assertions in ({"sigma": 91.9}, {"perfect": "false"}):
        assert (
            verify_witness(
                {
                    "schema": "factorization",
                    "n": 36,
                    "factors": [[2, 2], [3, 2]],
                    "asserts": assertions,
                }
            )[0]
            is None
        )


def test_csp_rejects_undeclared_variables_and_inexact_arithmetic():
    assert (
        verify_witness(
            {
                "schema": "csp_carry",
                "instance": {"vars": {}, "constraints": [{"type": "equal", "a": "x", "b": "y"}]},
                "assignment": {"x": 7, "y": 7},
            }
        )[0]
        is None
    )
    assert (
        verify_witness(
            {
                "schema": "csp_carry",
                "instance": {
                    "vars": {"x": [1e16], "y": [1]},
                    "constraints": [
                        {"type": "linear", "terms": [[1, "x"], [1, "y"]], "rel": "=", "rhs": 1e16}
                    ],
                },
                "assignment": {"x": 1e16, "y": 1},
            }
        )[0]
        is None
    )


def test_rejected_path_does_not_refute_solved_puzzle():
    assert (
        verify_witness(
            {"schema": "puzzle_parity", "rows": 2, "cols": 2, "state": [1, 2, 3, 0], "moves": "U"}
        )[0]
        is None
    )


def test_factorization_rejects_oversize_powers_before_expensive_work():
    assert (
        verify_witness({"schema": "factorization", "n": 12, "factors": [[2, 10**100]]})[0] is False
    )
    prime = 2**127 - 1
    assert (
        verify_witness({"schema": "factorization", "n": prime, "factors": [[prime, 1]]})[0] is None
    )


def test_predicate_boolean_cannot_impersonate_integer():
    from calx.comb_cli import portable_bundle
    from calx.combinatorial import make_witness

    witness = make_witness(
        {"kind": "graph", "ground_n": 1, "blocks": []},
        {"name": "colouring", "k": 1},
        {"colouring": [0]},
    )
    bundle = portable_bundle(witness)
    bundle["claims"][0]["claim"]["subject_ref"] = deepcopy(
        bundle["claims"][0]["claim"]["subject_ref"]
    )
    bundle["claims"][0]["claim"]["subject_ref"]["predicate"]["k"] = True
    assert verify_bundle(bundle)[0].ok is None


def test_csp_discrete_equality_distinguishes_booleans_and_integers():
    for kind, expected in (("equal", False), ("not_equal", True)):
        body = {
            "schema": "csp_carry",
            "instance": {
                "vars": {"x": [True], "y": [1]},
                "constraints": [{"type": kind, "a": "x", "b": "y"}],
            },
            "assignment": {"x": True, "y": 1},
        }
        assert verify_witness(body)[0] is expected


def test_malformed_typed_dfa_bundle_is_unknown_instead_of_crashing():
    item = entry()
    item["witness"]["body"] = {"schema": "dfa_count.v1"}
    assert verify_bundle({"trunk_bundle_version": 1, "claims": [item]})[0].ok is None
