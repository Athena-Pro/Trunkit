"""End-to-end offline consumer surfaces and bounded producer behavior."""

import json
import subprocess
import sys

from calx.comb_cli import bounded_colouring, portable_bundle
from calx.combinatorial import c5_chromatic_example


def test_cli_demo_verifies_entirely_offline(tmp_path):
    path = tmp_path / "c5.json"
    result = subprocess.run(
        [sys.executable, "-m", "calx.cli", "comb", "demo", "--output", str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    result = subprocess.run(
        [sys.executable, "-m", "calx.cli", "verify", "--bundle", str(path), "--offline"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "VALID" in result.stdout


def test_bounded_search_never_turns_failure_into_nonexistence():
    graph = c5_chromatic_example()["instance"]
    found = bounded_colouring(graph, 3)
    assert found["verdict"] == "valid"
    impossible = bounded_colouring(graph, 2)
    assert impossible["verdict"] == "unverified" and impossible["search_exhausted"]
    budget = bounded_colouring(graph, 3, 1)
    assert budget["verdict"] == "unverified" and not budget["search_exhausted"]


def test_mcp_graph_workflow_and_counting():
    from trunkit_mcp import server

    witness = c5_chromatic_example()
    imported = server.comb_import(json.dumps(witness["instance"]))
    assert imported["instance_digest"] == witness["instance_digest"]
    bundle = server.comb_export(
        json.dumps(witness["instance"]),
        json.dumps(witness["predicate"]),
        json.dumps(witness["certificate"]),
    )
    assert server.bundle_verify(json.dumps(bundle), offline=True)["all_valid"]
    dfa = {
        "alphabet": ["a", "b"],
        "states": [0],
        "initial": 0,
        "accepting": [0],
        "transitions": [[0, "a", 0], [0, "b", 0]],
    }
    result = server.automata_count(json.dumps(dfa), 40, "[-2,1]")
    assert result["verdict"] == "valid"
    assert result["witness"]["counts"][-1] == 2**40


def test_typed_c5_bundle_rejects_an_unrelated_witness():
    from calx.bundle import verify_bundle

    bundle = portable_bundle(c5_chromatic_example())
    bundle["claims"][0]["witness"]["body"] = {
        "schema": "factorization",
        "n": 12,
        "factors": [[2, 2], [3, 1]],
    }
    assert verify_bundle(bundle)[0].ok is None


def test_external_drup_import_cli_and_mcp(tmp_path):
    from trunkit_mcp import server

    graph = c5_chromatic_example()["instance"]
    graph_path, proof_path, output = (
        tmp_path / name for name in ("graph.json", "proof.drup", "out.json")
    )
    graph_path.write_text(json.dumps(graph), encoding="utf-8")
    proof_path.write_text("-1 0\n0\n", encoding="ascii")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "calx.cli",
            "comb",
            "exclude",
            str(graph_path),
            "--colours",
            "2",
            "--proof",
            str(proof_path),
            "--bundle",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    result = server.bundle_verify(output.read_text(encoding="utf-8"), offline=True)
    assert result["all_valid"]
    imported = server.comb_exclude(json.dumps(graph), 2, "-1 0\n0\n")
    assert server.bundle_verify(json.dumps(imported), offline=True)["all_valid"]
