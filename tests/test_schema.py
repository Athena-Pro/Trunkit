"""Smoke tests for schema, indexes, and view existence."""

from __future__ import annotations

import pytest


EXPECTED_TABLES = {
    "integers",
    "primes",
    "factorizations",
    "sequences",
    "sequence_membership",
    "integer_relations",
    "orbits",
}
EXPECTED_VIEWS = {
    "prime_signatures",
    "divisor_count",
    "divisor_sum",
    "smooth_numbers",
    "perfect_numbers",
    "abundant_numbers",
    "deficient_numbers",
}
EXPECTED_FUNCTIONS = {
    "ext_gcd",
    "mod_inverse",
    "crt_combine",
    "crt",
    "wheel_spokes",
    "progression_intersect",
    "crt_decompose",
    "crt_reconstruct",
    "characterize_relation",
    "crt_class_neighbors",
    "aliquot_step",
    "arithmetic_derivative",
    "signature_step",
    "crt_lift_step",
    "radical_step",
    "shared_sequences",
}
EXPECTED_PROCEDURES = {
    "generate_integer_database",
    "generate_factorizations_only",
    "_build_factorizations_and_derived",
    "trace_orbit",
}


def test_tables_exist(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'calx' AND table_type = 'BASE TABLE'"
        )
        tables = {row[0] for row in cur.fetchall()}
    assert EXPECTED_TABLES.issubset(tables)


def test_views_exist(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.views "
            "WHERE table_schema = 'calx'"
        )
        views = {row[0] for row in cur.fetchall()}
    assert EXPECTED_VIEWS.issubset(views)


def test_functions_exist(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT routine_name FROM information_schema.routines "
            "WHERE routine_schema = 'calx' AND routine_type = 'FUNCTION'"
        )
        fns = {row[0] for row in cur.fetchall()}
    assert EXPECTED_FUNCTIONS.issubset(fns)


def test_procedures_exist(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT routine_name FROM information_schema.routines "
            "WHERE routine_schema = 'calx' AND routine_type = 'PROCEDURE'"
        )
        procs = {row[0] for row in cur.fetchall()}
    assert EXPECTED_PROCEDURES.issubset(procs)
