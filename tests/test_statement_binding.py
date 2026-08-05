"""Tests for statement binding (106) and the digest recipes it depends on.

Unit tests (DB-free) pin the normalisation contract: what must change a
digest and what must not. DB-backed tests exercise the drift probe's three
verdicts. Skips cleanly when no test DSN is set.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from calx import goalhash

THEOREM = """
/-- A doc comment that should not affect the digest. -/
theorem Erdos125.target_theorem_0 (n : Nat) (h : 0 < n) :
    (A + B).lowerDensity = 0 := by
  sorry
"""

TRUE_PROBE = "SELECT TRUE, '{}'::jsonb"


# --- unit: the digest contract ---------------------------------------------

def test_extract_declaration_splits_at_top_level_assign():
    sig, body = goalhash.extract_declaration(THEOREM, "Erdos125.target_theorem_0")
    assert sig.startswith("theorem Erdos125.target_theorem_0")
    assert "lowerDensity = 0" in sig
    assert "sorry" in body
    assert "sorry" not in sig


def test_extract_declaration_matches_short_name():
    sig, _ = goalhash.extract_declaration(THEOREM, "target_theorem_0")
    assert "lowerDensity" in sig


def test_extract_declaration_missing_returns_none():
    assert goalhash.extract_declaration(THEOREM, "no_such_decl") is None


def test_assign_inside_brackets_does_not_end_the_signature():
    src = "theorem foo (c : Cfg := { x := 1 }) : True := trivial"
    sig, body = goalhash.extract_declaration(src, "foo")
    assert sig.endswith(": True")
    assert body == "trivial"


def test_comments_do_not_change_the_digest():
    a = "theorem foo : True"
    b = "theorem foo /- inline -/ : True -- trailing"
    assert goalhash.statement_digest(a) == goalhash.statement_digest(b)


def test_nested_block_comments_are_stripped():
    assert goalhash.normalize("a /- x /- y -/ z -/ b") == "a b"


def test_whitespace_is_collapsed_not_removed():
    assert goalhash.statement_digest("h  x") == goalhash.statement_digest("h\n  x")
    # Collapsing to nothing would merge distinct identifiers.
    assert goalhash.statement_digest("h x") != goalhash.statement_digest("hx")


def test_hypothesis_change_changes_the_digest():
    a = "theorem foo (n : Nat) (h : 0 < n) : P n"
    b = "theorem foo (n : Nat) (h : 0 ≤ n) : P n"
    assert goalhash.statement_digest(a) != goalhash.statement_digest(b)


def test_goal_digest_partition_is_unambiguous():
    # Same characters, different split between context and target.
    assert goalhash.goal_digest(target="b", context="a") != goalhash.goal_digest(
        target="", context="a b"
    )


def test_find_sorries_ignores_commented_ones():
    src = "theorem a : T := by\n  sorry\n-- sorry in a comment\n"
    hits = goalhash.find_sorries(src)
    assert len(hits) == 1


# --- DB-backed --------------------------------------------------------------

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
            cur.execute("SELECT to_regclass('cert.statement_binding')")
            if cur.fetchone()[0] is None:
                pytest.skip(
                    "cert.statement_binding missing — apply "
                    "106_cert_statement_binding.sql first"
                )
        yield c
    finally:
        c.close()


def _mk_claim(cur) -> int:
    cur.execute(
        "INSERT INTO cert.claim (subject_kind, subject_ref, statement,"
        " claim_kind, method, probe_sql)"
        " VALUES ('binding_test', '{}'::jsonb, %s, 'computational', 'comp_sql', %s)"
        " RETURNING id",
        (f"binding test claim {uuid.uuid4()}", TRUE_PROBE),
    )
    return cur.fetchone()[0]


def _bind(cur, claim_id, text, decl="Test.thm"):
    cur.execute(
        "SELECT (cert.bind_statement(%s, %s, %s, %s)).id",
        (claim_id, decl, text, goalhash.statement_digest(text)),
    )
    return cur.fetchone()[0]


def _bound(cur, claim_id):
    cur.execute("SELECT ok, evidence FROM cert.statement_bound(%s)", (claim_id,))
    return cur.fetchone()


def test_unbound_claim_is_unverified(conn):
    with conn.cursor() as cur:
        ok, ev = _bound(cur, _mk_claim(cur))
        assert ok is None
        assert "no statement binding" in ev["reason"]


def test_bound_but_unobserved_is_unverified(conn):
    with conn.cursor() as cur:
        cid = _mk_claim(cur)
        _bind(cur, cid, "theorem t : True")
        ok, ev = _bound(cur, cid)
        assert ok is None
        assert "never re-observed" in ev["reason"]


def test_matching_observation_is_valid(conn):
    with conn.cursor() as cur:
        cid = _mk_claim(cur)
        text = "theorem t (n : Nat) : P n"
        bid = _bind(cur, cid, text)
        cur.execute(
            "SELECT (cert.observe_statement(%s, %s, %s)).id",
            (bid, goalhash.statement_digest(text), text),
        )
        ok, ev = _bound(cur, cid)
        assert ok is True
        assert ev["decl"] == "Test.thm"


def test_drifted_statement_is_refuted(conn):
    with conn.cursor() as cur:
        cid = _mk_claim(cur)
        bound_text = "theorem t (n : Nat) (h : 0 < n) : P n"
        drifted = "theorem t (n : Nat) (h : 0 ≤ n) : P n"
        bid = _bind(cur, cid, bound_text)
        cur.execute(
            "SELECT (cert.observe_statement(%s, %s, %s)).id",
            (bid, goalhash.statement_digest(drifted), drifted),
        )
        ok, ev = _bound(cur, cid)
        assert ok is False
        assert "DRIFT" in ev["reason"]
        # Both texts travel with the verdict, so the diff is readable.
        assert ev["bound_text"] == bound_text
        assert ev["observed_text"] == drifted


def test_comment_only_edit_does_not_read_as_drift(conn):
    """The false positive most likely to make someone disable the check."""
    with conn.cursor() as cur:
        cid = _mk_claim(cur)
        bid = _bind(cur, cid, "theorem t : True")
        cur.execute(
            "SELECT (cert.observe_statement(%s, %s, %s)).id",
            (
                bid,
                goalhash.statement_digest("theorem t : True -- reworded note"),
                "theorem t : True -- reworded note",
            ),
        )
        assert _bound(cur, cid)[0] is True


def test_latest_observation_wins(conn):
    with conn.cursor() as cur:
        cid = _mk_claim(cur)
        text = "theorem t : True"
        bid = _bind(cur, cid, text)
        for digest in (goalhash.statement_digest("theorem t : False"),
                       goalhash.statement_digest(text)):
            cur.execute("SELECT (cert.observe_statement(%s, %s)).id", (bid, digest))
        assert _bound(cur, cid)[0] is True


def test_rebinding_is_a_new_row_and_the_newest_wins(conn):
    with conn.cursor() as cur:
        cid = _mk_claim(cur)
        _bind(cur, cid, "theorem t : P")
        bid2 = _bind(cur, cid, "theorem t : Q")
        cur.execute(
            "SELECT (cert.observe_statement(%s, %s)).id",
            (bid2, goalhash.statement_digest("theorem t : Q")),
        )
        ok, ev = _bound(cur, cid)
        assert ok is True
        assert ev["rebindings"] == 2


def test_binding_is_append_only(conn):
    with conn.cursor() as cur:
        cid = _mk_claim(cur)
        bid = _bind(cur, cid, "theorem t : True")
        with pytest.raises(psycopg.Error):
            cur.execute(
                "UPDATE cert.statement_binding SET decl_name = 'x' WHERE id = %s", (bid,)
            )


def test_binding_claim_is_recheckable(conn):
    with conn.cursor() as cur:
        cid = _mk_claim(cur)
        text = "theorem t : True"
        bid = _bind(cur, cid, text)
        cur.execute(
            "SELECT (cert.observe_statement(%s, %s)).id",
            (bid, goalhash.statement_digest(text)),
        )
        cur.execute("SELECT cert.statement_binding_claim(%s)", (cid,))
        probe_claim = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (probe_claim,))
        assert cur.fetchone()[0] == "valid"

        # Drift after the fact flips the SAME claim, which is the point of
        # making it a claim rather than a one-off report.
        cur.execute(
            "SELECT (cert.observe_statement(%s, %s)).id",
            (bid, goalhash.statement_digest("theorem t : False")),
        )
        cur.execute("SELECT (cert.check(%s)).status", (probe_claim,))
        assert cur.fetchone()[0] == "refuted"
