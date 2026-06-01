from __future__ import annotations

import tomllib
from pathlib import Path

from calx import db as calx_db

ROOT = Path(__file__).resolve().parents[1]


def test_unified_schema_tracks_all_numbered_sql_files():
    sql_dir = ROOT / "src" / "calx" / "sql"
    expected = tuple(
        path.name
        for path in sorted(sql_dir.iterdir(), key=lambda path: path.name)
        if path.is_file() and path.suffix == ".sql" and path.name[:2].isdigit()
    )
    assert expected == calx_db.UNIFIED_FILES


def test_wheel_shared_data_includes_tools_and_proofs():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    shared_data = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["shared-data"]
    assert shared_data["tools"] == "share/trunkit/tools"
    assert shared_data["proofs"] == "share/trunkit/proofs"


def test_readme_install_surface_matches_single_distribution():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "pip install nerode" not in readme
    assert "pip install trunkit   # installs both the trunkit and nerode CLIs" in readme
