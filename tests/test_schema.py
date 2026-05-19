"""Smoke tests for schema and function existence — Nerode and Calx."""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Nerode schema smoke tests
# ---------------------------------------------------------------------------

def test_nerode_tables_exist(conn):
    rows = conn.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'nerode'
        ORDER BY table_name
        """
    ).fetchall()
    names = {r[0] for r in rows}
    assert "alphabets" in names
    assert "automata" in names
    assert "states" in names
    assert "transitions" in names
    assert "construction_log" in names


def test_nerode_export_json_function_exists(conn):
    conn.execute("SELECT nerode.export_json(0)")


# ---------------------------------------------------------------------------
# Calx schema smoke tests (skipped if calx package not installed)
# ---------------------------------------------------------------------------

CALX_TABLES = {
    "integers", "primes", "factorizations",
    "sequences", "sequence_membership", "integer_relations", "orbits",
}
CALX_VIEWS = {
    "prime_signatures", "divisor_count", "divisor_sum",
    "smooth_numbers", "perfect_numbers", "abundant_numbers", "deficient_numbers",
}
CALX_FUNCTIONS = {
    "ext_gcd", "mod_inverse", "crt_combine", "crt",
    "aliquot_step", "arithmetic_derivative",
}


def test_calx_tables_exist(calx_conn):
    with calx_conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'calx' AND table_type = 'BASE TABLE'"
        )
        tables = {row[0] for row in cur.fetchall()}
    assert CALX_TABLES.issubset(tables)


def test_calx_views_exist(calx_conn):
    with calx_conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.views "
            "WHERE table_schema = 'calx'"
        )
        views = {row[0] for row in cur.fetchall()}
    assert CALX_VIEWS.issubset(views)


def test_calx_functions_exist(calx_conn):
    with calx_conn.cursor() as cur:
        cur.execute(
            "SELECT routine_name FROM information_schema.routines "
            "WHERE routine_schema = 'calx' AND routine_type = 'FUNCTION'"
        )
        fns = {row[0] for row in cur.fetchall()}
    assert CALX_FUNCTIONS.issubset(fns)
