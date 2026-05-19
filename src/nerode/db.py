"""
nerode.db — database connection and schema bootstrap utilities.
"""

from __future__ import annotations

import importlib.resources
import os
from collections.abc import Generator
from contextlib import contextmanager

import psycopg

DEFAULT_DSN  = "postgresql://nerode:nerode@localhost:5435/nerode"
TRUNKIT_DSN  = "postgresql://trunk:trunk@localhost:5434/trunk"

# SQL files applied in strict order; each is idempotent.
SCHEMA_FILES = (
    "00_bootstrap.sql",
    "01_schema.sql",
    "02_run.sql",
    "03_minimize.sql",
    "04_product.sql",
    "05_from_regex.sql",
    "10_cert.sql",
    "20_calx_bridge.sql",
    "11_chomsky.sql",
    "30_protocol.sql",
    "40_eigenform.sql",
    "50_corpus.sql",   # Phase 1b — named DFAs with composite state counts
    "60_product_corpus.sql",  # Phase 1c — pairwise intersection products
    "70_morphism.sql",         # Phase 1d — DFA homomorphisms
    "80_categorical.sql",      # Phase 2  — categorical structure
    "90_sequence.sql",         # Phase 3  — DFA walk / sequence generation
    "91_sequence_cache.sql",   # Phase 3b — persistent cache + NOTIFY callbacks
    "92_session_automata.sql", # Phase 3c — session event DFAs + NOTIFY shortcuts
    "93_handoff.sql",          # Phase 3d — close_session() + handoff envelope
    "94_open_session.sql",     # Phase 3e — open_session() + session_cache_tags
    "95_cybernetic_automata.sql", # Phase 3f — cybernetic control DFAs
    "96_dead_time_factory.sql",  # Phase 3g — ensure_dead_time(k) factory
    "97_composite_dfa.sql",     # Phase 3h — paired-alphabet projection + composite DFAs
)


def resolve_dsn() -> str:
    """Return connection string from NERODE_DSN env var or the default."""
    return os.environ.get("NERODE_DSN", DEFAULT_DSN)


@contextmanager
def connect(dsn: str | None = None) -> Generator[psycopg.Connection, None, None]:
    """Context manager: open, yield, and close a psycopg connection."""
    conn_str = dsn or resolve_dsn()
    with psycopg.connect(conn_str) as conn:
        yield conn


_SQL_DIR = importlib.resources.files("nerode") / "sql"


def apply_schema(conn: psycopg.Connection, *, verbose: bool = False) -> None:
    """Apply all SCHEMA_FILES to conn in order. Idempotent."""
    for filename in SCHEMA_FILES:
        sql = (_SQL_DIR / filename).read_text(encoding="utf-8")
        if verbose:
            print(f"  applying {filename} …", flush=True)
        conn.execute(sql)
    conn.commit()
    if verbose:
        print("  schema up to date.", flush=True)
