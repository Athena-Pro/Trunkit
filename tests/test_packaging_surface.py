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


def test_base_install_stays_pure_python_with_binary_as_a_real_extra():
    """Pins the Termux/ARM decision, and the extra that makes it survivable.

    psycopg[binary] ships no wheel for Termux/ARM and other non-x86 targets, so
    it must NOT be the base dependency -- that trades a missing-libpq failure on
    minimal Linux for a hard install failure everywhere off x86. The libpq-free
    Linux path is `trunkit[binary]`, which the linux-wheel-smoke CI leg installs
    and exercises, so this extra is load-bearing rather than decorative.

    A platform marker is not an alternative: packaging markers cannot reliably
    separate glibc, musl, and Termux/Android.
    """
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["dependencies"] == ["psycopg>=3.2,<4"]
    extras = pyproject["project"]["optional-dependencies"]
    assert "psycopg[binary]>=3.2,<4" in extras["binary"]


def test_mcp_extra_excludes_the_unsupported_major_version():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = pyproject["project"]["optional-dependencies"]
    assert "mcp>=1.0.0,<2" in extras["mcp"]
    assert "pydantic-settings>=2.5.2,<2.13" in extras["mcp"]
    assert "mcp>=1.0.0,<2" in extras["dev"]
    assert "pydantic-settings>=2.5.2,<2.13" in extras["dev"]


def test_ci_and_make_use_the_numeric_aware_schema_loader():
    workflow = (ROOT / ".github" / "workflows" / "python-package-conda.yml").read_text(
        encoding="utf-8"
    )
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert 'trunkit --dsn "$CALX_DSN" init' in workflow
    assert 'trunkit --dsn "$(TRUNK_DSN)" init' in makefile
    assert 'nerode --dsn "$NERODE_DSN" close --apply' in workflow
    assert 'nerode --dsn "$(NERODE_DSN)" close --apply' in makefile
    # No shell-sorted apply loop on either side. calx was the one that actually
    # broke (100_ before 10_ under `sort -n`, 112 errors psql walked straight
    # past); nerode had the same construction and was correct only by luck of
    # never reaching three digits.
    for haystack in (workflow, makefile):
        assert "sort -n" not in haystack
        assert "ls src/nerode/sql" not in haystack
        assert "ls src/calx/sql" not in haystack


def test_domain_schemas_are_reflected_into_kan():
    """The sync list is the only thing that decides what kan can see.

    110 states the reuse as an accomplished fact -- "because sync_category
    reflects ANY schema, `comb` becomes a kan category the moment it exists" --
    but sync_category only ever runs on the names in this list, and comb was
    never added. prov (114) arrived the same way. A self-analysis pass found
    both invisible, so the list is pinned here rather than left to be
    rediscovered when the next domain schema goes missing.

    cert is intentionally excluded: reflecting the ledger's own schema is a
    design question, not an omission.
    """
    assert set(calx_db.KAN_SYNC_CATEGORIES) == {"calx", "curry", "kan", "comb", "prov"}

    # Every schema the package's own SQL creates, so a new one cannot be added
    # without this test forcing a decision about whether kan should see it.
    created = set()
    for name in calx_db.UNIFIED_FILES:
        for line in (ROOT / "src" / "calx" / "sql" / name).read_text(
                encoding="utf-8").splitlines():
            stripped = line.strip().upper()
            if stripped.startswith("CREATE SCHEMA IF NOT EXISTS"):
                created.add(line.strip().split()[-1].rstrip(";").lower())
    unreflected = created - set(calx_db.KAN_SYNC_CATEGORIES)
    assert unreflected == {"cert"}, (
        f"schemas neither reflected into kan nor consciously excluded: "
        f"{unreflected - {'cert'}}")


def test_nerode_schema_loader_covers_every_sql_file_on_disk():
    """The calx side pins this (test_unified_schema_tracks_all_numbered_sql_files);
    nerode did not, and drifted -- 98_topological_signature.sql and
    99_precacher_roundtrip_cert.sql sat on disk outside SCHEMA_FILES, so the old
    `ls | sort` loop applied them and apply_schema did not. Every test database
    is built by apply_schema, so the cert that certifies the precacher
    close->open roundtrip never existed in the databases asserting that
    invariant. Switching the Makefile to the loader is only safe while these
    two lists agree."""
    from nerode.db import SCHEMA_FILES

    on_disk = {p.name for p in (ROOT / "src" / "nerode" / "sql").glob("*.sql")}
    assert on_disk == set(SCHEMA_FILES), (
        f"only on disk: {sorted(on_disk - set(SCHEMA_FILES))}; "
        f"only in SCHEMA_FILES: {sorted(set(SCHEMA_FILES) - on_disk)}"
    )
