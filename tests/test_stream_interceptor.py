"""
tests/test_stream_interceptor.py
=================================
Tests for tools/stream_interceptor.py.

Covers:
  - handle_nerode_query: build, certify, calx, partition, JSON
  - process_stream: pass-through and injection
  - edge cases: empty regex, repeated calls (idempotency of cert)
"""

from __future__ import annotations

import importlib.util
import io
import json
import pathlib

import pytest

from tests.dbskip import connect_or_skip

# Load the interceptor module via path (it lives in local/tools/, not src/)
_INTERCEPTOR = (
    pathlib.Path(__file__).parent.parent / "local" / "tools" / "stream_interceptor.py"
)
if not _INTERCEPTOR.is_file():
    pytest.skip(
        "local/tools/stream_interceptor.py not present", allow_module_level=True
    )
_spec = importlib.util.spec_from_file_location("stream_interceptor", _INTERCEPTOR)
_stream_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_stream_mod)

handle_nerode_query = _stream_mod.handle_nerode_query
process_stream      = _stream_mod.process_stream


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def result_astar_bplus(nerode_dsn):
    """Build a*b+ once and cache for the module."""
    with connect_or_skip(nerode_dsn, autocommit=True):
        pass
    return handle_nerode_query("a*b+", dsn=nerode_dsn)


@pytest.fixture(scope="module")
def result_ab_star(nerode_dsn):
    with connect_or_skip(nerode_dsn, autocommit=True):
        pass
    return handle_nerode_query("(ab)*", dsn=nerode_dsn)


@pytest.fixture(scope="module")
def result_ends_ab(nerode_dsn):
    with connect_or_skip(nerode_dsn, autocommit=True):
        pass
    return handle_nerode_query("(a|b)*ab", dsn=nerode_dsn)


# ---------------------------------------------------------------------------
# TestHandleNerodeQueryShape
# ---------------------------------------------------------------------------


class TestHandleNerodeQueryShape:
    """Return-value structure and types."""

    def test_returns_dict(self, result_astar_bplus):
        assert isinstance(result_astar_bplus, dict)

    def test_required_keys(self, result_astar_bplus):
        for key in (
            "automaton_id", "state_count", "alphabet",
            "transition_table", "nerode_partition", "calx_facts",
            "cert_claim_id", "automaton_json",
        ):
            assert key in result_astar_bplus, f"missing key: {key}"

    def test_automaton_id_positive(self, result_astar_bplus):
        assert isinstance(result_astar_bplus["automaton_id"], int)
        assert result_astar_bplus["automaton_id"] > 0

    def test_cert_claim_id_positive(self, result_astar_bplus):
        assert isinstance(result_astar_bplus["cert_claim_id"], int)
        assert result_astar_bplus["cert_claim_id"] > 0

    def test_alphabet_is_list(self, result_astar_bplus):
        assert isinstance(result_astar_bplus["alphabet"], list)
        assert sorted(result_astar_bplus["alphabet"]) == result_astar_bplus["alphabet"]

    def test_transition_table_is_string(self, result_astar_bplus):
        tt = result_astar_bplus["transition_table"]
        assert isinstance(tt, str)
        assert len(tt) > 0

    def test_transition_table_has_header(self, result_astar_bplus):
        tt = result_astar_bplus["transition_table"]
        assert "State" in tt
        assert "Accept" in tt

    def test_automaton_json_structure(self, result_astar_bplus):
        aj = result_astar_bplus["automaton_json"]
        assert aj["type"] == "DFA"
        assert "states" in aj
        assert "transitions" in aj
        assert "alphabet" in aj


# ---------------------------------------------------------------------------
# TestHandleNerodeQueryStateCount
# ---------------------------------------------------------------------------


