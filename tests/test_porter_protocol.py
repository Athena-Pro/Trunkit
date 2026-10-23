"""Porter v2 protocol checks; live tests use only an explicit disposable DSN."""

from __future__ import annotations

import copy
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import psycopg
import pytest

from nerode import porter
from nerode.db import SCHEMA_FILES


def test_protocol_migration_is_loaded():
    assert SCHEMA_FILES.index("E0_porter.sql") > SCHEMA_FILES.index("B4_carry.sql")


@pytest.mark.parametrize(
    "envelope",
    [
        {},
        {"handoff_v": 1},
        {"envelope_v": 1},
        {"envelope_v": 3},
        {"envelope_v": True},
        {"envelope_v": "2"},
        {"envelope_v": 2.0},
    ],
)
def test_legacy_and_unknown_versions_fail_explicitly_without_connecting(envelope, monkeypatch):
    monkeypatch.setattr(
        psycopg, "connect", lambda *a, **kw: pytest.fail("must reject before connecting")
    )
    with pytest.raises(porter.EnvelopeVersionError, match="v1 envelopes require"):
        porter.open(envelope)


def test_invalid_mode_rejected_before_connecting(monkeypatch):
    monkeypatch.setattr(
        psycopg, "connect", lambda *a, **kw: pytest.fail("must reject before connecting")
    )
    with pytest.raises(ValueError, match="mode"):
        porter.close("sender", mode="unknown")


def test_bad_ledger_entry_rejected_before_connecting(monkeypatch):
    monkeypatch.setattr(
        psycopg, "connect", lambda *a, **kw: pytest.fail("must reject before connecting")
    )
    with pytest.raises(ValueError, match="schema_type"):
        porter.close("sender", ledger_entries=[{"path": "task", "value": True}])


def test_read_only_open_query(monkeypatch):
    calls = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql, params=None):
            calls.append((sql, params))
            return self

        def fetchone(self):
            return ({"verification": "valid", "resolved": {}},)

    monkeypatch.setattr(psycopg, "connect", lambda *a, **kw: Conn())
    result = porter.open({"envelope_v": 2})
    assert result["resolved"] == {}
    assert calls[0][0] == "SET TRANSACTION READ ONLY"
    assert "porter_open" in calls[1][0]
    assert json.loads(calls[1][1][2]) == []


@pytest.fixture
def live_dsn(nerode_dsn, apply_schema_once):
    if not apply_schema_once:
        pytest.skip("disposable Nerode database unavailable")
    return nerode_dsn


def _session():
    return "porter-test-" + uuid.uuid4().hex


def _pack(dsn, **options):
    sender = _session()
    return porter.close(sender, dsn=dsn, payloads={sender + ":brief": {"answer": 42}}, **options)


def test_manifest_first_and_selected_payloads(live_dsn):
    envelope = _pack(live_dsn)
    context = porter.open(envelope, dsn=live_dsn)
    assert context["verification"] == "valid"
    assert context["prior_session"]["cert_valid"] is True
    assert context["ledger_valid"] is True
    assert context["resolved"] == {}
    manifest = context["payload_manifest"]
    assert len(manifest) == 1
    assert len(manifest[0]["sha256"]) == 64
    assert manifest[0]["bytes"] > 0
    selected = porter.open(envelope, dsn=live_dsn, payload_keys=[manifest[0]["key"]])
    assert selected["resolved"] == {manifest[0]["key"]: {"answer": 42}}
    assert selected == porter.open(envelope, dsn=live_dsn, payload_keys=[manifest[0]["key"]])


def test_payload_snapshot_survives_cache_update(live_dsn):
    envelope = _pack(live_dsn)
    key = envelope["payload_manifest"][0]["key"]
    with psycopg.connect(live_dsn) as conn:
        conn.execute(
            "UPDATE nerode.sequence_cache SET result=%s::jsonb WHERE seq_key=%s",
            ('{"answer": 999}', key),
        )
    context = porter.open(envelope, dsn=live_dsn, payload_keys=[key])
    assert context["verification"] == "valid"
    assert context["resolved"][key] == {"answer": 42}


@pytest.mark.parametrize(
    "field", ["attention_hint", "content_hash", "sender_session", "payload_manifest"]
)
def test_envelope_tampering_fails_closed(live_dsn, field):
    envelope = _pack(live_dsn)
    envelope[field] = [] if field == "payload_manifest" else "tampered"
    result = porter.open(envelope, dsn=live_dsn)
    assert result["verification"] == "refuted"
    assert result["resolved"] == {}


