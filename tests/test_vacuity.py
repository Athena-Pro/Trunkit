"""Vacuous-truth meta-probe tests (99_cert_vacuity.sql).

cert.kan_laws_vacuity() empties every kan '%_laws' view's kan base tables
inside an always-rolled-back savepoint and asserts each boolean law column
reads NULL there — the "an engine that never ran attests unverified"
discipline that the chromatic (36c0d04) and shadow (a725a7b) fixes each
re-derived by hand.

The sabotage tests plant a deliberately vacuous laws view inside a
transaction that is rolled back at the end, proving the probe can fail —
a probe that cannot fail attests nothing.
"""

from __future__ import annotations

import os

import psycopg
import pytest


def _calx_dsn():
    dsn = os.environ.get("CALX_TEST_DSN") or os.environ.get("ARITHMETIC_DB_TEST_DSN")
    if not dsn:
        pytest.skip("No test DSN provided. Refusing to write to default/production ledger.")
    return dsn


@pytest.fixture()
def conn():
    try:
        c = psycopg.connect(_calx_dsn(), connect_timeout=3)
    except psycopg.Error as exc:
        pytest.skip(f"calx DB not reachable: {exc}")
    try:
        yield c
    finally:
        c.rollback()   # sabotage DDL must never land
        c.close()


VERDICTS = {"null_on_empty", "vacuous_true", "false_on_empty",
            "undeletable", "no_boolean_laws", "no_base_tables"}


def _run_probe(cur):
    cur.execute("SELECT ok, evidence FROM cert.kan_laws_vacuity()")
    return cur.fetchone()


def test_shipped_law_views_are_honest_on_empty(conn):
    with conn.cursor() as cur:
        ok, ev = _run_probe(cur)
    assert ev["violations"] == 0, f"vacuous law views: {ev['views']}"
    assert ok is not False
    for view, entry in ev["views"].items():
        status = entry if isinstance(entry, str) else entry["status"]
        assert status in VERDICTS, f"{view}: unknown status {status}"
        assert status not in ("vacuous_true", "false_on_empty"), f"{view}: {entry}"


def test_probe_rolls_back_the_emptying(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM kan.bigrading_support")
        before = cur.fetchone()[0]
        _run_probe(cur)
        cur.execute("SELECT count(*) FROM kan.bigrading_support")
        assert cur.fetchone()[0] == before


def _plant_laws_view(cur, empty_default):
    cur.execute("CREATE TABLE kan.__vacprobe_t (x INTEGER)")
    cur.execute(
        "CREATE VIEW kan.__vacprobe_laws AS "
        f"SELECT COALESCE(bool_and(x > 0), {empty_default}) AS law "
        "FROM kan.__vacprobe_t"
    )


def test_probe_detects_planted_vacuous_true(conn):
    with conn.cursor() as cur:
        _plant_laws_view(cur, "TRUE")
        ok, ev = _run_probe(cur)
        assert ok is False
        assert ev["views"]["__vacprobe_laws"]["status"] == "vacuous_true"
        assert ev["violations"] >= 1


def test_probe_detects_planted_false_on_empty(conn):
    with conn.cursor() as cur:
        _plant_laws_view(cur, "FALSE")
        ok, ev = _run_probe(cur)
        assert ok is False
        assert ev["views"]["__vacprobe_laws"]["status"] == "false_on_empty"


def test_probe_claim_is_registered(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM cert.claim "
            "WHERE probe_sql = 'SELECT ok, evidence FROM cert.kan_laws_vacuity()'"
        )
        assert cur.fetchone()[0] == 1