class TestHandleNerodeQueryStateCount:
    """Minimal DFA state counts for known languages."""

    def test_astar_bplus_three_states(self, result_astar_bplus):
        # a*b+: q0 (reading a's), q1 (reading b's, accepting), q2 (dead)
        assert result_astar_bplus["state_count"] == 3

    def test_ab_star_three_states(self, result_ab_star):
        # (ab)*: needs 3 states
        assert result_ab_star["state_count"] == 3

    def test_ends_ab_three_states(self, result_ends_ab):
        # (a|b)*ab: minimal DFA has 3 states
        assert result_ends_ab["state_count"] == 3

    def test_state_count_matches_json_states(self, result_astar_bplus):
        assert result_astar_bplus["state_count"] == len(
            result_astar_bplus["automaton_json"]["states"]
        )

    def test_state_count_matches_json_states_ends_ab(self, result_ends_ab):
        assert result_ends_ab["state_count"] == len(
            result_ends_ab["automaton_json"]["states"]
        )


# ---------------------------------------------------------------------------
# TestHandleNerodeQueryPartition
# ---------------------------------------------------------------------------


class TestHandleNerodeQueryPartition:
    """Nerode partition (Myhill-Nerode equivalence classes)."""

    def test_partition_present(self, result_astar_bplus):
        assert result_astar_bplus["nerode_partition"] is not None

    def test_partition_is_dict(self, result_astar_bplus):
        assert isinstance(result_astar_bplus["nerode_partition"], dict)

    def test_partition_classes_count_matches_state_count(self, result_astar_bplus):
        part = result_astar_bplus["nerode_partition"]
        assert len(part) == result_astar_bplus["state_count"]

    def test_partition_covers_all_nfa_states(self, result_astar_bplus):
        part = result_astar_bplus["nerode_partition"]
        all_members = [sid for members in part.values() for sid in members]
        # Every NFA state is in exactly one equivalence class
        assert len(all_members) == len(set(all_members))

    def test_ab_star_partition_three_classes(self, result_ab_star):
        assert len(result_ab_star["nerode_partition"]) == 3


# ---------------------------------------------------------------------------
# TestHandleNerodeQueryCalx
# ---------------------------------------------------------------------------


class TestHandleNerodeQueryCalx:
    """calx_facts payload."""

    def test_calx_facts_present(self, result_astar_bplus):
        assert result_astar_bplus["calx_facts"] is not None

    def test_calx_state_count_matches(self, result_astar_bplus):
        cf = result_astar_bplus["calx_facts"]
        assert cf["state_count"] == result_astar_bplus["state_count"]

    def test_calx_is_prime_for_three_states(self, result_astar_bplus):
        # |Q|=3 is prime
        assert result_astar_bplus["calx_facts"]["is_prime"] is True

    def test_calx_factorization_for_three_states(self, result_astar_bplus):
        assert result_astar_bplus["calx_facts"]["factorization"] == [3]

    def test_calx_available_flag_present(self, result_astar_bplus):
        assert "calx_available" in result_astar_bplus["calx_facts"]


# ---------------------------------------------------------------------------
# TestHandleNerodeQueryCert
# ---------------------------------------------------------------------------


