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


def _numbered_sql_files() -> tuple[str, ...]:
    return tuple(
        entry.name
        for entry in sorted(SQL_DIR.iterdir(), key=lambda path: path.name)
        if entry.is_file()
        and entry.suffix == ".sql"
        and len(entry.name) >= 3
        and entry.name[:2].isdigit()
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


def resolve_dsn(dsn: str | None = None) -> str:
    if dsn:
        return dsn
    env = os.environ.get("CALX_DSN")
    if env:
        return env
    return DEFAULT_DSN


@contextmanager
def connect(dsn: str | None = None, *, autocommit: bool = False) -> Iterator[Connection]:
    conn = psycopg.connect(resolve_dsn(dsn), autocommit=autocommit)
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