def test_self_consistent_rehash_does_not_forge_certificate(live_dsn):
    envelope = _pack(live_dsn)
    envelope["attention_hint"] = "forged"
    with psycopg.connect(live_dsn) as conn:
        body = {
            k: v
            for k, v in envelope.items()
            if k not in ("content_hash", "envelope_hash", "session")
        }
        envelope["content_hash"] = conn.execute(
            "SELECT nerode.porter_sha256(%s::jsonb)", (json.dumps(body),)
        ).fetchone()[0]
        unsigned = {k: v for k, v in envelope.items() if k != "envelope_hash"}
        envelope["envelope_hash"] = conn.execute(
            "SELECT nerode.porter_sha256(%s::jsonb)", (json.dumps(unsigned),)
        ).fetchone()[0]
    assert porter.open(envelope, dsn=live_dsn)["verification"] == "refuted"


def test_snapshot_is_append_only(live_dsn):
    envelope = _pack(live_dsn)
    with psycopg.connect(live_dsn) as conn:
        with pytest.raises(psycopg.Error, match="append-only"):
            conn.execute(
                "UPDATE nerode.porter_envelopes SET payloads=%s::jsonb WHERE envelope_hash=%s",
                ("{}", envelope["envelope_hash"]),
            )
        conn.rollback()


def test_closing_certificate_content_binding_is_checked(live_dsn):
    envelope = _pack(live_dsn)
    # A different stored certificate cannot authenticate this snapshot. The
    # test transaction rolls back its deliberate fault injection.
    with psycopg.connect(live_dsn) as conn:
        conn.execute(
            "UPDATE cert.certificate SET valid_under=valid_under #- '{trunkit,porter}' WHERE id=%s",
            (envelope["session"]["cert_certificate_id"],),
        )
        assert (
            conn.execute(
                "SELECT nerode.porter_verify(%s::jsonb)", (json.dumps(envelope),)
            ).fetchone()[0]
            is False
        )
        conn.rollback()


def test_payload_budget_never_returns_truncated_values(live_dsn):
    envelope = _pack(live_dsn)
    key = envelope["payload_manifest"][0]["key"]
    context = porter.open(envelope, dsn=live_dsn, payload_keys=[key], max_payload_bytes=1)
    assert context["verification"] == "valid"
    assert context["payload_budget_exceeded"] is True
    assert context["resolved"] == {}


def test_receiver_routing_and_unknown_payload_keys(live_dsn):
    envelope = _pack(live_dsn, intended_receiver="worker-one")
    assert porter.open(envelope, dsn=live_dsn)["verification"] == "refuted"
    assert (
        porter.open(envelope, dsn=live_dsn, receiver_session="worker-one")["verification"]
        == "valid"
    )
    with pytest.raises(psycopg.Error, match="absent"):
        porter.open(envelope, dsn=live_dsn, receiver_session="worker-one", payload_keys=["absent"])


def test_concurrent_claim_single_winner_release_and_stale_tokens(live_dsn):
    envelope = _pack(live_dsn, mode="claim_once")
    barrier = Barrier(2)

    def acquire(receiver):
        barrier.wait()
        return porter.claim(envelope, receiver, dsn=live_dsn)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(acquire, ["worker-one", "worker-two"]))
    winners = [r for r in results if r.get("claimed")]
    assert len(winners) == 1
    winner = winners[0]
    key = envelope["payload_manifest"][0]["key"]
    assert porter.open(envelope, dsn=live_dsn)["verification"] == "valid"
    assert porter.open(envelope, dsn=live_dsn, payload_keys=[key])["resolved"] == {}
    assert porter.open(
        envelope,
        dsn=live_dsn,
        receiver_session=winner["receiver_session"],
        claim_token=winner["claim_token"],
        payload_keys=[key],
    )["resolved"][key] == {"answer": 42}
    assert porter.release(envelope, "wrong-token", dsn=live_dsn)["released"] is False
    assert porter.release(envelope, winner["claim_token"], dsn=live_dsn)["released"] is True
    next_claim = porter.claim(envelope, "worker-three", dsn=live_dsn)
    assert next_claim["claimed"] is True
    assert next_claim["claim_token"] != winner["claim_token"]
    assert porter.release(envelope, winner["claim_token"], dsn=live_dsn)["released"] is False


def test_expired_lease_can_be_claimed_again(live_dsn):
    envelope = _pack(live_dsn, mode="claim_once")
    first = porter.claim(envelope, "worker-one", dsn=live_dsn)
    with psycopg.connect(live_dsn) as conn:
        conn.execute(
            "UPDATE nerode.porter_claims SET expires_at=clock_timestamp()-interval '1 second' "
            "WHERE envelope_hash=%s",
            (envelope["envelope_hash"],),
        )
    assert porter.release(envelope, first["claim_token"], dsn=live_dsn)["released"] is False
    assert porter.claim(envelope, "worker-two", dsn=live_dsn)["claimed"] is True


