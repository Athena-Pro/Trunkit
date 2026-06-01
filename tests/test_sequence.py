"""
tests/test_sequence.py
======================
Tests for nerode.sql/90_sequence.sql.

Covers:
  - run_sequence: step count, accepting steps, period
  - parallel_run: state_vector keys, accept_vector correctness
  - accepting_positions: matches expected periods
"""

from __future__ import annotations

import psycopg
import pytest

from tests.dbskip import connect_or_skip

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh(dsn: str) -> psycopg.Connection:
    return connect_or_skip(dsn, autocommit=True)


def _corpus_id(conn, slug: str) -> int:
    row = conn.execute(
        "SELECT automaton_id FROM nerode.corpus WHERE slug = %s", (slug,)
    ).fetchone()
    assert row, f"corpus slug {slug!r} not found"
    return row[0]


def _product_id(conn, lhs: str, rhs: str) -> int:
    row = conn.execute(
        "SELECT product_id FROM nerode.product_pairs WHERE lhs_slug=%s AND rhs_slug=%s",
        (lhs, rhs),
    ).fetchone()
    assert row, f"product {lhs}x{rhs} not found"
    return row[0]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def corpus_ids(nerode_dsn):
    with _fresh(nerode_dsn) as c:
        return {
            "c4":  _corpus_id(c, "cycle_4"),
            "c6":  _corpus_id(c, "cycle_6"),
            "c9":  _corpus_id(c, "cycle_9"),
            "c10": _corpus_id(c, "cycle_10"),
            "p46": _product_id(c, "cycle_4", "cycle_6"),
            "p49": _product_id(c, "cycle_4", "cycle_9"),
        }


# ---------------------------------------------------------------------------
# TestRunSequence
# ---------------------------------------------------------------------------


class TestRunSequence:
    """nerode.run_sequence(automaton_id, length, symbol)."""

    def test_returns_correct_row_count(self, nerode_dsn, corpus_ids):
        with _fresh(nerode_dsn) as c:
            rows = c.execute(
                "SELECT * FROM nerode.run_sequence(%s, 20)", (corpus_ids["c4"],)
            ).fetchall()
        assert len(rows) == 20

    def test_step_zero_is_initial_state(self, nerode_dsn, corpus_ids):
        """Step 0 must be the automaton's initial state."""
        with _fresh(nerode_dsn) as c:
            aid = corpus_ids["c4"]
            initial_sid = c.execute(
                "SELECT state_id FROM nerode.states WHERE automaton_id=%s AND is_initial",
                (aid,),
            ).fetchone()[0]
            row0 = c.execute(
                "SELECT state_id FROM nerode.run_sequence(%s, 1)", (aid,)
            ).fetchone()
        assert row0[0] == initial_sid

    def test_steps_are_consecutive(self, nerode_dsn, corpus_ids):
        with _fresh(nerode_dsn) as c:
            rows = c.execute(
                "SELECT step FROM nerode.run_sequence(%s, 15)", (corpus_ids["c6"],)
            ).fetchall()
        steps = [r[0] for r in rows]
        assert steps == list(range(15))

    def test_cycle4_accepts_at_multiples_of_4(self, nerode_dsn, corpus_ids):
        """cycle_4 should accept at steps 0, 4, 8, 12, 16."""
        with _fresh(nerode_dsn) as c:
            rows = c.execute(
                "SELECT step, is_accepting FROM nerode.run_sequence(%s, 20)",
                (corpus_ids["c4"],),
            ).fetchall()
        accepting = [r[0] for r in rows if r[1]]
        assert accepting == [0, 4, 8, 12, 16]

    def test_cycle6_accepts_at_multiples_of_6(self, nerode_dsn, corpus_ids):
        with _fresh(nerode_dsn) as c:
            rows = c.execute(
                "SELECT step, is_accepting FROM nerode.run_sequence(%s, 25)",
                (corpus_ids["c6"],),
            ).fetchall()
        accepting = [r[0] for r in rows if r[1]]
        assert accepting == [0, 6, 12, 18, 24]

    def test_cycle9_accepts_at_multiples_of_9(self, nerode_dsn, corpus_ids):
        with _fresh(nerode_dsn) as c:
            rows = c.execute(
                "SELECT step, is_accepting FROM nerode.run_sequence(%s, 40)",
                (corpus_ids["c9"],),
            ).fetchall()
        accepting = [r[0] for r in rows if r[1]]
        assert accepting == [0, 9, 18, 27, 36]

    def test_cycle10_accepts_at_multiples_of_10(self, nerode_dsn, corpus_ids):
        with _fresh(nerode_dsn) as c:
            rows = c.execute(
                "SELECT step, is_accepting FROM nerode.run_sequence(%s, 35)",
                (corpus_ids["c10"],),
            ).fetchall()
        accepting = [r[0] for r in rows if r[1]]
        assert accepting == [0, 10, 20, 30]

    def test_state_ids_periodic(self, nerode_dsn, corpus_ids):
        """State IDs must repeat with period = state_count."""
        with _fresh(nerode_dsn) as c:
            aid = corpus_ids["c4"]
            rows = c.execute(
                "SELECT step, state_id FROM nerode.run_sequence(%s, 16)", (aid,)
            ).fetchall()
        # period 4: rows 0..3 must match rows 4..7, 8..11, 12..15
        sids = [r[1] for r in rows]
        assert sids[0:4] == sids[4:8] == sids[8:12] == sids[12:16]

    def test_length_zero_returns_no_rows(self, nerode_dsn, corpus_ids):
        with _fresh(nerode_dsn) as c:
            rows = c.execute(
                "SELECT * FROM nerode.run_sequence(%s, 0)", (corpus_ids["c4"],)
            ).fetchall()
        assert len(rows) == 0

    def test_length_one_returns_one_row(self, nerode_dsn, corpus_ids):
        with _fresh(nerode_dsn) as c:
            rows = c.execute(
                "SELECT * FROM nerode.run_sequence(%s, 1)", (corpus_ids["c4"],)
            ).fetchall()
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# TestParallelRun
# ---------------------------------------------------------------------------


