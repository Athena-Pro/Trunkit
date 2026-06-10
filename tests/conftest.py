"""
Pytest configuration and shared fixtures.

Nerode fixtures (nerode_dsn, apply_schema_once) are used by tests in
tests/test_*.py that target the nerode DB (port 5435).

Calx/Trunkit fixtures (dsn, conn, has_primesieve) are used by tests that
target the calx DB (port 5434). They are imported from calx.db and require
CALX_TEST_DSN or ARITHMETIC_DB_TEST_DSN to be set, or the calx default DSN.

Marks:
  @pytest.mark.slow         requires generate(limit > 10_000)
  @pytest.mark.primesieve   requires the primesieve CLI on PATH
  @pytest.mark.network      makes real HTTP requests

DB-free modules (test_cert_kernel.py, test_cert_ledger.py — the consumer-side
kernel/ledger checkers) must not be blocked when no database is reachable: the
autouse schema fixture below SKIPS rather than ERRORS when the nerode DB is
unreachable, so those tests run in plain CI without Postgres.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import psycopg
import pytest
from psycopg import Connection

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
src_dir_str = str(SRC_DIR)
if src_dir_str not in sys.path:
    sys.path.insert(0, src_dir_str)

from nerode.db import apply_schema as nerode_apply_schema
from nerode.db import resolve_dsn as nerode_resolve_dsn

# ---------------------------------------------------------------------------
# Nerode fixtures
# ---------------------------------------------------------------------------

NERODE_DSN = os.environ.get("NERODE_TEST_DSN", nerode_resolve_dsn())


@pytest.fixture(scope="session")
def nerode_dsn() -> str:
    return NERODE_DSN


@pytest.fixture(scope="session", autouse=True)
def apply_schema_once():
    """Apply the nerode schema once per session, if a database is reachable.

    A short connect timeout keeps DB-free runs fast; on failure we yield without
    applying so DB-free tests proceed, and DB-backed tests skip individually via
    the `conn` fixtures below.
    """
    try:
        with psycopg.connect(NERODE_DSN, connect_timeout=3) as conn:
            nerode_apply_schema(conn)
    except psycopg.Error:
        pass
    yield


def _require_db() -> psycopg.Connection:
    try:
        return psycopg.connect(NERODE_DSN, autocommit=False, connect_timeout=3)
    except psycopg.Error as exc:
        pytest.skip(f"nerode DB not reachable: {exc}")


@pytest.fixture
def conn(apply_schema_once):
    c = _require_db()
    try:
        yield c
        c.rollback()
    finally:
        c.close()


@pytest.fixture
def committed_conn(apply_schema_once):
    c = _require_db()
    try:
        yield c
        c.commit()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Calx / Trunkit fixtures
# ---------------------------------------------------------------------------

def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "slow: requires non-trivial limit")
    config.addinivalue_line("markers", "primesieve: requires primesieve CLI on PATH")
    config.addinivalue_line("markers", "network: tests that make real HTTP requests")


@pytest.fixture(scope="session")
def calx_dsn() -> str:
    try:
        from calx import db as calx_db
        return (
            os.environ.get("CALX_TEST_DSN")
            or os.environ.get("ARITHMETIC_DB_TEST_DSN")
            or calx_db.resolve_dsn()
        )
    except ImportError:
        pytest.skip("calx package not installed")


@pytest.fixture(scope="session")
def _initialized_calx_db(calx_dsn: str) -> str:
    from calx import db as calx_db
    try:
        with calx_db.connect(calx_dsn) as c:
            calx_db.apply_schema(c)
    except psycopg.Error as exc:
        pytest.skip(f"calx DB not reachable: {exc}")
    return calx_dsn


@pytest.fixture()
def calx_conn(_initialized_calx_db: str) -> Connection:
    from calx import db as calx_db
    with calx_db.connect(_initialized_calx_db) as c:
        with c.cursor() as cur:
            cur.execute(
                "TRUNCATE factorizations, primes, integers, "
                "sequences, sequence_membership, integer_relations, "
                "orbits, oeis_match_candidates, "
                "composition_membership, oeis_compose_candidates, "
                "sequence_compositions, composition_runs "
                "RESTART IDENTITY CASCADE"
            )
            cur.execute("ALTER SEQUENCE orbit_id_seq RESTART WITH 1")
        c.commit()
        yield c


@pytest.fixture()
def has_primesieve() -> bool:
    return shutil.which("primesieve") is not None
