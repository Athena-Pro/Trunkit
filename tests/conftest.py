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

# A DEDICATED test DSN or nothing.  `nerode_resolve_dsn()` used to be the
# fallback, which meant a plain `pytest` run applied the nerode schema to
# whatever that resolved to -- today the stopped 5435, but the calx side of this
# same pattern put 16 claims and 32 certificates into the canonical ledger
# (see tests/test_cert_formal_lean.py).  Failing safe by accident is not the
# same as being safe.
NERODE_DSN = os.environ.get("NERODE_TEST_DSN")


@pytest.fixture(scope="session")
def nerode_dsn() -> str:
    """The guard has to live HERE, not only in `_require_db`.

    ~200 tests take this fixture and hand the value straight to `_fresh(...)` /
    `connect_or_skip(...)`, most without a connect_timeout.  Returning None
    (which is what "no NERODE_TEST_DSN" used to produce once the resolve_dsn
    fallback was removed) does not raise: psycopg treats a None dsn as "use the
    libpq defaults", so the suite BLOCKS instead of skipping.  Observed
    2026-07-26 -- a pytest process sitting at 6s CPU with no registered server
    connection, going nowhere.
    """
    if not NERODE_DSN:
        pytest.skip("No NERODE_TEST_DSN provided. Refusing to use a "
                    "default/production nerode instance.")
    return NERODE_DSN


@pytest.fixture(scope="session")
def apply_schema_once() -> bool:
    """Apply the nerode schema once per session; report whether the DB is reachable.

    A short connect timeout keeps DB-free runs fast; the reachability result is
    cached for the whole session so `conn`/`committed_conn` can skip instantly
    on every subsequent test instead of retrying the connection themselves.
    """
    if not NERODE_DSN:
        return False
    try:
        with psycopg.connect(NERODE_DSN, connect_timeout=3) as conn:
            nerode_apply_schema(conn)
    except psycopg.Error:
        return False
    return True


def _require_db(reachable: bool) -> psycopg.Connection:
    if not NERODE_DSN:
        pytest.skip("No NERODE_TEST_DSN provided. Refusing to write to a "
                    "default/production nerode instance.")
    if not reachable:
        pytest.skip("nerode DB not reachable")
    return psycopg.connect(NERODE_DSN, autocommit=False, connect_timeout=3)


@pytest.fixture
def conn(apply_schema_once: bool):
    c = _require_db(apply_schema_once)
    try:
        yield c
        c.rollback()
    finally:
        c.close()


@pytest.fixture
def committed_conn(apply_schema_once: bool):
    c = _require_db(apply_schema_once)
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
    """A DEDICATED test DSN, or skip.  Never the resolved default.

    This fixture feeds `_initialized_calx_db`, which calls `apply_schema()`, so
    the fallback that used to live here (`calx_db.resolve_dsn()`) meant a plain
    `pytest` run wrote schema into whatever DSN the package resolved to -- and
    for the write-heavy tests downstream, rows as well.  That is not
    hypothetical: 16 `Erdős test claim <uuid> (lean bridge)` claims and 32
    certificates reached the canonical ledger this way between 2026-07-01 and
    07-13, revoked 2026-07-25.

    It was masked for as long as `calx.db.DEFAULT_DSN` pointed at the stopped
    5434 docker instance: the connect failed and everything skipped.  Correcting
    that default to the canonical 5432 (also 2026-07-25) removed the mask, so
    the guard has to be explicit rather than incidental.

    Set CALX_TEST_DSN to a scratch database -- NOT the canonical ledger -- to
    run the DB-backed tests.
    """
    try:
        from calx import db as calx_db  # noqa: F401  (import-guard only)
    except ImportError:
        pytest.skip("calx package not installed")
    dsn = (os.environ.get("CALX_TEST_DSN")
           or os.environ.get("ARITHMETIC_DB_TEST_DSN"))
    if not dsn:
        pytest.skip("No CALX_TEST_DSN provided. Refusing to apply schema or "
                    "write to the default/production ledger.")
    return dsn


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
