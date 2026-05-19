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
"""

from __future__ import annotations

import os
import shutil

import psycopg
import pytest
from psycopg import Connection

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
    with psycopg.connect(NERODE_DSN) as conn:
        nerode_apply_schema(conn)


@pytest.fixture
def conn(apply_schema_once):
    with psycopg.connect(NERODE_DSN, autocommit=False) as c:
        yield c
        c.rollback()


@pytest.fixture
def committed_conn(apply_schema_once):
    with psycopg.connect(NERODE_DSN, autocommit=False) as c:
        yield c
        c.commit()


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
    with calx_db.connect(calx_dsn) as c:
        calx_db.apply_schema(c)
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
