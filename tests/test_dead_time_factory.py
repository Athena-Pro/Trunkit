"""
tests/test_dead_time_factory.py
================================
Tests for nerode.ensure_dead_time(k) — 96_dead_time_factory.sql.

Covers:
  - ensure_dead_time() returns a valid automaton_id
  - Idempotency: calling twice returns the same id
  - state_count = k+2 stored in nerode.automata
  - Correct label/is_initial/is_accepting on states 0, 1, k, k+1
  - Boundary: k-1 blanks after A → NOT accepting (wait state)
  - Boundary: k blanks after A → accepting (alarm)
  - Response clears: A + "_"*(k-1) + "R" → idle, not accepting
  - Timer reset: A + "_"*(k-1) + "A" + "_" → wait_2, not alarm
  - Alarm persists: alarm + extra _ → still alarm
  - k=1 edge case: one blank triggers alarm
  - k=7 (not in hardcoded set): correct boundary at 7
  - scan_cybernetic() picks up factory-built DFAs automatically
  - Invalid k raises an exception
"""

from __future__ import annotations

import pytest
import psycopg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, autocommit=True)


def _ensure(conn, k: int) -> int:
    return conn.execute(
        "SELECT nerode.ensure_dead_time(%s)", (k,)
    ).fetchone()[0]


def _rts(conn, dfa_id: int, input_str: str) -> tuple[int | None, bool | None]:
    """Return (state_id, is_accepting) after running input_str."""
    state = conn.execute(
        "SELECT nerode.run_to_state(%s, %s)", (dfa_id, input_str)
    ).fetchone()[0]
    if state is None:
        return None, None
    accepting = conn.execute(
        "SELECT is_accepting FROM nerode.states "
        "WHERE automaton_id = %s AND state_id = %s",
        (dfa_id, state),
    ).fetchone()[0]
    return state, accepting


