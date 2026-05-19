"""
Pytest configuration and shared fixtures for nerode tests.
"""

from __future__ import annotations

import os
import pytest
import psycopg

from nerode.db import apply_schema, resolve_dsn

DSN = os.environ.get("NERODE_TEST_DSN", resolve_dsn())


@pytest.fixture(scope="session")
def nerode_dsn() -> str:
    """Return the DSN string for use in functions that open their own connection."""
    return DSN


@pytest.fixture(scope="session", autouse=True)
def apply_schema_once():
    """Apply schema once per test session."""
    with psycopg.connect(DSN) as conn:
        apply_schema(conn)


@pytest.fixture
def conn(apply_schema_once):
    """Per-test connection that rolls back all changes after the test."""
    with psycopg.connect(DSN, autocommit=False) as c:
        yield c
        c.rollback()


@pytest.fixture
def committed_conn(apply_schema_once):
    """
    Per-test connection that commits at teardown (for reading state written
    by functions that manage their own connections and commit internally).
    Callers are responsible for not leaving dirty state.
    """
    with psycopg.connect(DSN, autocommit=False) as c:
        yield c
        c.commit()
