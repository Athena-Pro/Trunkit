"""CI cannot silently substitute a skipped integration suite for a working DB."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import conftest


def test_required_database_unavailability_is_a_failure(monkeypatch):
    monkeypatch.setenv("TRUNKIT_REQUIRE_TEST_DBS", "1")
    with pytest.raises(pytest.fail.Exception, match="scratch database unavailable"):
        conftest._database_unavailable("scratch database unavailable")


def test_optional_database_unavailability_still_skips(monkeypatch):
    monkeypatch.delenv("TRUNKIT_REQUIRE_TEST_DBS", raising=False)
    with pytest.raises(pytest.skip.Exception, match="scratch database unavailable"):
        conftest._database_unavailable("scratch database unavailable")


def test_strict_session_rejects_missing_dedicated_dsns(tmp_path):
    env = os.environ.copy()
    for key in ("CALX_TEST_DSN", "ARITHMETIC_DB_TEST_DSN", "NERODE_TEST_DSN"):
        env.pop(key, None)
    env["TRUNKIT_REQUIRE_TEST_DBS"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/test_run.py",
         "--basetemp", str(tmp_path / "nested")],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True,
        timeout=30,
    )
    assert result.returncode == 4
    assert "CALX_TEST_DSN is required" in result.stderr
