"""Database connection helpers.

DSN resolution order:
  1. explicit ``dsn`` argument
  2. ``CALX_DSN`` env var
  3. the running docker-compose default
     (postgresql://trunk:trunk@localhost:5434/trunk)

The database hosts four sibling schemas — ``calx`` (integer/arithmetic data
and routines), ``curry`` (versioned-fact store), ``kan`` (schema-as-category
metadata), ``cert`` (proof-carrying attestation) — all reflected into ``kan``
by ``kan.sync_category``.
"""

from __future__ import annotations

import importlib.resources
import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import Connection

SQL_DIR = importlib.resources.files("calx") / "sql"


def schema_order(name: str) -> tuple[int, str]:
    """Numeric-aware apply order for ``NN_``/``NNN_`` schema filenames.

    Plain lexical sort breaks at the 2->3 digit boundary ('100_...' would
    apply before '10_curry.sql'). Ordering by (numeric prefix, remainder)
    keeps '41_' < '41a_' < '42_' and '99_' < '100_'. The Makefile / CI apply
    loops encode the same order as ``LC_ALL=C sort -n``.
    """
    digits = ""
    for ch in name:
        if not ch.isdigit():
            break
        digits += ch
    return (int(digits), name[len(digits):])


def is_numbered_sql(name: str) -> bool:
    """A schema file: '.sql' with a 2+ digit numeric prefix (NN_ / NNN_)."""
    return name.endswith(".sql") and len(name) >= 3 and name[:2].isdigit()


def _numbered_sql_files() -> tuple[str, ...]:
    return tuple(
        entry.name
        for entry in sorted(
            (e for e in SQL_DIR.iterdir() if e.is_file() and is_numbered_sql(e.name)),
            key=lambda e: schema_order(e.name),
        )
    )

# calx-only DDL (unqualified names; resolve to the `calx` schema via search_path)
SCHEMA_FILES = (
    "00_rehome_to_calx.sql",
    "01_schema.sql",
    "02_views.sql",
    "03_generate.sql",
    "04_crt.sql",
    "05_dynamics.sql",
    "06_oeis_match.sql",
    "07_compositions.sql",
)

# Full unified bootstrap, in order: schemas + re-home, calx DDL, curry, kan, functors.
UNIFIED_FILES = _numbered_sql_files()

# Applied per-session so a fresh-DB bootstrap creates calx objects in `calx`
# (ALTER ROLE in 00_rehome only affects *future* sessions).
SEARCH_PATH = "calx, curry, kan, public"

DEFAULT_DSN = "postgresql://trunk:trunk@localhost:5434/trunk"

# Without a timeout, an unreachable host hangs the CLI indefinitely.
CONNECT_TIMEOUT = int(os.environ.get("TRUNKIT_CONNECT_TIMEOUT", "10"))


def resolve_dsn(dsn: str | None = None) -> str:
    if dsn:
        return dsn
    env = os.environ.get("CALX_DSN")
    if env:
        return env
    return DEFAULT_DSN


@contextmanager
def connect(dsn: str | None = None, *, autocommit: bool = False) -> Iterator[Connection]:
    conn = psycopg.connect(
        resolve_dsn(dsn), autocommit=autocommit, connect_timeout=CONNECT_TIMEOUT
    )
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


def apply_schema(conn: Connection, files: tuple[str, ...] = SCHEMA_FILES) -> None:
    """Execute the calx DDL files (schema, views, procedures, CRT, dynamics).

    Idempotent — every statement uses ``CREATE OR REPLACE`` or ``IF NOT EXISTS``.
    Sets the session search_path first so unqualified objects land in ``calx``.
    """
    with conn.cursor() as cur:
        cur.execute(f"SET search_path = {SEARCH_PATH}")
        for fname in files:
            path = SQL_DIR / fname
            cur.execute(path.read_text(encoding="utf-8"))


def apply_extensions(conn: Connection, ext_dir: str | os.PathLike) -> list[str]:
    """Apply numbered SQL files from *ext_dir* (e.g. ``local/sql/``) in numeric order.

    Only files whose names begin with a 2+ digit numeric prefix (``NN_*.sql``
    or ``NNN_*.sql``) are loaded — the same convention and the same
    ``schema_order`` ordering as the core schema files.  Files are applied
    inside a single transaction so a failure rolls back the whole batch.

    Returns the list of filenames actually executed (useful for diagnostics).
    """
    import pathlib

    ext_path = pathlib.Path(ext_dir)
    sql_files = sorted(
        (p for p in ext_path.iterdir() if p.is_file() and is_numbered_sql(p.name)),
        key=lambda p: schema_order(p.name),
    )
    if not sql_files:
        return []
    applied: list[str] = []
    with conn.cursor() as cur:
        cur.execute(f"SET search_path = {SEARCH_PATH}")
        for p in sql_files:
            cur.execute(p.read_text(encoding="utf-8"))
            applied.append(p.name)
    return applied


def apply_unified(conn: Connection, *, sync_kan: bool = True) -> None:
    """Bootstrap the full unified model: schemas, calx, curry, kan.

    Safe on both a fresh database and the already-migrated live one
    (00_rehome's DO-loops are no-ops once ``public`` is empty).
    """
    with conn.cursor() as cur:
        for fname in UNIFIED_FILES:
            path = SQL_DIR / fname
            cur.execute(path.read_text(encoding="utf-8"))
            if fname == "00_rehome_to_calx.sql":
                cur.execute(f"SET search_path = {SEARCH_PATH}")
        if sync_kan:
            for cat in ("calx", "curry", "kan"):
                cur.execute("SELECT kan.sync_category(%s, %s)", (cat, cat))
            cur.execute("SELECT * FROM kan.populate_curry_calx_functor()")
