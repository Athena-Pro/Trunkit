"""Tests for the attempt ledger (109).

The load-bearing property is the boundary: an attempt records effort, never
truth. These tests pin that (append-only, no probe reads it as evidence) plus
the two payoffs — "has this been tried" and "a failed run still fills the goal
cache". Skips without a test DSN.
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
            cur.execute("SELECT to_regclass('cert.attempt')")
            if cur.fetchone()[0] is None:
                pytest.skip("cert.attempt missing — apply 109_cert_attempt.sql first")
        yield c
    finally:
        c.close()


@pytest.fixture()
def subject():
    return f"erdos:test-{uuid.uuid4().hex[:8]}"


def _attempt(cur, subject_key, outcome, **kw):
    cur.execute(
        # Explicit casts: psycopg sends a Python float as double precision and
        # a small int as smallint, neither of which resolves the overload.
        "SELECT (cert.record_attempt(%s,%s,%s,%s,%s,%s::numeric,%s::numeric,"
        "%s::integer,%s::bigint)).id",
        (
            subject_key,
            outcome,
            kw.get("agent", "nexus-basic"),
            kw.get("model", "gemini-3.1-pro"),
            kw.get("run_label", ""),
            kw.get("cost_usd"),
            kw.get("wall_clock_s"),
            kw.get("episodes"),
            kw.get("claim_id"),
        ),
    )
    return cur.fetchone()[0]


def _history(cur, subject_key):
    cur.execute(
        "SELECT attempts, best_outcome, total_cost_usd, agents, outcomes,"
        " settled_claim, goals_learned FROM cert.attempt_history(%s)",
        (subject_key,),
    )
    return cur.fetchone()


def test_unattempted_subject_is_empty(conn, subject):
    with conn.cursor() as cur:
        cur.execute("SELECT cert.attempted_before(%s)", (subject,))
        assert cur.fetchone()[0] is False
        attempts, best, cost, _agents, _outcomes, claim, learned = _history(cur, subject)
        assert (attempts, best, cost, claim, learned) == (0, None, None, None, 0)


def test_failed_attempts_accumulate_cost(conn, subject):
    with conn.cursor() as cur:
        _attempt(cur, subject, "exhausted", cost_usd=120.50, episodes=3000)
        _attempt(cur, subject, "exhausted", cost_usd=79.50, episodes=3000)
        attempts, best, cost, _agents, outcomes, claim, _learned = _history(cur, subject)
        assert attempts == 2
        assert best == "exhausted"
        assert float(cost) == pytest.approx(200.0)
        assert outcomes == {"exhausted": 2}
        assert claim is None
        cur.execute("SELECT cert.attempted_before(%s)", (subject,))
        assert cur.fetchone()[0] is True


def test_best_outcome_wins_over_later_failures(conn, subject):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO cert.claim (subject_kind, subject_ref, statement,"
            " claim_kind, method, probe_sql)"
            " VALUES ('attempt_test','{}'::jsonb,%s,'computational','comp_sql',%s)"
            " RETURNING id",
            (f"attempt test claim {uuid.uuid4()}", TRUE_PROBE),
        )
        cid = cur.fetchone()[0]
        _attempt(cur, subject, "exhausted", cost_usd=10)
        _attempt(cur, subject, "proved", cost_usd=40, claim_id=cid)
        _attempt(cur, subject, "error", cost_usd=1)
        attempts, best, cost, _agents, _outcomes, claim, _learned = _history(cur, subject)
        assert attempts == 3
        assert best == "proved"
        assert claim == cid
        assert float(cost) == pytest.approx(51.0)


def test_agents_and_models_are_collected(conn, subject):
    with conn.cursor() as cur:
        _attempt(cur, subject, "exhausted", agent="nexus-basic", model="gemini-3.1-pro")
        _attempt(cur, subject, "partial", agent="nexus-full", model="gemini-3.1-pro")
        _attempts, _best, _cost, agents, _outcomes, _claim, _learned = _history(cur, subject)
        assert sorted(agents) == ["nexus-basic", "nexus-full"]


def test_attempt_is_append_only(conn, subject):
    with conn.cursor() as cur:
        aid = _attempt(cur, subject, "exhausted")
        with pytest.raises(psycopg.Error):
            cur.execute("UPDATE cert.attempt SET outcome = 'proved' WHERE id = %s", (aid,))


def test_a_failed_attempt_still_fills_the_goal_cache(conn, subject):
    """The single highest-value thing a failure can do."""
    with conn.cursor() as cur:
        aid = _attempt(cur, subject, "exhausted", cost_usd=180)
        target = f"side lemma {uuid.uuid4()}"
        cur.execute(
            "SELECT (cert.register_goal(%s, %s)).id",
            (goalhash.goal_digest(target), target),
        )
        gid = cur.fetchone()[0]
        cur.execute("SELECT (cert.attempt_learned(%s, %s, 'proved')).goal_id", (aid, gid))
        assert cur.fetchone()[0] == gid

        _attempts, best, _cost, _agents, _outcomes, _claim, learned = _history(cur, subject)
        assert best == "exhausted"   # the run still failed
        assert learned == 1          # and it still taught the ledger something


def test_attempt_learned_is_idempotent(conn, subject):
    with conn.cursor() as cur:
        aid = _attempt(cur, subject, "partial")
        target = f"g {uuid.uuid4()}"
        cur.execute(
            "SELECT (cert.register_goal(%s, %s)).id",
            (goalhash.goal_digest(target), target),
        )
        gid = cur.fetchone()[0]
        cur.execute("SELECT (cert.attempt_learned(%s,%s,'open')).goal_id", (aid, gid))
        cur.execute("SELECT (cert.attempt_learned(%s,%s,'proved')).status", (aid, gid))
        assert cur.fetchone()[0] == "proved"
        cur.execute("SELECT COUNT(*) FROM cert.attempt_goal WHERE attempt_id = %s", (aid,))
        assert cur.fetchone()[0] == 1


def test_bad_outcome_is_rejected(conn, subject):
    with conn.cursor() as cur, pytest.raises(psycopg.Error):
        _attempt(cur, subject, "probably_fine")


def test_negative_cost_is_rejected(conn, subject):
    with conn.cursor() as cur, pytest.raises(psycopg.Error):
        _attempt(cur, subject, "exhausted", cost_usd=-1)


def test_run_summary_reports_an_honest_denominator(conn):
    with conn.cursor() as cur:
        run = f"sweep-{uuid.uuid4().hex[:8]}"
        for i in range(9):
            _attempt(cur, f"erdos:{run}-{i}", "exhausted", cost_usd=100, run_label=run)
        _attempt(cur, f"erdos:{run}-solved", "proved", cost_usd=300, run_label=run)
        cur.execute(
            "SELECT attempts, subjects, settled, settle_rate, cost_usd,"
            " cost_per_settled FROM cert.run_summary WHERE run_label = %s",
            (run,),
        )
        attempts, subjects, settled, rate, cost, per_settled = cur.fetchone()
        assert (attempts, subjects, settled) == (10, 10, 1)
        assert float(rate) == pytest.approx(0.1)
        assert float(cost) == pytest.approx(1200)
        # The number that matters: total spend divided by what was settled,
        # not the cost of the run that happened to work.
        assert float(per_settled) == pytest.approx(1200)


def test_spend_view_lists_unsettled_subjects(conn, subject):
    with conn.cursor() as cur:
        _attempt(cur, subject, "exhausted", cost_usd=50)
        _attempt(cur, subject, "error", cost_usd=2)
        cur.execute(
            "SELECT attempts, cost_usd, settled, exhausted, errored"
            " FROM cert.attempt_spend WHERE subject_key = %s",
            (subject,),
        )
        attempts, cost, settled, exhausted, errored = cur.fetchone()
        assert (attempts, settled, exhausted, errored) == (2, False, 1, 1)
        assert float(cost) == pytest.approx(52)
