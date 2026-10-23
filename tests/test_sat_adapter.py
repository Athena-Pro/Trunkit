"""External DRUP import must establish exactly the regenerated graph formula."""

from __future__ import annotations

import pytest

from calx.combinatorial import check_combinatorial, graph_colouring_cnf
from calx.sat_adapter import make_colouring_exclusion, parse_drup

K2 = {"kind": "graph", "ground_n": 2, "blocks": [[0, 1]]}


def test_k2_one_colour_external_empty_clause_rechecks_end_to_end():
    witness = make_colouring_exclusion(K2, 1, "c External solver artifact\n0\n")
    assert witness["predicate"] == {"name": "not_k_colourable", "k": 1}
    assert witness["certificate"]["proof"] == {"format": "rup.v1", "steps": [[]]}
    assert witness["certificate"]["cnf_digest"] == graph_colouring_cnf(K2, 1)["cnf_digest"]
    assert check_combinatorial(witness)[0] is True


def test_imported_rup_addition_precedes_empty_clause_for_an_odd_cycle():
    cycle = {"kind": "graph", "ground_n": 5, "blocks": [[0, 1], [1, 2], [2, 3], [3, 4], [0, 4]]}
    text = "\n c Proof of exclusion\n-1 0\r\n0\n c Finished\n"
    assert parse_drup(text) == {"format": "rup.v1", "steps": [[-1], []]}
    assert check_combinatorial(make_colouring_exclusion(cycle, 2, text))[0] is True


@pytest.mark.parametrize(
    "text",
    [
        "",
        "c comment only\n",
        "1 0\n",
        "1\n",
        "1 0 2\n",
        "0 0\n",
        "0\n1 0\n",
        "d 1 0\n0\n",
        "p cnf 2 1\n0\n",
        "a 1 0\n0\n",
        "1 0 4 0\n0\n",
        "+1 0\n0\n",
        "01 0\n0\n",
        "-0 0\n0\n",
        "2147483648 0\n0\n",
        "999999999999999999999999999 0\n0\n",
        "c non-ASCII é\n0\n",
        "1\x00 0\n0\n",
    ],
)
def test_incomplete_unsupported_or_malformed_artifacts_are_rejected(text):
    with pytest.raises(ValueError):
        parse_drup(text)


def test_syntactic_import_cannot_establish_a_satisfiable_formula():
    assert parse_drup("0") == {"format": "rup.v1", "steps": [[]]}
    with pytest.raises(ValueError, match="does not establish"):
        make_colouring_exclusion(K2, 2, "0")
    with pytest.raises(ValueError, match="does not establish"):
        make_colouring_exclusion(K2, 1, "99 0\n0\n")


def test_budget_parameters_only_lower_fixed_resource_ceilings():
    with pytest.raises(ValueError, match="byte budget"):
        parse_drup("c long comment\n0", max_bytes=2)
    with pytest.raises(ValueError, match="step budget"):
        parse_drup("1 0\n0", max_steps=1)
    with pytest.raises(ValueError, match="literal budget"):
        parse_drup("1 2 0\n0", max_literals=1)
    for kwargs in ({"max_bytes": 8_000_001}, {"max_steps": True}, {"max_literals": -1}):
        with pytest.raises(ValueError):
            parse_drup("0", **kwargs)
    assert parse_drup("0", max_bytes=1, max_steps=1, max_literals=0)["steps"] == [[]]


def test_non_text_is_rejected_without_running_an_external_command():
    with pytest.raises(ValueError, match="must be text"):
        parse_drup(b"0")
    with pytest.raises(ValueError, match="unsupported proof format"):
        make_colouring_exclusion(K2, 1, "powershell Invoke-Expression bad\n0")
