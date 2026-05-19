"""Tests: schema smoke — tables and helper functions exist."""

import pytest


def test_schema_tables_exist(conn):
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


def test_export_json_function_exists(conn):
    conn.execute("SELECT nerode.export_json(0)")  # may return NULL for id 0; just must not error on missing func
    # If automaton 0 doesn't exist export_json should return NULL gracefully
    # (it uses a LEFT JOIN internally)
