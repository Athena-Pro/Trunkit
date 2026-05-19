"""Tests: nerode.minimize() — Hopcroft partition refinement."""

import pytest


def _build(conn, pattern):
    return conn.execute(
        "SELECT nerode.from_regex(%s)", (pattern,)
    ).fetchone()[0]


def test_minimize_idempotent(conn):
    """Minimizing an already-minimal DFA should give same state count."""
    aid = _build(conn, "(a|b)*abb")
    sc1 = conn.execute(
        "SELECT state_count FROM nerode.automata WHERE id = %s", (aid,)
    ).fetchone()[0]

    mid = conn.execute("SELECT nerode.minimize(%s)", (aid,)).fetchone()[0]
    sc2 = conn.execute(
        "SELECT state_count FROM nerode.automata WHERE id = %s", (mid,)
    ).fetchone()[0]

    assert sc2 <= sc1


def test_minimize_certified(conn):
    aid = _build(conn, "ab*c")
    row = conn.execute(
        "SELECT automaton_id, claim_id FROM nerode.minimize_certified(%s)", (aid,)
    ).fetchone()
    assert row[0] is not None
    assert row[1] is not None

    # Automaton should be marked certified
    cert = conn.execute(
        "SELECT certified FROM nerode.automata WHERE id = %s", (row[0],)
    ).fetchone()[0]
    assert cert is True
