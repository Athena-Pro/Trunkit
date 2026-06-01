"""
tests/test_composite_dfa.py
============================
Tests for 97_composite_dfa.sql:
  - metric_x_control paired alphabet (9 symbols)
  - nerode.project_to_paired() — state/transition structure
  - nerode.ensure_composite_cybernetic() — idempotency, state count bound
  - dead_time_5_x_metric_oscillate — acceptance boundary

Acceptance logic of the composite:
  Pattern A_{5,} ∩ (UD){3,} over metric_x_control symbols.

  For the composite to accept, the same 6-step sequence must simultaneously
  satisfy both components.  Minimum accepting input:

    Step 1: U + A  → "UA"   (metric rises;  action taken)
    Step 2: D + _  → "D_"   (metric falls;  no response)
    Step 3: U + _  → "U_"   (metric rises;  no response)
    Step 4: D + _  → "D_"   (metric falls;  no response)
    Step 5: U + _  → "U_"   (metric rises;  no response)
    Step 6: D + _  → "D_"   (metric falls;  no response — 5 blanks after A)

  After step 6:
    metric_oscillate: UDUDUD = (UD){3} → state 6 (osc3+), accepting ✓
    dead_time_5:      A_____            → state 6 (alarm),  accepting ✓
  → composite accepts.
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


def _dfa_id(conn, name: str) -> int:
    row = conn.execute(
        "SELECT id FROM nerode.automata WHERE name = %s", (name,)
    ).fetchone()
    assert row, f"DFA {name!r} not found"
    return row[0]


def _rts(conn, dfa_id: int, inp: str) -> tuple[int | None, bool | None]:
    """Run dfa_id on a single-char-per-step string."""
    state = conn.execute(
        "SELECT nerode.run_to_state(%s, %s)", (dfa_id, inp)
    ).fetchone()[0]
    if state is None:
        return None, None
    accepting = conn.execute(
        "SELECT is_accepting FROM nerode.states "
        "WHERE automaton_id = %s AND state_id = %s",
        (dfa_id, state),
    ).fetchone()[0]
    return state, accepting


def _rts_arr(conn, dfa_id: int, syms: list[str]) -> tuple[int | None, bool | None]:
    """Run dfa_id on an explicit list of symbols (for multi-char alphabets)."""
    state = conn.execute(
        "SELECT nerode.run_to_state_arr(%s, %s)", (dfa_id, syms)
    ).fetchone()[0]
    if state is None:
        return None, None
    accepting = conn.execute(
        "SELECT is_accepting FROM nerode.states "
        "WHERE automaton_id = %s AND state_id = %s",
        (dfa_id, state),
    ).fetchone()[0]
    return state, accepting


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def conn(nerode_dsn):
    with _fresh(nerode_dsn) as c:
        yield c


@pytest.fixture(scope="module")
def composite_id(conn):
    return _dfa_id(conn, "dead_time_5_x_metric_oscillate")


# ---------------------------------------------------------------------------
# TestPairedAlphabet
# ---------------------------------------------------------------------------

class TestPairedAlphabet:

    def test_alphabet_exists(self, conn):
        row = conn.execute(
            "SELECT id FROM nerode.alphabets WHERE name = 'metric_x_control'"
        ).fetchone()
        assert row is not None

    def test_has_nine_symbols(self, conn):
        row = conn.execute(
            "SELECT array_length(symbols, 1) FROM nerode.alphabets "
            "WHERE name = 'metric_x_control'"
        ).fetchone()
        assert row[0] == 9

    def test_all_pairs_present(self, conn):
        syms = conn.execute(
            "SELECT symbols FROM nerode.alphabets WHERE name = 'metric_x_control'"
        ).fetchone()[0]
        sym_set = set(syms)
        for m in "UDS":
            for c in ("A", "R", "_"):
                assert m + c in sym_set, f"missing paired symbol {m}{c}"


# ---------------------------------------------------------------------------
# TestProjectToPaired
# ---------------------------------------------------------------------------

class TestProjectToPaired:

    def test_internal_projections_exist(self, conn):
        for suffix in ("_proj1_internal", "_proj2_internal"):
            row = conn.execute(
                "SELECT id FROM nerode.automata WHERE name = %s",
                ("dead_time_5_x_metric_oscillate" + suffix,),
            ).fetchone()
            assert row is not None, f"projection {suffix!r} not found"

    def test_proj1_has_metric_oscillate_state_count(self, conn):
        """Projection preserves state count of the source DFA."""
        src_count = conn.execute(
            "SELECT state_count FROM nerode.automata WHERE name = 'metric_oscillate'"
        ).fetchone()[0]
        proj_count = conn.execute(
            "SELECT state_count FROM nerode.automata "
            "WHERE name = 'dead_time_5_x_metric_oscillate_proj1_internal'"
        ).fetchone()[0]
        assert proj_count == src_count

    def test_proj1_uses_paired_alphabet(self, conn):
        alpha = conn.execute(
            "SELECT al.name FROM nerode.alphabets al "
            "JOIN nerode.automata a ON a.alphabet_id = al.id "
            "WHERE a.name = 'dead_time_5_x_metric_oscillate_proj1_internal'"
        ).fetchone()[0]
        assert alpha == "metric_x_control"

    def test_proj1_transition_count(self, conn):
        """Each of the 7 states × 9 paired symbols = 63 transitions (DFA is complete)."""
        proj_id = conn.execute(
            "SELECT id FROM nerode.automata "
            "WHERE name = 'dead_time_5_x_metric_oscillate_proj1_internal'"
        ).fetchone()[0]
        n = conn.execute(
            "SELECT COUNT(*) FROM nerode.transitions WHERE automaton_id = %s",
            (proj_id,),
        ).fetchone()[0]
        assert n == 63  # 7 states × 9 symbols

    def test_proj2_transition_count(self, conn):
        """dead_time_5 has 7 states × 9 symbols = 63 transitions after projection."""
        proj_id = conn.execute(
            "SELECT id FROM nerode.automata "
            "WHERE name = 'dead_time_5_x_metric_oscillate_proj2_internal'"
        ).fetchone()[0]
        n = conn.execute(
            "SELECT COUNT(*) FROM nerode.transitions WHERE automaton_id = %s",
            (proj_id,),
        ).fetchone()[0]
        assert n == 63


# ---------------------------------------------------------------------------
# TestCompositeDfa
# ---------------------------------------------------------------------------

class TestCompositeDfa:

    def test_composite_exists(self, composite_id):
        assert composite_id > 0

    def test_idempotent(self, conn, composite_id):
        id2 = conn.execute(
            "SELECT nerode.ensure_composite_cybernetic("
            "  'dead_time_5_x_metric_oscillate',"
            "  'metric_oscillate', 1,"
            "  'dead_time_5',      2"
            ")"
        ).fetchone()[0]
        assert id2 == composite_id

    def test_state_count_within_bound(self, conn, composite_id):
        """Reachable product states ≤ 7 × 7 = 49."""
        count = conn.execute(
            "SELECT state_count FROM nerode.automata WHERE id = %s",
            (composite_id,),
        ).fetchone()[0]
        assert 1 <= count <= 49

    def test_uses_paired_alphabet(self, conn, composite_id):
        alpha = conn.execute(
            "SELECT al.name FROM nerode.alphabets al "
            "JOIN nerode.automata a ON a.alphabet_id = al.id "
            "WHERE a.id = %s",
            (composite_id,),
        ).fetchone()[0]
        assert alpha == "metric_x_control"

    def test_empty_input_not_accepting(self, conn, composite_id):
        _, accepting = _rts_arr(conn, composite_id, [])
        assert accepting is False

    # ------------------------------------------------------------------
    # Acceptance: both conditions must hold simultaneously
    # Symbols are (metric, control) pairs: first char = metric, second = control.
    #
    # Minimal accepting sequence (6 steps):
    #   UA D_ U_ D_ U_ D_
    #   metric:  U D U D U D  = (UD){3}   → metric_oscillate accepts
    #   control: A _ _ _ _ _  = A_{5,}    → dead_time_5     accepts
    # ------------------------------------------------------------------

    def test_minimal_accepting_input(self, conn, composite_id):
        syms = ["UA", "D_", "U_", "D_", "U_", "D_"]
        _, accepting = _rts_arr(conn, composite_id, syms)
        assert accepting is True

    def test_one_step_short_dead_time_not_accepting(self, conn, composite_id):
        """5 steps — oscillation pattern complete but only 4 blanks after A."""
        syms = ["UA", "D_", "U_", "D_", "U_"]
        _, accepting = _rts_arr(conn, composite_id, syms)
        assert accepting is False

    def test_oscillation_incomplete_not_accepting(self, conn, composite_id):
        """Dead-time alarms at step 6 but oscillation only (UD){2} so far."""
        # 8 steps: (UD){2} met by step 4, then 4 more S_ steps for dead_time
        syms = ["UA", "D_", "U_", "D_", "S_", "S_", "S_", "S_"]
        _, accepting = _rts_arr(conn, composite_id, syms)
        assert accepting is False

    def test_dead_time_only_not_accepting(self, conn, composite_id):
        """A then 5 blanks — dead_time alarms but metric is always stable (S)."""
        syms = ["SA", "S_", "S_", "S_", "S_", "S_"]
        _, accepting = _rts_arr(conn, composite_id, syms)
        assert accepting is False

    def test_oscillation_only_not_accepting(self, conn, composite_id):
        """Pure metric oscillation with no action — dead_time stays idle."""
        syms = ["U_", "D_", "U_", "D_", "U_", "D_"]
        _, accepting = _rts_arr(conn, composite_id, syms)
        assert accepting is False

    def test_response_clears_dead_time(self, conn, composite_id):
        """Oscillation completes, but a response on step 6 clears the timer."""
        # Step 6 is UR (metric continues oscillation, control responds) →
        # dead_time resets to idle; composite must not accept.
        syms = ["UA", "D_", "U_", "D_", "U_", "DR"]
        _, accepting = _rts_arr(conn, composite_id, syms)
        assert accepting is False

    def test_stable_metric_resets_oscillation(self, conn, composite_id):
        """S at step 4 resets metric_oscillate; composite cannot accept at step 6."""
        syms = ["UA", "D_", "U_", "S_", "U_", "D_"]
        _, accepting = _rts_arr(conn, composite_id, syms)
        assert accepting is False

    def test_longer_sequence_still_accepting(self, conn, composite_id):
        """Extra steps after acceptance — composite stays in accept region."""
        base = ["UA", "D_", "U_", "D_", "U_", "D_"]
        extra = ["D_", "D_", "D_"]
        _, accepting = _rts_arr(conn, composite_id, base + extra)
        assert accepting is True
