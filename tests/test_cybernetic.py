"""
tests/test_cybernetic.py
========================
Tests for nerode.sql/95_cybernetic_automata.sql.

Covers:
  - DFA registration: all 8 DFAs with correct state counts and alphabet names
  - metric alphabet: metric_rise_3, metric_oscillate, metric_bounce_3
    — accept/reject boundaries, sensor-persistence, reset cases
  - control alphabet: dead_time_5/10/20
    — exact boundary at k steps (the off-by-one that was caught and fixed)
  - homeostasis alphabet: homeostasis_alarm_5, homeostasis_stable_5
    — standard cases + "never-left-band" false-convergence guard
  - log_cybernetic / scan_cybernetic pipeline
    — insertion, per-alphabet seq independence, no-error on match
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


def _rts(conn, dfa_id: int, input_str: str) -> tuple[int | None, bool | None]:
    """Return (final_state_id, is_accepting) after running input_str through dfa_id."""
    state = conn.execute(
        "SELECT nerode.run_to_state(%s, %s)", (dfa_id, input_str)
    ).fetchone()[0]
    if state is None:
        return None, None
    accepting = conn.execute(
        "SELECT is_accepting FROM nerode.states WHERE automaton_id = %s AND state_id = %s",
        (dfa_id, state),
    ).fetchone()[0]
    return state, accepting


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def dfas(nerode_dsn):
    with _fresh(nerode_dsn) as c:
        return {
            name: _dfa_id(c, name)
            for name in [
                "metric_rise_3",
                "metric_oscillate",
                "metric_bounce_3",
                "dead_time_5",
                "dead_time_10",
                "dead_time_20",
                "homeostasis_alarm_5",
                "homeostasis_stable_5",
            ]
        }


# ---------------------------------------------------------------------------
# TestDfaRegistration
# ---------------------------------------------------------------------------

class TestDfaRegistration:
    """All 8 DFAs exist with the correct state counts and alphabet names."""

    EXPECTED = {
        "metric_rise_3":        ("metric",      4),
        "metric_oscillate":     ("metric",      7),
        "metric_bounce_3":      ("metric",      7),
        "dead_time_5":          ("control",     7),
        "dead_time_10":         ("control",    12),
        "dead_time_20":         ("control",    22),
        "homeostasis_alarm_5":  ("homeostasis", 6),
        "homeostasis_stable_5": ("homeostasis", 7),
    }

    def test_all_dfas_exist(self, nerode_dsn):
        with _fresh(nerode_dsn) as c:
            for name in self.EXPECTED:
                row = c.execute(
                    "SELECT id FROM nerode.automata WHERE name = %s", (name,)
                ).fetchone()
                assert row is not None, f"DFA {name!r} missing"

    def test_state_counts(self, nerode_dsn, dfas):
        with _fresh(nerode_dsn) as c:
            for name, (_, expected_count) in self.EXPECTED.items():
                actual = c.execute(
                    "SELECT state_count FROM nerode.automata WHERE id = %s",
                    (dfas[name],),
                ).fetchone()[0]
                assert actual == expected_count, (
                    f"{name}: state_count={actual}, expected {expected_count}"
                )

    def test_alphabet_assignments(self, nerode_dsn, dfas):
        with _fresh(nerode_dsn) as c:
            for name, (expected_alpha, _) in self.EXPECTED.items():
                actual = c.execute(
                    """SELECT al.name
                       FROM nerode.automata a
                       JOIN nerode.alphabets al ON al.id = a.alphabet_id
                       WHERE a.id = %s""",
                    (dfas[name],),
                ).fetchone()[0]
                assert actual == expected_alpha, (
                    f"{name}: alphabet={actual!r}, expected {expected_alpha!r}"
                )

    def test_each_dfa_has_exactly_one_initial_state(self, nerode_dsn, dfas):
        with _fresh(nerode_dsn) as c:
            for name, dfa_id in dfas.items():
                count = c.execute(
                    "SELECT COUNT(*) FROM nerode.states "
                    "WHERE automaton_id = %s AND is_initial = TRUE",
                    (dfa_id,),
                ).fetchone()[0]
                assert count == 1, f"{name}: {count} initial states"

    def test_each_dfa_has_exactly_one_accepting_state(self, nerode_dsn, dfas):
        with _fresh(nerode_dsn) as c:
            for name, dfa_id in dfas.items():
                count = c.execute(
                    "SELECT COUNT(*) FROM nerode.states "
                    "WHERE automaton_id = %s AND is_accepting = TRUE",
                    (dfa_id,),
                ).fetchone()[0]
                assert count == 1, f"{name}: {count} accepting states"


# ---------------------------------------------------------------------------
# TestMetricRise3   U{3,}
# ---------------------------------------------------------------------------

class TestMetricRise3:

    def test_empty_input_is_initial_not_accepting(self, nerode_dsn, dfas):
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["metric_rise_3"], "")
        assert acc is False

    def test_one_and_two_rises_not_accepted(self, nerode_dsn, dfas):
        with _fresh(nerode_dsn) as c:
            _, a1 = _rts(c, dfas["metric_rise_3"], "U")
            _, a2 = _rts(c, dfas["metric_rise_3"], "UU")
        assert a1 is False
        assert a2 is False

    def test_three_rises_accepted(self, nerode_dsn, dfas):
        """Boundary: exactly 3 U's triggers acceptance."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["metric_rise_3"], "UUU")
        assert acc is True

    def test_more_than_three_rises_accepted(self, nerode_dsn, dfas):
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["metric_rise_3"], "UUUUUU")
        assert acc is True

    def test_d_resets_counter(self, nerode_dsn, dfas):
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["metric_rise_3"], "UUD")
        assert acc is False

    def test_s_resets_counter(self, nerode_dsn, dfas):
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["metric_rise_3"], "UUS")
        assert acc is False

    def test_reset_then_three_rises_accepted(self, nerode_dsn, dfas):
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["metric_rise_3"], "DUUU")
        assert acc is True

    def test_interrupted_run_not_accepted(self, nerode_dsn, dfas):
        """UUU then reset leaves only one U in current run."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["metric_rise_3"], "UUUDU")
        assert acc is False


# ---------------------------------------------------------------------------
# TestMetricOscillate   (UD){3,}
# ---------------------------------------------------------------------------

class TestMetricOscillate:

    def test_fewer_than_3_ud_cycles_not_accepted(self, nerode_dsn, dfas):
        with _fresh(nerode_dsn) as c:
            _, a1 = _rts(c, dfas["metric_oscillate"], "UD")
            _, a2 = _rts(c, dfas["metric_oscillate"], "UDUD")
        assert a1 is False
        assert a2 is False

    def test_three_ud_cycles_accepted(self, nerode_dsn, dfas):
        """Boundary: exactly 3 UD cycles."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["metric_oscillate"], "UDUDUD")
        assert acc is True

    def test_more_than_three_ud_cycles_accepted(self, nerode_dsn, dfas):
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["metric_oscillate"], "UDUDUDUDUD")
        assert acc is True

    def test_sensor_persistence_u_absorbed(self, nerode_dsn, dfas):
        """Multiple U's while waiting for D are absorbed (same state)."""
        with _fresh(nerode_dsn) as c:
            s_single, _ = _rts(c, dfas["metric_oscillate"], "U")
            s_double, _ = _rts(c, dfas["metric_oscillate"], "UU")
        assert s_single == s_double

    def test_sensor_persistence_does_not_prevent_acceptance(self, nerode_dsn, dfas):
        """Doubled symbols still reach acceptance."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["metric_oscillate"], "UUDDUUDDUUDD")
        assert acc is True

    def test_s_resets_from_any_state(self, nerode_dsn, dfas):
        with _fresh(nerode_dsn) as c:
            s_init, _ = _rts(c, dfas["metric_oscillate"], "")
            s_after, _ = _rts(c, dfas["metric_oscillate"], "UDUDUS")
        assert s_init == s_after

    def test_starts_with_d_not_accepted(self, nerode_dsn, dfas):
        """D before any U resets to 0 — pattern must start with U."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["metric_oscillate"], "DUDUDU")
        assert acc is False


