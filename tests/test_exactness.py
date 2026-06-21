"""Exact-domain shield tests (94). float_heuristic can never record a valid cert."""

from __future__ import annotations

import os
import uuid

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
    with c:
        yield c


def _always_true_claim(cur, domain):
    stmt = f"shield probe {uuid.uuid4()}"
    cur.execute(
        "INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql) "
        "VALUES ('test','{}'::jsonb,%s,'computational','comp_sql',%s) RETURNING id",
        (stmt, "SELECT true AS ok, '{}'::jsonb AS evidence"),
    )
    cid = cur.fetchone()[0]
    cur.execute("SELECT cert.set_domain(%s,%s)", (cid, domain))
    cur.execute("SELECT (cert.check(%s)).status", (cid,))
    return cid, cur.fetchone()[0]


def test_float_heuristic_downgraded(conn):
    with conn.cursor() as cur:
        cid, status = _always_true_claim(cur, "float_heuristic")
        assert status == "unverified"            # shielded from 'valid'
        cur.execute("SELECT evidence->>'shield' FROM cert.certificate "
                    "WHERE claim_id=%s ORDER BY seq DESC LIMIT 1", (cid,))
        assert cur.fetchone()[0] is not None


def test_exact_int_stays_valid(conn):
    with conn.cursor() as cur:
        _, status = _always_true_claim(cur, "exact_int")
        assert status == "valid"


def test_unknown_domain_rejected(conn):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql) "
            "VALUES ('test','{}'::jsonb,%s,'computational','comp_sql',%s) RETURNING id",
            (f"dom {uuid.uuid4()}", "SELECT true AS ok, '{}'::jsonb AS evidence"),
        )
        cid = cur.fetchone()[0]
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute("SELECT cert.set_domain(%s,'imaginary')", (cid,))
