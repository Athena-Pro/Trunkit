"""SQL ↔ Python kernel parity — closes the duplication-divergence risk.

The cert_kernel tier is implemented twice (PL/pgSQL `cert.kernel_verify` and
Python `calx.kernel.verify_witness`). An independent review flagged that the two
could silently diverge. This module enforces that they DON'T: every vector in
tests/kernel_vectors.py is run through *both* implementations and the verdicts
must be identical (and equal to the declared expectation).

Requires the Trunkit (calx/cert) DB with step 94 applied. If unreachable, the
DB-side tests skip — the Python side is covered DB-free in test_cert_kernel.py.
"""
from __future__ import annotations

import json

import pytest

from calx.kernel import verify_witness
from tests.kernel_vectors import VECTORS

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None


def _calx_dsn() -> str:
    import os

    from calx import db as calx_db
    return (
        os.environ.get("CALX_TEST_DSN")
        or os.environ.get("CALX_DSN")
        or calx_db.resolve_dsn()
    )


@pytest.fixture(scope="module")
def calx_conn():
    if psycopg is None:
        pytest.skip("psycopg not installed")
    try:
        conn = psycopg.connect(_calx_dsn(), autocommit=True, connect_timeout=3)
    except Exception as exc:  # noqa: BLE001 - any connect failure → skip, don't error
        pytest.skip(f"calx DB not reachable: {exc}")
    # Confirm the kernel is installed (step 94); skip rather than fail if not —
    # parity can only be checked against a DB that actually has the SQL kernel.
    try:
        present = conn.execute(
            "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE p.proname = 'kernel_verify' AND n.nspname = 'cert' LIMIT 1"
        ).fetchone()
    except Exception as exc:  # noqa: BLE001
        conn.close()
        pytest.skip(f"cannot query for cert.kernel_verify: {exc}")
    if present is None:
        conn.close()
        pytest.skip("cert.kernel_verify not installed (apply step 94 / make apply-trunkit)")
    yield conn
    conn.close()


def _sql_verdict(conn, witness: dict) -> bool | None:
    row = conn.execute(
        "SELECT ok FROM cert.kernel_verify(%s::jsonb)", (json.dumps(witness),)
    ).fetchone()
    return row[0] if row else None


@pytest.mark.parametrize("vid,witness,expected", VECTORS, ids=[v[0] for v in VECTORS])
def test_python_kernel_matches_expectation(vid, witness, expected):
    """The Python kernel returns the declared verdict (DB-free)."""
    ok, _ = verify_witness(witness)
    assert ok is expected, f"{vid}: python verdict {ok!r} != expected {expected!r}"


@pytest.mark.parametrize("vid,witness,expected", VECTORS, ids=[v[0] for v in VECTORS])
def test_sql_kernel_matches_python(calx_conn, vid, witness, expected):
    """The SQL kernel and the Python kernel agree, vector by vector.

    This is the parity guarantee: the two independent implementations of the
    cert_kernel tier must never disagree. Both are also checked against the
    declared expectation so a shared bug can't make them 'agree on wrong'.
    """
    py_ok, _ = verify_witness(witness)
    sql_ok = _sql_verdict(calx_conn, witness)
    assert sql_ok is expected, f"{vid}: SQL verdict {sql_ok!r} != expected {expected!r}"
    assert sql_ok is py_ok, f"{vid}: SQL {sql_ok!r} != Python {py_ok!r} (kernels diverged)"