# ---------------------------------------------------------------------------
# TestMetricBounce3   D{3,}U{3,}
# ---------------------------------------------------------------------------

class TestMetricBounce3:

    def test_fewer_than_3_d_not_accepted(self, nerode_dsn, dfas):
        """2 D's then 3 U's — insufficient descent, resets on U from state 2."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["metric_bounce_3"], "DDUUU")
        assert acc is False

    def test_three_d_three_u_accepted(self, nerode_dsn, dfas):
        """Boundary: exactly D{3}U{3}."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["metric_bounce_3"], "DDDUUU")
        assert acc is True

    def test_extra_d_absorbed_in_descent(self, nerode_dsn, dfas):
        """State 3 (DDD+) self-loops on D — extra descent steps are valid."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["metric_bounce_3"], "DDDDUUU")
        assert acc is True

    def test_extra_u_absorbed_in_ascent(self, nerode_dsn, dfas):
        """State 6 (accept) self-loops on U."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["metric_bounce_3"], "DDDUUUUU")
        assert acc is True

    def test_fewer_than_3_u_not_accepted(self, nerode_dsn, dfas):
        with _fresh(nerode_dsn) as c:
            _, a1 = _rts(c, dfas["metric_bounce_3"], "DDDUU")
            _, a2 = _rts(c, dfas["metric_bounce_3"], "DDDU")
        assert a1 is False
        assert a2 is False

    def test_u_interrupting_descent_restarts(self, nerode_dsn, dfas):
        """U from state 1 or 2 resets — must get all 3 D's uninterrupted."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["metric_bounce_3"], "DDUDDUUU")
        assert acc is False

    def test_s_resets(self, nerode_dsn, dfas):
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["metric_bounce_3"], "DDDSUUU")
        assert acc is False


# ---------------------------------------------------------------------------
# TestDeadTime   A_{k,}
# ---------------------------------------------------------------------------

class TestDeadTime:
    """
    Explicit boundary tests for all three k values.

    The off-by-one bug (k+1 states instead of k+2) caused alarm at k-1 steps.
    Each test checks: k-1 _ → no alarm, k _ → alarm.
    """

    @pytest.mark.parametrize("name,k", [
        ("dead_time_5",  5),
        ("dead_time_10", 10),
        ("dead_time_20", 20),
    ])
    def test_k_minus_1_blanks_not_alarm(self, nerode_dsn, dfas, name, k):
        input_str = "A" + "_" * (k - 1)
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas[name], input_str)
        assert acc is False, f"{name}: A + {k-1} _'s should not alarm yet"

    @pytest.mark.parametrize("name,k", [
        ("dead_time_5",  5),
        ("dead_time_10", 10),
        ("dead_time_20", 20),
    ])
    def test_k_blanks_triggers_alarm(self, nerode_dsn, dfas, name, k):
        """Boundary: exactly k blanks after A fires the alarm."""
        input_str = "A" + "_" * k
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas[name], input_str)
        assert acc is True, f"{name}: A + {k} _'s should alarm"

    def test_response_before_alarm_clears(self, nerode_dsn, dfas):
        """R arriving before k steps resets to idle — no alarm."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["dead_time_5"], "A____R")
        assert acc is False

    def test_response_after_alarm_clears(self, nerode_dsn, dfas):
        """Late R (after alarm fired) still resets to idle."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["dead_time_5"], "A_____R")
        assert acc is False

    def test_new_action_resets_timer(self, nerode_dsn, dfas):
        """A during wait_n restarts the counter from 1."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["dead_time_5"], "A___A____")
        assert acc is False  # second A resets; only 4 _ follow

    def test_alarm_persists_without_response(self, nerode_dsn, dfas):
        """Once in alarm state, more _ symbols keep it there."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["dead_time_5"], "A_______")
        assert acc is True

    def test_second_cycle_alarms_correctly(self, nerode_dsn, dfas):
        """After a clear, a new A+k_ sequence alarms again."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["dead_time_5"], "A_____RA_____")
        assert acc is True

    def test_blanks_without_action_not_alarm(self, nerode_dsn, dfas):
        """_ from idle state stays idle — no pending action."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["dead_time_5"], "_____")
        assert acc is False


# ---------------------------------------------------------------------------
# TestHomeostasisAlarm5   O{5,}
# ---------------------------------------------------------------------------

class TestHomeostasisAlarm5:

    def test_fewer_than_5_o_not_accepted(self, nerode_dsn, dfas):
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["homeostasis_alarm_5"], "OOOO")
        assert acc is False

    def test_five_o_accepted(self, nerode_dsn, dfas):
        """Boundary: exactly 5 O's."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["homeostasis_alarm_5"], "OOOOO")
        assert acc is True

    def test_more_than_five_o_accepted(self, nerode_dsn, dfas):
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["homeostasis_alarm_5"], "OOOOOOOO")
        assert acc is True

    def test_i_resets_counter(self, nerode_dsn, dfas):
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["homeostasis_alarm_5"], "OOOOI")
        assert acc is False

    def test_i_then_five_o_accepted(self, nerode_dsn, dfas):
        """I resets to 0; 5 fresh O's still alarm."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["homeostasis_alarm_5"], "IOOOOO")
        assert acc is True

    def test_alternating_does_not_alarm(self, nerode_dsn, dfas):
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["homeostasis_alarm_5"], "IOIOIO")
        assert acc is False


# ---------------------------------------------------------------------------
# TestHomeostasisStable5   O+I{5,}
# ---------------------------------------------------------------------------

class TestHomeostasisStable5:

    def test_never_left_band_not_convergence(self, nerode_dsn, dfas):
        """I's without a preceding O are not convergence — never left the band."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["homeostasis_stable_5"], "IIIII")
        assert acc is False

    def test_fewer_than_5_i_after_o_not_accepted(self, nerode_dsn, dfas):
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["homeostasis_stable_5"], "OIIII")
        assert acc is False

    def test_five_i_after_o_accepted(self, nerode_dsn, dfas):
        """Boundary: O then exactly 5 I's."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["homeostasis_stable_5"], "OIIIII")
        assert acc is True

    def test_multiple_o_then_five_i_accepted(self, nerode_dsn, dfas):
        """Multiple O's before the I-run still satisfy O+I{5,}."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["homeostasis_stable_5"], "OOOIIIII")
        assert acc is True

    def test_oscillation_then_convergence_accepted(self, nerode_dsn, dfas):
        """O-run, partial I, new O-run, then 5 I's — confirms re-excursion path."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["homeostasis_stable_5"], "OOIIIOIIIII")
        assert acc is True

    def test_new_excursion_after_convergence_not_accepted(self, nerode_dsn, dfas):
        """After reaching converged state, a new O resets the I-counter."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["homeostasis_stable_5"], "OIIIIIOI")
        assert acc is False

    def test_more_i_after_convergence_stays_accepted(self, nerode_dsn, dfas):
        """State 6 self-loops on I — extra I's after convergence are fine."""
        with _fresh(nerode_dsn) as c:
            _, acc = _rts(c, dfas["homeostasis_stable_5"], "OIIIIIIII")
        assert acc is True


