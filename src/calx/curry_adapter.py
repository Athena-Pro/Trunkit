"""Curry adapter for calx.

Each calx SQL procedure / function is declared as a Curry function whose body
returns a "call plan" dict:

    {
        "sql": "function" | "procedure",
        "proc": "<sql_identifier>",
        "args": [...],
        "returns": "scalar" | "rows" | "none",
        "schema_version": <bound calx_schema_version constant>,
    }

This module reads the plan via ``CurrySession.call_function`` and dispatches
the actual SQL execution against PostgreSQL through psycopg. The Curry layer
owns versioning, dependency binding, expected_args, and per-argument
descriptions; psycopg handles parameter binding and result shaping.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _curry_project_dir() -> Path:
    """Return the calx project root (the directory containing ``.curry``)."""
    return Path(__file__).resolve().parents[2]


def _load_curry_core():
    """Load the standalone ``curry_core`` module from the path in config.json.

    Curry is not on the Python import path; the config file declares where its
    sources live so we can import without installing.
    """
    project_dir = _curry_project_dir()
    config_path = project_dir / ".curry" / "config.json"
    with open(config_path, encoding="utf-8") as fh:
        config = json.load(fh)

    curry_path = config.get("curry_module_path")
    if not curry_path:
        raise RuntimeError(
            f"{config_path} is missing 'curry_module_path' — cannot locate curry_core.py"
        )
    if "curry_core" in sys.modules:
        return sys.modules["curry_core"]

    src = Path(curry_path) / "curry_core.py"
    if not src.is_file():
        raise FileNotFoundError(f"curry_core.py not found at {src}")
    spec = importlib.util.spec_from_file_location("curry_core", src)
    module = importlib.util.module_from_spec(spec)
    sys.modules["curry_core"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@contextmanager
def curry_session() -> Iterator[Any]:
    """Open a CurrySession scoped to the calx project root."""
    curry_core = _load_curry_core()
    project_dir = str(_curry_project_dir())
    session = curry_core.CurrySession.from_project(project_dir)
    try:
        yield session
    finally:
        session.close()


def _safe_ident(name: str) -> str:
    if not _IDENT_RE.match(name):
        raise ValueError(f"unsafe SQL identifier from Curry plan: {name!r}")
    return name


def execute_plan(conn, plan: dict[str, Any]) -> Any:
    """Execute a call plan returned by a Curry function body against ``conn``.

    Procedure / function names are validated against an identifier regex
    before being interpolated, because psycopg cannot parameterize identifiers.
    Argument values are always passed as parameters.
    """
    sql_kind = plan["sql"]
    proc = _safe_ident(plan["proc"])
    args = list(plan.get("args", []))
    returns = plan.get("returns", "scalar")
    placeholders = ", ".join(["%s"] * len(args))

    with conn.cursor() as cur:
        if sql_kind == "function":
            cur.execute(f"SELECT * FROM {proc}({placeholders})", args)
            if returns == "scalar":
                row = cur.fetchone()
                return None if row is None else row[0]
            if returns == "rows":
                cols = [d.name for d in cur.description]
                return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
            raise ValueError(f"unsupported 'returns' for function plan: {returns!r}")
        if sql_kind == "procedure":
            cur.execute(f"CALL {proc}({placeholders})", args)
            return None
        raise ValueError(f"unknown 'sql' kind in plan: {sql_kind!r}")


def run(
    name: str,
    version: int,
    args: dict[str, Any],
    *,
    dsn: str | None = None,
) -> Any:
    """End-to-end: open Curry session, resolve plan, execute against Postgres."""
    from . import db  # lazy: psycopg is only required when actually executing
    with curry_session() as session:
        plan = session.call_function(name, version, args)
    with db.connect(dsn) as conn:
        return execute_plan(conn, plan)


def plan_only(name: str, version: int, args: dict[str, Any]) -> dict[str, Any]:
    """Resolve the call plan without executing — useful for tests / inspection."""
    with curry_session() as session:
        return session.call_function(name, version, args)


__all__ = [
    "curry_session",
    "execute_plan",
    "plan_only",
    "run",
]
