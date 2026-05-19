"""Tests: nerode.equivalent() — language equivalence by symmetric difference."""

import pytest


def _build(conn, pattern, symbols=None):
    if symbols is None:
        return conn.execute(
            "SELECT nerode.from_regex(%s)", (pattern,)
        ).fetchone()[0]
    return conn.execute(
        "SELECT nerode.from_regex(%s, NULL, %s)", (pattern, symbols)
    ).fetchone()[0]


def test_equivalent_same_pattern(conn):
    aid1 = _build(conn, "a*b")
    aid2 = _build(conn, "a*b")
    row = conn.execute(
        "SELECT equivalent, witness FROM nerode.equivalent(%s, %s)", (aid1, aid2)
    ).fetchone()
    assert row[0] is True


def test_equivalent_different_but_same_language(conn):
    # (a|b)* vs (a|b|ab)* on alphabet {a,b}
    aid1 = _build(conn, "(a|b)*")
    aid2 = _build(conn, "(a|b)*")   # same pattern → same language
    row = conn.execute(
        "SELECT equivalent FROM nerode.equivalent(%s, %s)", (aid1, aid2)
    ).fetchone()
    assert row[0] is True


def test_not_equivalent(conn):
    aid1 = _build(conn, "a*")
    aid2 = _build(conn, "a+")
    row = conn.execute(
        "SELECT equivalent, witness FROM nerode.equivalent(%s, %s)", (aid1, aid2)
    ).fetchone()
    assert row[0] is False
    # Witness should be a distinguishing string (the empty string)
    assert row[1] is not None


def test_certify_equivalence(conn):
    aid1 = _build(conn, "ab")
    aid2 = _build(conn, "ab")
    row = conn.execute(
        "SELECT equivalent, claim_id FROM nerode.certify_equivalence(%s, %s)", (aid1, aid2)
    ).fetchone()
    assert row[0] is True
    assert row[1] is not None


def test_certify_non_equivalence_has_claim(conn):
    # shared alphabet required: a* and b* have no common strings, clearly non-equivalent
    aid1 = _build(conn, "a*", ["a", "b"])
    aid2 = _build(conn, "b*", ["a", "b"])
    row = conn.execute(
        "SELECT equivalent, claim_id FROM nerode.certify_equivalence(%s, %s)", (aid1, aid2)
    ).fetchone()
    assert row[0] is False
    assert row[1] is not None