class TestParallelRun:
    """nerode.parallel_run(automaton_ids[], length, symbol)."""

    def test_returns_correct_row_count(self, nerode_dsn, corpus_ids):
        ids = [corpus_ids["c4"], corpus_ids["c6"]]
        with _fresh(nerode_dsn) as c:
            rows = c.execute(
                "SELECT * FROM nerode.parallel_run(%s, 12)", (ids,)
            ).fetchall()
        assert len(rows) == 12

    def test_state_vector_has_all_automata_keys(self, nerode_dsn, corpus_ids):
        auto_ids = [corpus_ids["c4"], corpus_ids["c6"], corpus_ids["c9"]]
        with _fresh(nerode_dsn) as c:
            rows = c.execute(
                "SELECT step, state_vector FROM nerode.parallel_run(%s, 5)", (auto_ids,)
            ).fetchall()
        for step, sv in rows:
            for aid in auto_ids:
                assert str(aid) in sv, f"step {step}: automaton {aid} missing from state_vector"

    def test_accept_vector_has_all_automata_keys(self, nerode_dsn, corpus_ids):
        auto_ids = [corpus_ids["c4"], corpus_ids["c6"]]
        with _fresh(nerode_dsn) as c:
            rows = c.execute(
                "SELECT step, accept_vector FROM nerode.parallel_run(%s, 5)", (auto_ids,)
            ).fetchall()
        for step, av in rows:
            for aid in auto_ids:
                assert str(aid) in av

    def test_accept_vector_matches_individual_sequences(self, nerode_dsn, corpus_ids):
        """parallel_run accept_vector must agree with individual run_sequence results."""
        c4 = corpus_ids["c4"]
        c6 = corpus_ids["c6"]
        length = 24  # lcm(4,6) * 2

        with _fresh(nerode_dsn) as c:
            par = c.execute(
                "SELECT step, accept_vector FROM nerode.parallel_run(%s, %s)",
                ([c4, c6], length),
            ).fetchall()
            seq4 = {r[0]: r[2] for r in c.execute(
                "SELECT step, state_id, is_accepting FROM nerode.run_sequence(%s, %s)",
                (c4, length),
            ).fetchall()}
            seq6 = {r[0]: r[2] for r in c.execute(
                "SELECT step, state_id, is_accepting FROM nerode.run_sequence(%s, %s)",
                (c6, length),
            ).fetchall()}

        for step, av in par:
            assert bool(av[str(c4)]) == seq4[step], f"step {step}: c4 accept mismatch"
            assert bool(av[str(c6)]) == seq6[step], f"step {step}: c6 accept mismatch"

    def test_accept_quad_matches_mod_arithmetic(self, nerode_dsn, corpus_ids):
        """For all 4 corpus DFAs, accept_vector[aid] == (step mod k == 0)."""
        mapping = [
            (corpus_ids["c4"],  4),
            (corpus_ids["c6"],  6),
            (corpus_ids["c9"],  9),
            (corpus_ids["c10"], 10),
        ]
        auto_ids = [m[0] for m in mapping]
        length = 60

        with _fresh(nerode_dsn) as c:
            rows = c.execute(
                "SELECT step, accept_vector FROM nerode.parallel_run(%s, %s)",
                (auto_ids, length),
            ).fetchall()

        for step, av in rows:
            for aid, k in mapping:
                expected = (step % k == 0)
                actual   = bool(av[str(aid)])
                assert actual == expected, (
                    f"step {step}: DFA(k={k}) accept={actual}, expected {expected}"
                )

    def test_single_automaton_parallel_equals_run_sequence(self, nerode_dsn, corpus_ids):
        """parallel_run with one DFA must match run_sequence."""
        c9 = corpus_ids["c9"]
        length = 27
        with _fresh(nerode_dsn) as c:
            par = c.execute(
                "SELECT step, accept_vector FROM nerode.parallel_run(%s, %s)",
                ([c9], length),
            ).fetchall()
            seq = {r[0]: r[2] for r in c.execute(
                "SELECT step, state_id, is_accepting FROM nerode.run_sequence(%s, %s)",
                (c9, length),
            ).fetchall()}
        for step, av in par:
            assert bool(av[str(c9)]) == seq[step]


