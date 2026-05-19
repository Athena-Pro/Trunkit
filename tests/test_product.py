"""Tests: nerode.product() — intersection and union."""

import pytest


def _build(conn, pattern, symbols=None):
    if symbols is None:
        return conn.execute(
            "SELECT nerode.from_regex(%s)", (pattern,)
        ).fetchone()[0]
    return conn.execute(
        "SELECT nerode.from_regex(%s, NULL, %s)", (pattern, symbols)
    ).fetchone()[0]


def _run(conn, aid, word):
    return conn.execute(
        "SELECT accept FROM nerode.run(%s, %s)", (aid, word)
    ).fetchone()[0]


def test_intersection(conn):
    # a*  ∩  a+  =  a+
    aid1 = _build(conn, "a*")
    aid2 = _build(conn, "a+")
    prod = conn.execute(
        "SELECT nerode.product(%s, %s, 'intersection')", (aid1, aid2)
    ).fetchone()[0]

    assert _run(conn, prod, "") is False
    assert _run(conn, prod, "a") is True
    assert _run(conn, prod, "aaa") is True


def test_union_via_product(conn):
    # {a}  ∪  {b}  =  a|b  (shared alphabet {a,b} required for product)
    aid1 = _build(conn, "a", ["a", "b"])
    aid2 = _build(conn, "b", ["a", "b"])
    prod = conn.execute(
        "SELECT nerode.product(%s, %s, 'union')", (aid1, aid2)
    ).fetchone()[0]

    assert _run(conn, prod, "a") is True
    assert _run(conn, prod, "b") is True
    assert _run(conn, prod, "c") is False


def test_intersection_empty(conn):
    # {a} ∩ {b} = ∅  (shared alphabet {a,b} required for product)
    aid1 = _build(conn, "a", ["a", "b"])
    aid2 = _build(conn, "b", ["a", "b"])
    prod = conn.execute(
        "SELECT nerode.product(%s, %s, 'intersection')", (aid1, aid2)
    ).fetchone()[0]

    assert _run(conn, prod, "a") is False
    assert _run(conn, prod, "b") is False