def test_close_budget_rolls_back_payloads_and_certificates(live_dsn):
    sender = _session()
    with pytest.raises(psycopg.Error, match="budget"):
        porter.close(sender, dsn=live_dsn, payloads={sender: 42}, max_envelope_bytes=1024)
    with psycopg.connect(live_dsn) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM nerode.sequence_cache WHERE seq_key=%s", (sender,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM cert.claim WHERE statement=%s",
                ("session:" + sender + ":closed",),
            ).fetchone()[0]
            == 0
        )


def test_health_reports_dead_time_from_prior_trace(live_dsn):
    sender = _session()
    with psycopg.connect(live_dsn) as conn:
        for symbol in "A_____":
            conn.execute("SELECT nerode.log_cybernetic(%s,%s,%s)", (sender, "control", symbol))
    envelope = porter.close(sender, dsn=live_dsn)
    warnings = porter.open(envelope, dsn=live_dsn)["health"]["warnings"]
    assert any(w["detector"] == "dead_time_5" for w in warnings)
    assert porter.open(_pack(live_dsn), dsn=live_dsn)["health"]["warnings"] == []


def test_gate_requires_claim_before_writing_receiver(live_dsn):
    envelope = _pack(live_dsn, mode="claim_once")
    receiver = _session()
    result = porter.open_and_gate(envelope, receiver, "read_file", dsn=live_dsn)
    assert result["decision"]["verdict"] == "REVISE"
    assert "claim token" in result["decision"]["feedback"]
    with psycopg.connect(live_dsn) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM nerode.session_log WHERE session_id=%s", (receiver,)
            ).fetchone()[0]
            == 0
        )


def test_atomic_payload_ledger_close_and_gate(live_dsn):
    sender = _session()
    envelope = porter.close(
        sender,
        dsn=live_dsn,
        payloads={sender: {"ok": True}},
        ledger_entries=[
            {"path": "task.objective", "value": "inspect safely", "schema_type": "string"}
        ],
    )
    assert porter.open(envelope, dsn=live_dsn)["ledger_valid"] is True
    result = porter.open_and_gate(envelope, _session(), "read_file", dsn=live_dsn)
    assert result["context"]["verification"] == "valid"
    assert result["context"]["resolved"] == {}
    assert result["context"]["ledger_restored"] == 1
    assert result["decision"]["verdict"] in ("ALLOW", "REVISE", "BLOCK")


def test_reclose_preserves_prior_payload_snapshot(live_dsn):
    sender = _session()
    first = porter.close(sender, dsn=live_dsn, payloads={sender: 1})
    second = porter.close(sender, dsn=live_dsn, payloads={sender: 2})
    assert first["envelope_hash"] != second["envelope_hash"]
    assert porter.open(first, dsn=live_dsn, payload_keys=[sender])["resolved"] == {sender: 1}
    assert porter.open(second, dsn=live_dsn, payload_keys=[sender])["resolved"] == {sender: 2}


def test_manifest_open_does_not_write_receiver_state(live_dsn):
    envelope = _pack(live_dsn)
    receiver = _session()
    assert porter.open(envelope, dsn=live_dsn, receiver_session=receiver)["verification"] == "valid"
    with psycopg.connect(live_dsn) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM nerode.session_log WHERE session_id=%s", (receiver,)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM nerode.ledger_state WHERE session_id=%s", (receiver,)
            ).fetchone()[0]
            == 0
        )


def test_certificate_and_session_splice_cannot_authenticate_snapshot(live_dsn):
    original = _pack(live_dsn)
    other = _pack(live_dsn)
    forged = copy.deepcopy(original)
    forged["session"] = other["session"]
    with psycopg.connect(live_dsn) as conn:
        forged.pop("envelope_hash")
        forged["envelope_hash"] = conn.execute(
            "SELECT nerode.porter_sha256(%s::jsonb)", (json.dumps(forged),)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO nerode.porter_envelopes(envelope_hash,envelope,payloads,certificate_id) "
            "SELECT %s,%s::jsonb,payloads,certificate_id FROM nerode.porter_envelopes "
            "WHERE envelope_hash=%s",
            (forged["envelope_hash"], json.dumps(forged), original["envelope_hash"]),
        )
        assert (
            conn.execute(
                "SELECT nerode.porter_verify(%s::jsonb)", (json.dumps(forged),)
            ).fetchone()[0]
            is False
        )
        conn.rollback()