# ---------------------------------------------------------------------------
# TestAcceptingPositions
# ---------------------------------------------------------------------------


class TestAcceptingPositions:
    """nerode.accepting_positions(automaton_id, length, symbol)."""

    def test_cycle4_multiples_of_4(self, nerode_dsn, corpus_ids):
        with _fresh(nerode_dsn) as c:
            pos = c.execute(
                "SELECT nerode.accepting_positions(%s, 20)", (corpus_ids["c4"],)
            ).fetchone()[0]
        assert pos == [0, 4, 8, 12, 16]

    def test_cycle6_multiples_of_6(self, nerode_dsn, corpus_ids):
        with _fresh(nerode_dsn) as c:
            pos = c.execute(
                "SELECT nerode.accepting_positions(%s, 25)", (corpus_ids["c6"],)
            ).fetchone()[0]
        assert pos == [0, 6, 12, 18, 24]

    def test_product_cycle4_cycle9_multiples_of_36(self, nerode_dsn, corpus_ids):
        """Product of cycle_4 x cycle_9 accepts at multiples of lcm(4,9)=36."""
        with _fresh(nerode_dsn) as c:
            pos = c.execute(
                "SELECT nerode.accepting_positions(%s, 180)", (corpus_ids["p49"],)
            ).fetchone()[0]
        assert pos == [0, 36, 72, 108, 144]

    def test_product_cycle4_cycle6_multiples_of_12(self, nerode_dsn, corpus_ids):
        """Product of cycle_4 x cycle_6 accepts at multiples of lcm(4,6)=12."""
        with _fresh(nerode_dsn) as c:
            pos = c.execute(
                "SELECT nerode.accepting_positions(%s, 60)", (corpus_ids["p46"],)
            ).fetchone()[0]
        assert pos == [0, 12, 24, 36, 48]

    def test_result_is_sorted(self, nerode_dsn, corpus_ids):
        with _fresh(nerode_dsn) as c:
            pos = c.execute(
                "SELECT nerode.accepting_positions(%s, 40)", (corpus_ids["c9"],)
            ).fetchone()[0]
        assert pos == sorted(pos)

    def test_all_positions_within_length(self, nerode_dsn, corpus_ids):
        length = 30
        with _fresh(nerode_dsn) as c:
            pos = c.execute(
                "SELECT nerode.accepting_positions(%s, %s)", (corpus_ids["c4"], length)
            ).fetchone()[0]
        assert all(0 <= p < length for p in pos)

    def test_count_equals_floor_div(self, nerode_dsn, corpus_ids):
        """Number of accepting positions for cycle_k in [0, length) = floor((length-1)/k) + 1."""
        length = 100
        with _fresh(nerode_dsn) as c:
            for slug, k in [("c4", 4), ("c6", 6), ("c9", 9), ("c10", 10)]:
                pos = c.execute(
                    "SELECT nerode.accepting_positions(%s, %s)",
                    (corpus_ids[slug], length),
                ).fetchone()[0]
                expected_count = (length - 1) // k + 1  # floor((length-1)/k) + 1
                assert len(pos) == expected_count, (
                    f"{slug}: expected {expected_count} positions, got {len(pos)}"
                )

    def test_no_accepting_positions_returns_null_or_empty(self, nerode_dsn, corpus_ids):
        """With length < period, only step 0 accepts."""
        with _fresh(nerode_dsn) as c:
            pos = c.execute(
                "SELECT nerode.accepting_positions(%s, 4)", (corpus_ids["c10"],)
            ).fetchone()[0]
        # Only step 0 accepts (step 10 is beyond length 4)
        assert pos == [0]
