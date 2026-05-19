"""Tests: nerode.run() — DFA simulation and certify_run()."""

import pytest


def _build(conn, pattern):
    return conn.execute(
        "SELECT nerode.from_regex(%s)", (pattern,)
    ).fetchone()[0]


def test_run_accept(conn):
    aid = _build(conn, "ab*c")
    row = conn.execute(
        "SELECT accept FROM nerode.run(%s, %s)", (aid, "ac")
    ).fetchone()
    assert row[0] is True


def test_run_reject(conn):
    aid = _build(conn, "ab*c")
    row = conn.execute(
        "SELECT accept FROM nerode.run(%s, %s)", (aid, "ab")
    ).fetchone()
    assert row[0] is False


def test_run_evidence(conn):
    aid = _build(conn, "a")
    row = conn.execute(
        "SELECT accept, evidence FROM nerode.run(%s, %s)", (aid, "a")
    ).fetchone()
    assert row[0] is True
    assert row[1] is not None


def test_certify_run(conn):
    aid = _build(conn, "a*b")
    row = conn.execute(
        "SELECT accept, claim_id FROM nerode.certify_run(%s, %s)", (aid, "aab")
    ).fetchone()
    assert row[0] is True
    assert row[1] is not None


def test_certify_run_reject_has_claim(conn):
    aid = _build(conn, "a*b")
    row = conn.execute(
        "SELECT accept, claim_id FROM nerode.certify_run(%s, %s)", (aid, "aac")
    ).fetchone()
    assert row[0] is False
    assert row[1] is not None