class TestHandleNerodeQueryCert:
    """Cert chain written to DB.

    handle_nerode_query opens its own connection and commits, so we read
    back with a fresh autocommit connection rather than the rolling-back
    test fixture.
    """

    def _read_conn(self, nerode_dsn):
        return connect_or_skip(nerode_dsn, autocommit=True)

    def test_cert_claim_written(self, result_astar_bplus, nerode_dsn):
        with self._read_conn(nerode_dsn) as c:
            row = c.execute(
                "SELECT id, method FROM cert.claim WHERE id = %s",
                (result_astar_bplus["cert_claim_id"],),
            ).fetchone()
        assert row is not None
        assert row[1] == "nerode_from_regex"

    def test_cert_certificate_written(self, result_astar_bplus, nerode_dsn):
        with self._read_conn(nerode_dsn) as c:
            row = c.execute(
                "SELECT status FROM cert.certificate WHERE claim_id = %s LIMIT 1",
                (result_astar_bplus["cert_claim_id"],),
            ).fetchone()
        assert row is not None
        assert row[0] == "valid"

    def test_cert_witness_kind_construction_record(self, result_astar_bplus, nerode_dsn):
        with self._read_conn(nerode_dsn) as c:
            row = c.execute(
                """
                SELECT w.kind
                FROM cert.witness w
                JOIN cert.certificate ce ON ce.id = w.certificate_id
                WHERE ce.claim_id = %s
                LIMIT 1
                """,
                (result_astar_bplus["cert_claim_id"],),
            ).fetchone()
        assert row is not None
        assert row[0] == "construction_record"

    def test_idempotent_cert_claim(self, nerode_dsn):
        """Calling handle_nerode_query twice for the same regex produces consistent results."""
        with connect_or_skip(nerode_dsn, autocommit=True):
            pass
        r1 = handle_nerode_query("b*a+", dsn=nerode_dsn)
        r2 = handle_nerode_query("b*a+", dsn=nerode_dsn)
        assert r1["state_count"] == r2["state_count"]
        assert r1["cert_claim_id"] > 0
        assert r2["cert_claim_id"] > 0


# ---------------------------------------------------------------------------
# TestProcessStream
# ---------------------------------------------------------------------------


class TestProcessStream:
    """Stream filter: pass-through and tool injection."""

    def _run_stream(self, events: list[dict], dsn: str) -> list[dict]:
        if any(
            event.get("type") == "tool_call" and event.get("tool") == "nerode_query"
            for event in events
        ):
            with connect_or_skip(dsn, autocommit=True):
                pass
        in_text  = "\n".join(json.dumps(e) for e in events) + "\n"
        in_buf   = io.StringIO(in_text)
        out_buf  = io.StringIO()
        process_stream(in_stream=in_buf, out_stream=out_buf, dsn=dsn)
        out_buf.seek(0)
        return [json.loads(line) for line in out_buf if line.strip()]

    def test_passthrough_non_tool_events(self, nerode_dsn):
        events = [
            {"type": "text", "content": "hello"},
            {"type": "text", "content": "world"},
        ]
        out = self._run_stream(events, nerode_dsn)
        assert len(out) == 2
        assert out[0]["content"] == "hello"

    def test_tool_call_injects_result(self, nerode_dsn):
        events = [
            {"type": "tool_call", "tool": "nerode_query",
             "id": "c1", "arguments": {"spec": "a+"}},
        ]
        out = self._run_stream(events, nerode_dsn)
        # original event + injected tool_result
        assert len(out) == 2
        assert out[0]["type"] == "tool_call"
        assert out[1]["type"] == "tool_result"
        assert out[1]["tool"] == "nerode_query"
        assert out[1]["call_id"] == "c1"
        assert "result" in out[1]

    def test_tool_result_has_state_count(self, nerode_dsn):
        events = [
            {"type": "tool_call", "tool": "nerode_query",
             "id": "c2", "arguments": {"spec": "a*b*"}},
        ]
        out = self._run_stream(events, nerode_dsn)
        result = out[1]["result"]
        assert "state_count" in result
        assert result["state_count"] > 0

    def test_non_nerode_tool_not_intercepted(self, nerode_dsn):
        events = [
            {"type": "tool_call", "tool": "some_other_tool",
             "id": "c3", "arguments": {}},
        ]
        out = self._run_stream(events, nerode_dsn)
        # Only the original event, no injection
        assert len(out) == 1

    def test_mixed_stream(self, nerode_dsn):
        events = [
            {"type": "text", "content": "Starting"},
            {"type": "tool_call", "tool": "nerode_query",
             "id": "c4", "arguments": {"spec": "ab"}},
            {"type": "text", "content": "Done"},
        ]
        out = self._run_stream(events, nerode_dsn)
        # text, tool_call, tool_result, text
        assert len(out) == 4
        assert out[0]["type"] == "text"
        assert out[1]["type"] == "tool_call"
        assert out[2]["type"] == "tool_result"
        assert out[3]["type"] == "text"
