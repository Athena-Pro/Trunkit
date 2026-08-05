"""Tests for goal-level content hashing (107).

Covers the three questions the layer exists to answer: has this goal been
settled anywhere (standing-aware cache), do two claims share an obligation,
and does a claim's own gap restate its target. Skips without a test DSN.
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
            cur.execute("SELECT to_regclass('cert.goal')")
            if cur.fetchone()[0] is None:
                pytest.skip("cert.goal missing — apply 107_cert_goal_hash.sql first")
        yield c
    finally:
        c.close()


def _mk_claim(cur) -> int:
    cur.execute(
        "INSERT INTO cert.claim (subject_kind, subject_ref, statement,"
        " claim_kind, method, probe_sql)"
        " VALUES ('goal_test', '{}'::jsonb, %s, 'computational', 'comp_sql', %s)"
        " RETURNING id",
        (f"goal test claim {uuid.uuid4()}", TRUE_PROBE),
    )
    return cur.fetchone()[0]


def _goal(cur, target: str, context: str = "") -> int:
    cur.execute(
        "SELECT (cert.register_goal(%s, %s, %s)).id",
        (goalhash.goal_digest(target, context), target, context),
    )
    return cur.fetchone()[0]


def _occ(cur, goal_id, claim_id, role, status="open", decl="", gap_kind=None):
    cur.execute(
        "SELECT (cert.record_goal_occurrence(%s,%s,%s,%s,%s,%s)).id",
        (goal_id, claim_id, role, status, decl, gap_kind),
    )
    return cur.fetchone()[0]


def _noncircular(cur, claim_id):
    cur.execute("SELECT ok, evidence FROM cert.goal_noncircular(%s)", (claim_id,))
    return cur.fetchone()


def test_registering_the_same_goal_twice_is_idempotent(conn):
    with conn.cursor() as cur:
        target = f"P {uuid.uuid4()}"
        assert _goal(cur, target) == _goal(cur, target)


def test_same_goal_different_formatting_collides_on_purpose(conn):
    with conn.cursor() as cur:
        tag = uuid.uuid4()
        a = _goal(cur, f"forall n, P n {tag}")
        b = _goal(cur, f"forall n,   P n {tag}   -- note")
        assert a == b


def test_goal_is_append_only(conn):
    with conn.cursor() as cur:
        gid = _goal(cur, f"P {uuid.uuid4()}")
        with pytest.raises(psycopg.Error):
            cur.execute("UPDATE cert.goal SET target_text = 'x' WHERE id = %s", (gid,))


def test_unseen_goal_is_not_settled(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT settled, occurrences FROM cert.goal_resolved(%s)",
            (goalhash.goal_digest(f"never seen {uuid.uuid4()}"),),
        )
        settled, occurrences = cur.fetchone()
        assert settled is False
        assert occurrences == 0


def test_goal_proved_by_a_standing_claim_is_a_cache_hit(conn):
    with conn.cursor() as cur:
        target = f"lemma {uuid.uuid4()}"
        gid, cid = _goal(cur, target), _mk_claim(cur)
        _occ(cur, gid, cid, "subgoal", "proved", decl="Helper.a")
        cur.execute("SELECT (cert.check(%s)).status", (cid,))
        cur.execute(
            "SELECT settled, resolution, claim_id FROM cert.goal_resolved(%s)",
            (goalhash.goal_digest(target),),
        )
        settled, resolution, claim_id = cur.fetchone()
        assert settled is True
        assert resolution == "proved"
        assert claim_id == cid


def test_revoked_claim_is_not_a_cache_hit(conn):
    """A filesystem cache cannot do this; a ledger-backed one must."""
    with conn.cursor() as cur:
        target = f"lemma {uuid.uuid4()}"
        gid, cid = _goal(cur, target), _mk_claim(cur)
        _occ(cur, gid, cid, "subgoal", "proved", decl="Helper.b")
        cur.execute("SELECT (cert.check(%s)).status", (cid,))
        cur.execute("SELECT (cert.revoke_claim(%s, %s)).id", (cid, "goal cache test"))
        cur.execute(
            "SELECT settled FROM cert.goal_resolved(%s)", (goalhash.goal_digest(target),)
        )
        assert cur.fetchone()[0] is False


def test_shared_goal_shows_both_claims(conn):
    with conn.cursor() as cur:
        target = f"shared {uuid.uuid4()}"
        gid = _goal(cur, target)
        a, b = _mk_claim(cur), _mk_claim(cur)
        _occ(cur, gid, a, "subgoal", "open", decl="A")
        _occ(cur, gid, b, "gap", "open", decl="B")
        cur.execute(
            "SELECT n_claims, claim_ids, open_somewhere FROM cert.shared_goals"
            " WHERE goal_id = %s",
            (gid,),
        )
        n_claims, claim_ids, open_somewhere = cur.fetchone()
        assert n_claims == 2
        assert sorted(claim_ids) == sorted([a, b])
        assert open_somewhere is True


def test_a_gap_cannot_be_recorded_as_proved(conn):
    with conn.cursor() as cur:
        gid, cid = _goal(cur, f"g {uuid.uuid4()}"), _mk_claim(cur)
        with pytest.raises(psycopg.Error):
            _occ(cur, gid, cid, "gap", "proved")


def test_gap_defaults_to_unclassified(conn):
    with conn.cursor() as cur:
        gid, cid = _goal(cur, f"g {uuid.uuid4()}"), _mk_claim(cur)
        oid = _occ(cur, gid, cid, "gap")
        cur.execute("SELECT gap_kind FROM cert.goal_occurrence WHERE id = %s", (oid,))
        assert cur.fetchone()[0] == "unclassified"


def test_no_target_recorded_is_unverified(conn):
    with conn.cursor() as cur:
        cid = _mk_claim(cur)
        ok, ev = _noncircular(cur, cid)
        assert ok is None
        assert "no target goal" in ev["reason"]


def test_ordinary_gap_is_noncircular(conn):
    with conn.cursor() as cur:
        cid = _mk_claim(cur)
        _occ(cur, _goal(cur, f"target {uuid.uuid4()}"), cid, "target", "open")
        _occ(cur, _goal(cur, f"routine {uuid.uuid4()}"), cid, "gap", "open",
             decl="Helper", gap_kind="routine")
        ok, ev = _noncircular(cur, cid)
        assert ok is True
        assert ev["open_gaps"] == 1


def test_gap_restating_the_target_is_refuted(conn):
    """AlphaProof Nexus failure mode #1, caught by string comparison."""
    with conn.cursor() as cur:
        cid = _mk_claim(cur)
        target = f"(A + B).lowerDensity = 0  {uuid.uuid4()}"
        gid = _goal(cur, target)
        _occ(cur, gid, cid, "target", "open", decl="target_theorem_0")
        # The "helper lemma" restates the target, reformatted and commented.
        restated = _goal(cur, f"(A + B).lowerDensity   = 0  {uuid.uuid4()}")
        assert restated != gid  # different text -> different goal
        same = _goal(cur, f"(A + B).lowerDensity = 0 -- helper  {uuid.uuid4()}")
        assert same != gid
        _occ(cur, gid, cid, "gap", "open", decl="helper_lemma", gap_kind="strategic")

        ok, ev = _noncircular(cur, cid)
        assert ok is False
        assert "CIRCULAR" in ev["reason"]
        assert ev["circular_gaps"][0]["decl"] == "helper_lemma"


def test_noncircular_claim_is_recheckable(conn):
    with conn.cursor() as cur:
        cid = _mk_claim(cur)
        gid = _goal(cur, f"t {uuid.uuid4()}")
        _occ(cur, gid, cid, "target", "open")
        cur.execute("SELECT cert.goal_noncircular_claim(%s)", (cid,))
        probe = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (probe,))
        assert cur.fetchone()[0] == "valid"

        _occ(cur, gid, cid, "gap", "open", decl="circular_helper")
        cur.execute("SELECT (cert.check(%s)).status", (probe,))
        assert cur.fetchone()[0] == "refuted"