def test_lease_clock_starts_after_waiting_for_row_lock(live_dsn):
    envelope = _pack(live_dsn, mode="claim_once")
    first = porter.claim(envelope, "worker-one", dsn=live_dsn)
    porter.release(envelope, first["claim_token"], dsn=live_dsn)
    application = "porter-lock-test-" + uuid.uuid4().hex
    waiting_dsn = psycopg.conninfo.make_conninfo(live_dsn, application_name=application)
    with psycopg.connect(live_dsn) as holder, ThreadPoolExecutor(max_workers=1) as pool:
        holder.execute(
            "SELECT 1 FROM nerode.porter_claims WHERE envelope_hash=%s FOR UPDATE",
            (envelope["envelope_hash"],),
        )
        future = pool.submit(porter.claim, envelope, "worker-two", dsn=waiting_dsn, lease_seconds=1)
        with psycopg.connect(live_dsn, autocommit=True) as observer:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                waiting = observer.execute(
                    "SELECT 1 FROM pg_stat_activity WHERE application_name=%s "
                    "AND wait_event_type='Lock'",
                    (application,),
                ).fetchone()
                if waiting:
                    break
                time.sleep(0.02)
            else:
                holder.rollback()
                pytest.fail("claim did not reach its row-lock wait")
            # Longer than the requested lease: timestamps evaluated before the
            # row-lock wait would grant an already-expired lease.
            time.sleep(1.1)
            holder.commit()
            second = future.result(timeout=5)
            assert second["claimed"] is True
            assert (
                observer.execute(
                    "SELECT expires_at>clock_timestamp() FROM nerode.porter_claims "
                    "WHERE envelope_hash=%s",
                    (envelope["envelope_hash"],),
                ).fetchone()[0]
                is True
            )


def test_gate_rechecks_lease_expiry_after_row_lock_wait(live_dsn):
    envelope = _pack(live_dsn, mode="claim_once")
    receiver = _session()
    acquired = porter.claim(envelope, receiver, dsn=live_dsn, lease_seconds=1)
    application = "porter-gate-lock-" + uuid.uuid4().hex
    waiting_dsn = psycopg.conninfo.make_conninfo(live_dsn, application_name=application)
    with psycopg.connect(live_dsn) as holder, ThreadPoolExecutor(max_workers=1) as pool:
        holder.execute(
            "SELECT 1 FROM nerode.porter_claims WHERE envelope_hash=%s FOR UPDATE",
            (envelope["envelope_hash"],),
        )
        future = pool.submit(
            porter.open_and_gate,
            envelope,
            receiver,
            "read_file",
            dsn=waiting_dsn,
            claim_token=acquired["claim_token"],
        )
        with psycopg.connect(live_dsn, autocommit=True) as observer:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                waiting = observer.execute(
                    "SELECT 1 FROM pg_stat_activity WHERE application_name=%s "
                    "AND wait_event_type='Lock'",
                    (application,),
                ).fetchone()
                if waiting:
                    break
                time.sleep(0.02)
            else:
                holder.rollback()
                pytest.fail("gate did not reach its lease row-lock wait")
            time.sleep(1.1)
            holder.commit()
            gated = future.result(timeout=5)
            assert gated["decision"]["verdict"] == "REVISE"
            assert "claim token" in gated["decision"]["feedback"]
            assert (
                observer.execute(
                    "SELECT count(*) FROM nerode.session_log WHERE session_id=%s", (receiver,)
                ).fetchone()[0]
                == 0
            )


def test_release_rechecks_expiry_after_row_lock_wait(live_dsn):
    envelope = _pack(live_dsn, mode="claim_once")
    acquired = porter.claim(envelope, _session(), dsn=live_dsn, lease_seconds=1)
    application = "porter-release-lock-" + uuid.uuid4().hex
    waiting_dsn = psycopg.conninfo.make_conninfo(live_dsn, application_name=application)
    with psycopg.connect(live_dsn) as holder, ThreadPoolExecutor(max_workers=1) as pool:
        holder.execute(
            "SELECT 1 FROM nerode.porter_claims WHERE envelope_hash=%s FOR UPDATE",
            (envelope["envelope_hash"],),
        )
        future = pool.submit(porter.release, envelope, acquired["claim_token"], dsn=waiting_dsn)
        with psycopg.connect(live_dsn, autocommit=True) as observer:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                waiting = observer.execute(
                    "SELECT 1 FROM pg_stat_activity WHERE application_name=%s "
                    "AND wait_event_type='Lock'",
                    (application,),
                ).fetchone()
                if waiting:
                    break
                time.sleep(0.02)
            else:
                holder.rollback()
                pytest.fail("release did not reach its row-lock wait")
            time.sleep(1.1)
            holder.commit()
            assert future.result(timeout=5)["released"] is False
