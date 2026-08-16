from __future__ import annotations

import tomllib
from pathlib import Path

from calx import db as calx_db

ROOT = Path(__file__).resolve().parents[1]


def test_unified_schema_tracks_all_numbered_sql_files():
    # Apply order is (numeric prefix, remainder) — '99_' before '100_' — so
    # mirror calx.db.schema_order, not lexicographic filename order.
    sql_dir = ROOT / "src" / "calx" / "sql"
    expected = tuple(
        sorted(
            (
                path.name
                for path in sql_dir.iterdir()
                if path.is_file() and path.suffix == ".sql" and path.name[:2].isdigit()
            ),
            key=calx_db.schema_order,
        )
    )
    assert expected == calx_db.UNIFIED_FILES


def _shared_data() -> dict[str, str]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["shared-data"]


def test_wheel_shared_data_maps_files_never_directories():
    """The 0.4.0 leak, pinned so it cannot come back.

    shared-data is a force-include mapping: it honours neither .gitignore nor
    `exclude`, so a mapped DIRECTORY ships whatever sits on the builder's disk.
    Mapping "proofs" wholesale put 8,885 files / 97 MB of untracked Lean .lake
    build artifacts into the 0.4.0 wheel (32.4 MB, against a 1.5 MB core) --
    the "3 GB compiler in the box" the README promises is never shipped.
    Every entry must therefore name a single existing FILE.
    """
    for src, dest in _shared_data().items():
        path = ROOT / src
        assert path.is_file(), f"shared-data key {src!r} is not an existing file"
        assert dest.startswith("share/trunkit/"), f"{src!r} escapes share/trunkit/"
        # A directory mapping is the leak; a file mapped onto a bare directory
        # destination would silently reintroduce it.
        assert dest.endswith(Path(src).name), f"{src!r} must map onto its own basename"


def test_wheel_shared_data_still_carries_the_tools_that_matter():
    """Explicit-file mapping trades leak-safety for a list that can go stale:
    a new tool is only shipped once someone adds a line. These are the ones a
    consumer is documented to run, so a silent drop is a broken install."""
    shared_data = _shared_data()
    for required in (
        "tools/cert_formal.py",      # the attestation pass
        "tools/verify_bundle.py",    # consumer-side bundle verification
        "tools/oeis_loader.py",
        "tools/oeis_match.py",
        "tools/lean_check.sh",       # T1 Lean bridge checker hook
        "proofs/combined_signature.py",
    ):
        assert required in shared_data, f"{required} dropped out of the wheel"


def test_readme_install_surface_matches_single_distribution():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "pip install nerode" not in readme
    assert "pip install trunkit   # installs both the trunkit and nerode CLIs" in readme
