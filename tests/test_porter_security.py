"""Porter trust-boundary regressions. DB tests require a dedicated Nerode DSN."""

from __future__ import annotations

import copy
import json
import uuid

import psycopg
import pytest

from nerode.warden import Warden


@pytest.fixture
def porter_conn(nerode_dsn, apply_schema_once):
    # A schema error must fail these tests, rather than silently skipping the
    # certificate layers whose fresh-schema behavior they are meant to cover.
    assert apply_schema_once, "Nerode test schema could not be applied"
    with psycopg.connect(nerode_dsn, connect_timeout=3) as connection:
        try:
            yield connection
        finally:
            connection.rollback()


def _session():
    return f"porter-security-{uuid.uuid4().hex}"


def _policy(conn, tool, predicate="($1->'reservation'->>'permitted')::boolean"):
    return conn.execute(
        "SELECT nerode.register_policy(%s, %s, %s, 'reservation policy', 'block')",
        (tool, tool, predicate),
    ).fetchone()[0]


def _absorb(conn, session, value):
    conn.execute(
        "SELECT nerode.ledger_absorb(%s, 'reservation', %s::jsonb, 'reservation')",
        (session, json.dumps(value)),
    )


def _decision(conn, session, tool):
    return conn.execute(
        "SELECT verdict, feedback, witness, claim_id "
        "FROM nerode.certify_gate_decision(%s, %s)", (session, tool),
    ).fetchone()


def _envelope(conn):
    session = _session()
    _absorb(conn, session, {"permitted": True})
    return conn.execute("SELECT nerode.close_with_ledger(%s)", (session,)).fetchone()[0]


def _open_and_gate(conn, envelope, target, tool):
    return conn.execute(
        "SELECT nerode.open_and_gate(%s::jsonb, %s, %s)",
        (json.dumps(envelope), target, tool),
    ).fetchone()[0]


@pytest.mark.parametrize("value, verdict, status", [
    ({"permitted": True}, "ALLOW", "valid"),
    ({"permitted": False}, "BLOCK", "refuted"),
    ({}, "REVISE", "unverified"),
])
def test_gate_certifies_all_three_verdicts(porter_conn, value, verdict, status):
    conn, session, tool = porter_conn, _session(), _session()
    _policy(conn, tool)
    _absorb(conn, session, value)
    actual, _, witness, claim = _decision(conn, session, tool)
    assert actual == verdict
    stored_status, stored_witness = conn.execute(
        "SELECT ce.status, w.body FROM cert.certificate ce "
        "JOIN cert.witness w ON w.certificate_id = ce.id "
        "WHERE ce.claim_id = %s ORDER BY ce.seq DESC LIMIT 1", (claim,),
    ).fetchone()
    assert (stored_status, stored_witness) == (status, witness)
    assert conn.execute(
        "SELECT nerode.replay_gate(%s::jsonb)", (json.dumps(witness),),
    ).fetchone()[0]


def test_registered_tool_without_enabled_rules_requires_revision(porter_conn):
    conn, session, tool = porter_conn, _session(), _session()
    conn.execute("SELECT nerode.register_gated_tool(%s)", (tool,))
    assert _decision(conn, session, tool)[0] == "REVISE"
    rule = _policy(conn, tool, "TRUE")
    assert _decision(conn, session, tool)[0] == "ALLOW"
    conn.execute("UPDATE nerode.policy_rule SET enabled = FALSE WHERE id = %s", (rule,))
    assert _decision(conn, session, tool)[0] == "REVISE"
    assert _decision(conn, session, "unregistered-read")[0] == "ALLOW"


def test_policy_changes_preserve_replay_but_invalidate_execution(porter_conn):
    conn, session, tool = porter_conn, _session(), _session()
    _policy(conn, tool, "TRUE")
    _, _, witness, claim = _decision(conn, session, tool)
    encoded = json.dumps(witness)
    assert conn.execute(
        "SELECT nerode.gate_current(%s, %s::jsonb)", (session, encoded),
    ).fetchone()[0]
    _policy(conn, tool, "FALSE")
    replay, drift, current = conn.execute(
        "SELECT nerode.replay_gate(%s::jsonb), nerode.gate_drift(%s, %s::jsonb), "
        "nerode.gate_current(%s, %s::jsonb)",
        (encoded, session, encoded, session, encoded),
    ).fetchone()
    assert (replay, drift, current) == (True, True, False)
    new_verdict, _, _, new_claim = _decision(conn, session, tool)
    assert new_verdict == "BLOCK" and new_claim != claim


def test_policy_snapshots_are_immutable(porter_conn):
    conn = porter_conn
    _, _, witness, _ = _decision(conn, _session(), "read")
    for statement in (
        "UPDATE nerode.policy_snapshot SET body = body WHERE policy_hash = %s",
        "DELETE FROM nerode.policy_snapshot WHERE policy_hash = %s",
    ):
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"), conn.transaction():
            conn.execute(statement, (witness["policy_hash"],))


def test_execution_checks_ledger_drift_expiry_and_certificate_binding(porter_conn):
    conn, session = porter_conn, _session()
    _, _, witness, claim = _decision(conn, session, "read")
    forged = dict(witness, valid_until="2999-01-01T00:00:00+00:00")
    assert not conn.execute(
        "SELECT nerode.gate_current(%s, %s::jsonb)", (session, json.dumps(forged)),
    ).fetchone()[0]
    _absorb(conn, session, {"permitted": True})
    assert conn.execute(
        "SELECT nerode.gate_drift(%s, %s::jsonb)", (session, json.dumps(witness)),
    ).fetchone()[0]
    _, _, witness, claim = _decision(conn, session, "read")
    # An authentic stored decision with an elapsed validity window cannot run.
    expired = conn.execute(
        "UPDATE cert.witness w SET body = jsonb_set(w.body, '{valid_until}', "
        "to_jsonb(statement_timestamp() - interval '1 second')) "
        "FROM cert.certificate ce WHERE ce.id = w.certificate_id AND ce.claim_id = %s "
        "RETURNING w.body", (claim,),
    ).fetchone()[0]
    assert not conn.execute(
        "SELECT nerode.gate_current(%s, %s::jsonb)", (session, json.dumps(expired)),
    ).fetchone()[0]


