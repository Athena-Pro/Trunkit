"""`trunkit close` tool resolution (DB-free).

`close` runs the kan-in-kan self-analysis, a project-specific tool that lives in
the `local/` overlay (`local/tools/kan_in_kan.py`), not the base package. The CLI
must resolve it from either the shipped `tools/` dir or the local overlay, and
degrade gracefully when it is absent.
"""

from __future__ import annotations

import pathlib

import pytest

from calx import cli

_KAN_IN_KAN = pathlib.Path(__file__).parent.parent / "local" / "tools" / "kan_in_kan.py"


@pytest.mark.skipif(not _KAN_IN_KAN.is_file(), reason="local/tools/kan_in_kan.py not present")
def test_resolve_local_extension_tool():
    # present only in a workspace checkout that carries the local/ overlay
    p = cli._resolve_tool("kan_in_kan.py")
    assert p is not None
    assert p.is_file()
    assert p.name == "kan_in_kan.py"


def test_resolve_missing_tool_returns_none():
    assert cli._resolve_tool("definitely_not_a_real_tool_xyz.py") is None
