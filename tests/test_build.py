"""Tests: nerode.from_regex() — Thompson + subset + Hopcroft pipeline."""

import pytest


def _build(conn, pattern, name=None):
    row = conn.execute(
        "SELECT nerode.from_regex(%s, %s)", (pattern, name)
    ).fetchone()
    return row[0]


def _run(conn, auto_id, word):
    row = conn.execute(
        "SELECT accept FROM nerode.run(%s, %s)", (auto_id, word)
    ).fetchone()
    return row[0]


def test_single_literal(conn):
    aid = _build(conn, "a")
    assert _run(conn, aid, "a") is True
    assert _run(conn, aid, "b") is False
    assert _run(conn, aid, "") is False


def test_concatenation(conn):
    aid = _build(conn, "ab")
    assert _run(conn, aid, "ab") is True
    assert _run(conn, aid, "a") is False
    assert _run(conn, aid, "abc") is False


def test_union(conn):
    aid = _build(conn, "a|b")
    assert _run(conn, aid, "a") is True
    assert _run(conn, aid, "b") is True
    assert _run(conn, aid, "c") is False


def test_kleene_star(conn):
    aid = _build(conn, "a*")
    assert _run(conn, aid, "") is True
    assert _run(conn, aid, "a") is True
    assert _run(conn, aid, "aaa") is True
    assert _run(conn, aid, "b") is False


def test_plus(conn):
    aid = _build(conn, "a+")
    assert _run(conn, aid, "") is False
    assert _run(conn, aid, "a") is True
    assert _run(conn, aid, "aaa") is True


def test_optional(conn):
    aid = _build(conn, "ab?")
    assert _run(conn, aid, "a") is True
    assert _run(conn, aid, "ab") is True
    assert _run(conn, aid, "b") is False


def test_complex_pattern(conn):
    # (a|b)*abb
    aid = _build(conn, "(a|b)*abb")
    assert _run(conn, aid, "abb") is True
    assert _run(conn, aid, "aabb") is True
    assert _run(conn, aid, "babb") is True
    assert _run(conn, aid, "ab") is False
    assert _run(conn, aid, "") is False


def test_minimized_state_count(conn):
    # (a|b)*abb should minimize to 4 states (classic Sipser example)
    aid = _build(conn, "(a|b)*abb")
    row = conn.execute(
        "SELECT state_count FROM nerode.automata WHERE id = %s", (aid,)
    ).fetchone()
    assert row[0] == 4


def test_named_automaton(conn):
    aid = _build(conn, "a*b", "star_a_b")
    row = conn.execute(
        "SELECT name FROM nerode.automata WHERE id = %s", (aid,)
    ).fetchone()
    assert row[0] == "star_a_b"
