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

# Without a timeout, an unreachable host hangs the CLI indefinitely.
CONNECT_TIMEOUT = int(os.environ.get("TRUNKIT_CONNECT_TIMEOUT", "10"))

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
    # Phase 4 — quantitative automata (EHA/WFFA) + exact extremal analysis.
    # arXiv:2606.11223 "Scenario Constraints with Memory". Order is dependency-
    # critical: A0 (interval type) must precede everything that declares it.
    "A0_interval.sql",          # Phase 4a — interval algebra I(D)
    "A1_eha.sql",               # Phase 4b — event history automata + compile_eha
    "A2_wffa.sql",              # Phase 4c — weighted finance automata + payoff_eval
    "A3_wffa_product.sql",      # Phase 4d — scenario-restricted product (Thm 2)
    "A4_extremal.sql",          # Phase 4e — exact best/worst-case DP + witness (Thm 3)
    "A5_extremal_cert.sql",     # Phase 4f — carried certificates + consistency check
    "A6_monotonicity.sql",      # Phase 4g — cross-scenario monotonicity probe
    # Phase 5 — Porter policy gate (LedgerAgent, arXiv:2606.20529). Ledger must
    # precede the policy registry, gate, and cert bridge.
    "B0_ledger.sql",            # Phase 5a — schema-anchored ledger (Absorb/Render)
    "B1_policy.sql",            # Phase 5b — policy predicate registry (Π)
    "B2_gate.sql",              # Phase 5c — policy_gate (GateFilter: ALLOW/REVISE/BLOCK)
    "B3_gate_cert.sql",         # Phase 5d — proof-carrying decisions + replay + drift
    "B4_carry.sql",             # Phase 5e — carry the ledger across the Porter handoff
    # Phase 6 — unified witness-kind re-verification.
    "C0_verify.sql",            # Phase 6a — nerode.verify() dispatches by witness kind
)


def resolve_dsn() -> str:
    """Return connection string from NERODE_DSN env var or the default."""
    return os.environ.get("NERODE_DSN", DEFAULT_DSN)


@contextmanager
def connect(dsn: str | None = None) -> Generator[psycopg.Connection, None, None]:
    """Context manager: open, yield, and close a psycopg connection."""
    conn_str = dsn or resolve_dsn()
    with psycopg.connect(conn_str, connect_timeout=CONNECT_TIMEOUT) as conn:
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