def test_valid_certified_ledger_roundtrip_and_first_gate(porter_conn):
    conn, tool, target = porter_conn, _session(), _session()
    _policy(conn, tool)
    envelope = _envelope(conn)
    result = _open_and_gate(conn, envelope, target, tool)
    assert result["context"]["prior_session"]["cert_valid"] is True
    assert result["context"]["ledger_valid"] is True
    assert result["context"]["ledger"] == {"reservation": {"permitted": True}}
    assert result["decision"]["verdict"] == "ALLOW"
    # Both new witness families must be accepted on the fresh schema.
    kinds = conn.execute(
        "SELECT w.kind FROM cert.witness w JOIN cert.certificate ce ON ce.id = w.certificate_id "
        "WHERE ce.claim_id IN (%s, %s)",
        (envelope["ledger_claim_id"], result["decision"]["claim_id"]),
    ).fetchall()
    assert {row[0] for row in kinds} == {"ledger_snapshot", "gate_decision"}


@pytest.mark.parametrize("tamper", [
    "ledger", "ledger_and_hash", "ledger_claim", "session_claim", "cache_keys", "dfa", "hint",
    "ledger_certificate", "session_certificate", "missing_binding", "version",
])
def test_tampered_envelopes_never_restore_or_allow(porter_conn, tamper):
    conn, tool, target = porter_conn, _session(), _session()
    _policy(conn, tool)
    envelope = copy.deepcopy(_envelope(conn))
    if tamper in {"ledger", "ledger_and_hash"}:
        envelope["ledger"]["entries"][0]["value"]["permitted"] = False
        if tamper == "ledger_and_hash":
            forged_hash = conn.execute(
                "SELECT md5(%s::jsonb::text)",
                (json.dumps({"reservation": {"permitted": False}}),),
            ).fetchone()[0]
            envelope["ledger"]["ledger_hash"] = envelope["ledger_hash"] = forged_hash
    elif tamper == "ledger_claim":
        envelope["ledger_claim_id"] = envelope["cert_bundle_id"]
    elif tamper == "session_claim":
        envelope["cert_bundle_id"] = envelope["ledger_claim_id"]
    elif tamper == "ledger_certificate":
        envelope["ledger_certificate_id"] = envelope["cert_certificate_id"]
    elif tamper == "session_certificate":
        envelope["cert_certificate_id"] = envelope["ledger_certificate_id"]
    elif tamper == "missing_binding":
        del envelope["cert_certificate_id"]
    elif tamper == "version":
        envelope["handoff_v"] = 999
    elif tamper == "cache_keys":
        envelope["cache_keys"] = ["forged-key"]
    elif tamper == "dfa":
        envelope["session_dfa_states"]["session_calx_loop"] = 999999
    else:
        envelope["attention_hint"] = "forged instructions"
    result = _open_and_gate(conn, envelope, target, tool)
    assert result["context"]["ledger_valid"] is False
    assert result["decision"]["verdict"] == "REVISE"
    assert result["decision"]["claim_id"] is None
    assert conn.execute("SELECT nerode.ledger_render(%s)", (target,)).fetchone()[0] == {}
    assert conn.execute(
        "SELECT count(*) FROM nerode.session_log WHERE session_id = %s", (target,),
    ).fetchone()[0] == 0


def test_session_and_ledger_certificates_cannot_be_mixed_across_closes(porter_conn):
    conn, target = porter_conn, _session()
    envelope = _envelope(conn)
    old = copy.deepcopy(envelope)
    _absorb(conn, envelope["session_id"], {"permitted": False})
    newer = conn.execute(
        "SELECT nerode.close_with_ledger(%s)", (envelope["session_id"],),
    ).fetchone()[0]
    for key in (
        "ledger", "ledger_hash", "ledger_claim_id", "ledger_certificate_id", "ledger_binding",
    ):
        newer[key] = old[key]
    result = _open_and_gate(conn, newer, target, "read")
    assert result["decision"]["verdict"] == "REVISE"
    assert result["context"]["ledger_valid"] is False


def test_existing_target_ledger_is_not_overwritten(porter_conn):
    conn, target = porter_conn, _session()
    envelope = _envelope(conn)
    _absorb(conn, target, {"permitted": False})
    result = _open_and_gate(conn, envelope, target, "read")
    assert result["decision"]["verdict"] == "REVISE"
    assert conn.execute("SELECT nerode.ledger_render(%s)", (target,)).fetchone()[0] == {
        "reservation": {"permitted": False},
    }


def test_warden_returns_the_exact_certified_decision(nerode_dsn, apply_schema_once):
    assert apply_schema_once
    with Warden(_session(), dsn=nerode_dsn) as warden:
        decision = warden.gate("read")
        assert decision.allowed and warden.can_execute(decision)
        stored = warden._require().execute(
            "SELECT w.body FROM cert.witness w "
            "JOIN cert.certificate ce ON ce.id = w.certificate_id "
            "WHERE ce.claim_id = %s ORDER BY ce.seq DESC LIMIT 1", (decision.claim_id,),
        ).fetchone()[0]
        assert stored == decision.witness
        warden.absorb("reservation", {"permitted": True})
        assert warden.drifted(decision.witness) and not warden.can_execute(decision)