# ---------------------------------------------------------------------------
# TestCyberneticLog   log_cybernetic / scan_cybernetic
# ---------------------------------------------------------------------------

class TestCyberneticLog:
    """Infrastructure tests for the log + scan pipeline."""

    def test_log_inserts_rows(self, conn):
        sid = "test-log-insert-01"
        for sym in ["U", "U", "D"]:
            conn.execute(
                "SELECT nerode.log_cybernetic(%s, 'metric', %s)", (sid, sym)
            )
        count = conn.execute(
            "SELECT COUNT(*) FROM nerode.cybernetic_log "
            "WHERE session_id = %s AND alphabet = 'metric'",
            (sid,),
        ).fetchone()[0]
        assert count == 3

    def test_seq_increments_per_symbol(self, conn):
        sid = "test-log-seq-01"
        for sym in ["O", "I", "O"]:
            conn.execute(
                "SELECT nerode.log_cybernetic(%s, 'homeostasis', %s)", (sid, sym)
            )
        rows = conn.execute(
            "SELECT seq, symbol FROM nerode.cybernetic_log "
            "WHERE session_id = %s AND alphabet = 'homeostasis' ORDER BY seq",
            (sid,),
        ).fetchall()
        assert [r[0] for r in rows] == [1, 2, 3]
        assert [r[1] for r in rows] == ["O", "I", "O"]

    def test_seq_is_independent_per_alphabet(self, conn):
        """metric and control seqs are counted separately for the same session."""
        sid = "test-log-alpha-01"
        conn.execute("SELECT nerode.log_cybernetic(%s, 'metric',  'U')", (sid,))
        conn.execute("SELECT nerode.log_cybernetic(%s, 'metric',  'D')", (sid,))
        conn.execute("SELECT nerode.log_cybernetic(%s, 'control', 'A')", (sid,))

        metric_max = conn.execute(
            "SELECT MAX(seq) FROM nerode.cybernetic_log "
            "WHERE session_id = %s AND alphabet = 'metric'",
            (sid,),
        ).fetchone()[0]
        control_max = conn.execute(
            "SELECT MAX(seq) FROM nerode.cybernetic_log "
            "WHERE session_id = %s AND alphabet = 'control'",
            (sid,),
        ).fetchone()[0]
        assert metric_max == 2
        assert control_max == 1

    def test_scan_cybernetic_runs_without_error_on_match(self, conn):
        """Feeding UUU should trigger metric_rise_3 — scan must not raise."""
        sid = "test-scan-match-01"
        for sym in ["U", "U", "U"]:
            conn.execute(
                "SELECT nerode.log_cybernetic(%s, 'metric', %s)", (sid, sym)
            )

    def test_scan_cybernetic_runs_without_error_on_no_match(self, conn):
        sid = "test-scan-nomatch-01"
        for sym in ["U", "D", "U"]:
            conn.execute(
                "SELECT nerode.log_cybernetic(%s, 'metric', %s)", (sid, sym)
            )

    def test_log_cybernetic_match_confirmed_by_run(self, nerode_dsn, dfas, conn):
        """After logging UUU, run() on the stored input confirms metric_rise_3 accepts."""
        sid = "test-log-run-confirm-01"
        for sym in ["U", "U", "U"]:
            conn.execute(
                "SELECT nerode.log_cybernetic(%s, 'metric', %s)", (sid, sym)
            )
        input_str = conn.execute(
            "SELECT string_agg(symbol, '' ORDER BY seq) "
            "FROM nerode.cybernetic_log "
            "WHERE session_id = %s AND alphabet = 'metric'",
            (sid,),
        ).fetchone()[0]
        accept = conn.execute(
            "SELECT accept FROM nerode.run(%s, %s, FALSE)",
            (dfas["metric_rise_3"], input_str),
        ).fetchone()[0]
        assert accept is True
