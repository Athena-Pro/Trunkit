"""Tests for load-bearing gaps and support ratio (108).

The closure walks are the risky part — cycles, depth, direction — so they get
tested directly rather than only through the probe. Skips without a test DSN.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from calx import goalhash

TRUE_PROBE = "SELECT TRUE, '{}'::jsonb"


def _dsn():
    dsn = os.environ.get("CALX_TEST_DSN") or os.environ.get("ARITHMETIC_DB_TEST_DSN")
    if not dsn:
        pytest.skip("No test DSN provided. Refusing to write to default/production ledger.")
    return dsn


@pytest.fixture()
def conn():
    try:
        c = psycopg.connect(_dsn(), connect_timeout=3)
    except psycopg.Error as exc:
        pytest.skip(f"test DB not reachable: {exc}")
    c.autocommit = True
    try:
        with c.cursor() as cur:
            cur.execute("SELECT to_regprocedure('cert.gap_impact(bigint)')")
            if cur.fetchone()[0] is None:
                pytest.skip("cert.gap_impact missing — apply 108_cert_gap_metric.sql first")
        yield c
    finally:
        c.close()


def _mk_claim(cur, domain="gap_metric_test") -> int:
    cur.execute(
        "INSERT INTO cert.claim (subject_kind, subject_ref, statement,"
        " claim_kind, method, probe_sql, domain)"
        " VALUES ('gap_test', '{}'::jsonb, %s, 'computational', 'comp_sql', %s, %s)"
        " RETURNING id",
        (f"gap metric claim {uuid.uuid4()}", TRUE_PROBE, domain),
    )
    return cur.fetchone()[0]


def _derive(cur, conclusion, premises):
    cur.execute(
        "INSERT INTO cert.derivation (conclusion_id, premise_ids, rule)"
        " VALUES (%s, %s::bigint[], 'modus_ponens') RETURNING id",
        (conclusion, premises),
    )
    return cur.fetchone()[0]


def _gap(cur, claim_id, decl="Helper", kind="routine"):
    target = f"gap {uuid.uuid4()}"
    cur.execute(
        "SELECT (cert.register_goal(%s, %s)).id", (goalhash.goal_digest(target), target)
    )
    gid = cur.fetchone()[0]
    cur.execute(
        "SELECT (cert.record_goal_occurrence(%s,%s,'gap','open',%s,%s)).id",
        (gid, claim_id, decl, kind),
    )
    return gid


def _impacts(cur, claim_id):
    cur.execute(
        "SELECT blast_radius, shared_with, load_bearing FROM cert.gap_impact(%s)",
        (claim_id,),
    )
    return cur.fetchall()


def _probe(cur, claim_id):
    cur.execute("SELECT ok, evidence FROM cert.no_load_bearing_gaps(%s)", (claim_id,))
    return cur.fetchone()


def test_dependent_closure_walks_downstream(conn):
    with conn.cursor() as cur:
        a, b, c = _mk_claim(cur), _mk_claim(cur), _mk_claim(cur)
        _derive(cur, b, [a])
        _derive(cur, c, [b])
        cur.execute(
            "SELECT claim_id, depth FROM cert.dependent_closure(%s) ORDER BY depth", (a,)
        )
        assert cur.fetchall() == [(b, 1), (c, 2)]


def test_support_closure_walks_upstream(conn):
    with conn.cursor() as cur:
        a, b, c = _mk_claim(cur), _mk_claim(cur), _mk_claim(cur)
        _derive(cur, b, [a])
        _derive(cur, c, [b])
        cur.execute(
            "SELECT claim_id, depth FROM cert.support_closure(%s) ORDER BY depth", (c,)
        )
        assert cur.fetchall() == [(b, 1), (a, 2)]


def test_closure_terminates_on_a_cycle(conn):
    with conn.cursor() as cur:
        a, b = _mk_claim(cur), _mk_claim(cur)
        _derive(cur, b, [a])
        _derive(cur, a, [b])
        cur.execute("SELECT COUNT(*) FROM cert.dependent_closure(%s)", (a,))
        assert cur.fetchone()[0] >= 1  # terminates rather than hanging


def test_isolated_gap_is_not_load_bearing(conn):
    with conn.cursor() as cur:
        cid = _mk_claim(cur)
        _gap(cur, cid)
        (blast, shared, bearing), = _impacts(cur, cid)
        assert (blast, shared, bearing) == (0, 0, False)


def test_gap_under_dependents_is_load_bearing(conn):
    with conn.cursor() as cur:
        carrier, mid, top = _mk_claim(cur), _mk_claim(cur), _mk_claim(cur)
        _derive(cur, mid, [carrier])
        _derive(cur, top, [mid])
        _gap(cur, carrier)
        (blast, _shared, bearing), = _impacts(cur, carrier)
        assert blast == 2
        assert bearing is True


def test_shared_gap_is_load_bearing_without_any_derivation_edge(conn):
    """Two claims can stand or fall together with no edge between them."""
    with conn.cursor() as cur:
        a, b = _mk_claim(cur), _mk_claim(cur)
        gid = _gap(cur, a, decl="A")
        cur.execute(
            "SELECT (cert.record_goal_occurrence(%s,%s,'gap','open','B','routine')).id",
            (gid, b),
        )
        (blast, shared, bearing), = _impacts(cur, a)
        assert blast == 0          # no derivation edges at all
        assert shared == 1
        assert bearing is True     # ...and still load-bearing


def test_probe_is_unverified_without_goals(conn):
    with conn.cursor() as cur:
        ok, ev = _probe(cur, _mk_claim(cur))
        assert ok is None
        assert "no goals recorded" in ev["reason"]


def test_probe_is_valid_when_no_gap_bears_load(conn):
    with conn.cursor() as cur:
        cid = _mk_claim(cur)
        _gap(cur, cid)
        ok, ev = _probe(cur, cid)
        assert ok is True
        assert ev["load_bearing"] == 0


def test_probe_is_unverified_when_gaps_bear_load_but_claim_does_not_stand(conn):
    with conn.cursor() as cur:
        carrier, top = _mk_claim(cur), _mk_claim(cur)
        _derive(cur, top, [carrier])
        _gap(cur, carrier)
        ok, ev = _probe(cur, carrier)
        assert ok is None
        assert ev["standing"] in ("unchecked", "unverified")


def test_probe_is_refuted_when_a_valid_claim_carries_a_load_bearing_gap(conn):
    with conn.cursor() as cur:
        carrier, top = _mk_claim(cur), _mk_claim(cur)
        _derive(cur, top, [carrier])
        _gap(cur, carrier, decl="core_insight", kind="strategic")
        cur.execute("SELECT (cert.check(%s)).status", (carrier,))
        assert cur.fetchone()[0] == "valid"
        ok, ev = _probe(cur, carrier)
        assert ok is False
        assert ev["load_bearing_gaps"][0]["gap_kind"] == "strategic"
        assert ev["load_bearing_gaps"][0]["blast_radius"] == 1


def test_support_ratio_reports_both_numbers(conn):
    with conn.cursor() as cur:
        domain = f"ratio_{uuid.uuid4().hex[:8]}"
        base = [_mk_claim(cur, domain) for _ in range(5)]
        top = _mk_claim(cur, domain)
        _derive(cur, top, base[:2])          # only 2 of 6 support the target
        cur.execute(
            "SELECT supporting, universe, ratio FROM cert.support_ratio(%s)", (top,)
        )
        supporting, universe, ratio = cur.fetchone()
        assert supporting == 2
        assert universe == 6
        assert float(ratio) == pytest.approx(2 / 6, abs=1e-4)


def test_gap_board_ranks_by_blast_radius(conn):
    with conn.cursor() as cur:
        loud, quiet, dep = _mk_claim(cur), _mk_claim(cur), _mk_claim(cur)
        _derive(cur, dep, [loud])
        _gap(cur, loud, decl="loud")
        _gap(cur, quiet, decl="quiet")
        cur.execute(
            "SELECT decl_name, blast_radius FROM cert.gap_board"
            " WHERE claim_id IN (%s, %s) ORDER BY blast_radius DESC",
            (loud, quiet),
        )
        rows = cur.fetchall()
        assert rows[0][0] == "loud"
        assert rows[0][1] > rows[-1][1]
