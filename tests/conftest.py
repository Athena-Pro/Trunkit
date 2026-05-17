"""Test fixtures.

Tests require a Postgres reachable via ``CALX_TEST_DSN`` (or fall back
to libpq env defaults). Each test runs against a freshly-created database;
schema is applied once per session, data is reset per test via TRUNCATE.

Marks:
  @pytest.mark.slow         requires generate(limit > 10_000); deselect with -m "not slow"
  @pytest.mark.primesieve   requires the primesieve CLI on PATH
"""

from __future__ import annotations

import os
import shutil

import pytest
from psycopg import Connection

from calx import db


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "slow: requires non-trivial limit")
    config.addinivalue_line("markers", "primesieve: requires primesieve CLI on PATH")


@pytest.fixture(scope="session")
def dsn() -> str:
    return os.environ.get("CALX_TEST_DSN") or os.environ.get("ARITHMETIC_DB_TEST_DSN") or db.resolve_dsn()


@pytest.fixture(scope="session")
def _initialized_db(dsn: str) -> str:
    with db.connect(dsn) as conn:
        db.apply_schema(conn)
    return dsn


@pytest.fixture()
def conn(_initialized_db: str) -> Connection:
    with db.connect(_initialized_db) as c:
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
