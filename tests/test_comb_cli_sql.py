"""The explicit-write import preserves instance identity across CLI and SQL."""

import json
import subprocess
import sys
import uuid

import psycopg

from calx.combinatorial import instance_digest


def test_cli_database_import_preserves_snapshot_and_refuses_replacement(
    _initialized_calx_db,
    tmp_path,
):
    subject = "cli-import-" + uuid.uuid4().hex
    graph = {
        "kind": "graph",
        "ground_n": 3,
        "blocks": [{"idx": 7, "elements": [0, 1]}, {"idx": 10, "elements": [1, 2]}],
    }
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(graph), encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "calx.cli",
        "--dsn",
        _initialized_calx_db,
        "comb",
        "import",
        str(path),
        "--subject",
        subject,
        "--write",
    ]
    try:
        for _ in range(2):
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stdout + result.stderr
        with psycopg.connect(_initialized_calx_db) as conn:
            snapshot = conn.execute("SELECT comb.serialize_structure(%s)", (subject,)).fetchone()[0]
            assert instance_digest(snapshot) == instance_digest(graph)
        graph["blocks"].pop()
        path.write_text(json.dumps(graph), encoding="utf-8")
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        assert result.returncode == 2
        assert "different instance" in result.stdout
    finally:
        with psycopg.connect(_initialized_calx_db) as conn:
            conn.execute("DELETE FROM comb.structure WHERE subject_id=%s", (subject,))