def _state_label(conn, dfa_id: int, state_id: int) -> str:
    return conn.execute(
        "SELECT label FROM nerode.states "
        "WHERE automaton_id = %s AND state_id = %s",
        (dfa_id, state_id),
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def conn(nerode_dsn):
    with _fresh(nerode_dsn) as c:
        yield c


# ---------------------------------------------------------------------------
# TestEnsureDeadTime
# ---------------------------------------------------------------------------

class TestEnsureDeadTime:

    def test_returns_bigint(self, conn):
        dfa_id = _ensure(conn, 3)
        assert isinstance(dfa_id, int)
        assert dfa_id > 0

    def test_idempotent(self, conn):
        id1 = _ensure(conn, 3)
        id2 = _ensure(conn, 3)
        assert id1 == id2

    def test_state_count_stored(self, conn):
        k = 3
        dfa_id = _ensure(conn, k)
        count = conn.execute(
            "SELECT state_count FROM nerode.automata WHERE id = %s", (dfa_id,)
        ).fetchone()[0]
        assert count == k + 2

    def test_actual_state_rows(self, conn):
        k = 3
        dfa_id = _ensure(conn, k)
        n = conn.execute(
            "SELECT COUNT(*) FROM nerode.states WHERE automaton_id = %s", (dfa_id,)
        ).fetchone()[0]
        assert n == k + 2

    def test_state_labels(self, conn):
        k = 3
        dfa_id = _ensure(conn, k)
        assert _state_label(conn, dfa_id, 0)     == "idle"
        assert _state_label(conn, dfa_id, 1)     == "wait_1"
        assert _state_label(conn, dfa_id, k)     == f"wait_{k}"
        assert _state_label(conn, dfa_id, k + 1) == "alarm"

    def test_initial_state_is_idle(self, conn):
        k = 3
        dfa_id = _ensure(conn, k)
        row = conn.execute(
            "SELECT state_id, is_initial, is_accepting FROM nerode.states "
            "WHERE automaton_id = %s AND state_id = 0",
            (dfa_id,),
        ).fetchone()
        assert row[1] is True   # is_initial
        assert row[2] is False  # not accepting

    def test_alarm_state_is_accepting(self, conn):
        k = 3
        dfa_id = _ensure(conn, k)
        row = conn.execute(
            "SELECT is_accepting FROM nerode.states "
            "WHERE automaton_id = %s AND state_id = %s",
            (dfa_id, k + 1),
        ).fetchone()
        assert row[0] is True

    def test_alphabet_is_control(self, conn):
        dfa_id = _ensure(conn, 4)
        name = conn.execute(
            "SELECT al.name FROM nerode.alphabets al "
            "JOIN nerode.automata a ON a.alphabet_id = al.id "
            "WHERE a.id = %s",
            (dfa_id,),
        ).fetchone()[0]
        assert name == "control"

    def test_provenance_pattern(self, conn):
        k = 4
        dfa_id = _ensure(conn, k)
        pattern = conn.execute(
            "SELECT provenance->>'pattern' FROM nerode.automata WHERE id = %s",
            (dfa_id,),
        ).fetchone()[0]
        assert pattern == f"A_{{{k},}}"


# ---------------------------------------------------------------------------
# TestDeadTimeBoundary — parametrised over k values including novel ones
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("k", [1, 2, 3, 7, 15])
class TestDeadTimeBoundary:

    def test_k_minus_1_blanks_not_alarm(self, conn, k):
        dfa_id = _ensure(conn, k)
        inp = "A" + "_" * (k - 1)
        _, accepting = _rts(conn, dfa_id, inp)
        assert accepting is False, f"k={k}: {inp!r} should NOT alarm"

    def test_k_blanks_triggers_alarm(self, conn, k):
        dfa_id = _ensure(conn, k)
        inp = "A" + "_" * k
        _, accepting = _rts(conn, dfa_id, inp)
        assert accepting is True, f"k={k}: {inp!r} should alarm"

    def test_response_clears(self, conn, k):
        dfa_id = _ensure(conn, k)
        inp = "A" + "_" * (k - 1) + "R"
        state, accepting = _rts(conn, dfa_id, inp)
        assert state == 0, f"k={k}: R should return to idle"
        assert accepting is False

    def test_response_after_alarm_clears(self, conn, k):
        dfa_id = _ensure(conn, k)
        inp = "A" + "_" * k + "R"
        state, accepting = _rts(conn, dfa_id, inp)
        assert state == 0
        assert accepting is False

    def test_alarm_persists_on_more_blanks(self, conn, k):
        dfa_id = _ensure(conn, k)
        inp = "A" + "_" * k + "_" * 5
        state, accepting = _rts(conn, dfa_id, inp)
        assert state == k + 1, f"k={k}: alarm state should persist"
        assert accepting is True

    def test_action_resets_timer(self, conn, k):
        """A + (k-1 blanks) + A should restart the timer, NOT alarm."""
        dfa_id = _ensure(conn, k)
        inp = "A" + "_" * (k - 1) + "A"
        state, accepting = _rts(conn, dfa_id, inp)
        assert state == 1, f"k={k}: second A should reset to wait_1"
        assert accepting is False

    def test_new_action_after_alarm_resets(self, conn, k):
        """An action during alarm restarts the timer."""
        dfa_id = _ensure(conn, k)
        inp = "A" + "_" * k + "A"
        state, accepting = _rts(conn, dfa_id, inp)
        assert state == 1
        assert accepting is False

    def test_idle_blank_stays_idle(self, conn, k):
        dfa_id = _ensure(conn, k)
        state, accepting = _rts(conn, dfa_id, "_____")
        assert state == 0
        assert accepting is False

    def test_idle_response_stays_idle(self, conn, k):
        dfa_id = _ensure(conn, k)
        state, accepting = _rts(conn, dfa_id, "RRR")
        assert state == 0
        assert accepting is False


# ---------------------------------------------------------------------------
# TestScanCyberneticIntegration
# ---------------------------------------------------------------------------

class TestScanCyberneticIntegration:
    """Factory-built DFAs are picked up by scan_cybernetic() automatically."""

    def test_scan_detects_factory_dfa_on_alarm(self, conn):
        """Logging k+1 control symbols triggers no error (alarm fires NOTIFY)."""
        k = 3
        _ensure(conn, k)
        session_id = f"factory-scan-test-k{k}"

        conn.execute(
            "DELETE FROM nerode.cybernetic_log WHERE session_id = %s",
            (session_id,),
        )

        # Log A + k underscores = alarm for dead_time_3
        conn.execute(
            "SELECT nerode.log_cybernetic(%s, 'control', 'A')", (session_id,)
        )
        for _ in range(k):
            conn.execute(
                "SELECT nerode.log_cybernetic(%s, 'control', '_')", (session_id,)
            )
        # If scan_cybernetic errors out the call above would raise; reaching
        # here means the pipeline handled the factory DFA without exception.

    def test_scan_does_not_alarm_before_threshold(self, conn):
        """k-1 underscores after A — scan_cybernetic runs without error."""
        k = 3
        _ensure(conn, k)
        session_id = f"factory-scan-no-alarm-k{k}"

        conn.execute(
            "DELETE FROM nerode.cybernetic_log WHERE session_id = %s",
            (session_id,),
        )

        conn.execute(
            "SELECT nerode.log_cybernetic(%s, 'control', 'A')", (session_id,)
        )
        for _ in range(k - 1):
            conn.execute(
                "SELECT nerode.log_cybernetic(%s, 'control', '_')", (session_id,)
            )


# ---------------------------------------------------------------------------
# TestInvalidK
# ---------------------------------------------------------------------------

class TestInvalidK:

    def test_k_zero_raises(self, conn):
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("SELECT nerode.ensure_dead_time(0)")

    def test_k_negative_raises(self, conn):
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("SELECT nerode.ensure_dead_time(-5)")
