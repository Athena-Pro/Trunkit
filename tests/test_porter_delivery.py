"""Live delivery regressions against explicit, disposable test databases."""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

import psycopg
import pytest

from calx.ledger import (
    CLAIM_DIGEST_KIND,
    certificate_row_hash,
    claim_digest,
    verify_chain,
    witness_row_hash,
)
from calx.proposition import witness_subject_ref
from nerode.precache import Precacher


def _session():
    return f"porter-delivery-{uuid.uuid4().hex}"


@pytest.fixture
def carry_conn(nerode_dsn, apply_schema_once):
    assert apply_schema_once, "Nerode test schema could not be applied"
    with psycopg.connect(nerode_dsn, connect_timeout=3) as conn:
        try:
            yield conn
        finally:
            conn.rollback()


def _close_ledger(conn, source, *, schema_type="reservation"):
    conn.execute(
        "SELECT nerode.ledger_absorb(%s, 'reservation', %s::jsonb, %s)",
        (source, json.dumps({"permitted": True}), schema_type),
    )
    return conn.execute("SELECT nerode.close_with_ledger(%s)", (source,)).fetchone()[0]


@pytest.mark.parametrize("carried_type,target_type", [("reservation", "order"), (None, "order")])
def test_matching_values_do_not_overwrite_incompatible_target_types(
    carry_conn, carried_type, target_type,
):
    conn, source, target = carry_conn, _session(), _session()
    envelope = _close_ledger(conn, source, schema_type=carried_type)
    conn.execute(
        "SELECT nerode.ledger_absorb(%s, 'reservation', %s::jsonb, %s)",
        (target, json.dumps({"permitted": True}), target_type),
    )
    result = conn.execute(
        "SELECT nerode.open_and_gate(%s::jsonb, %s, 'read')", (json.dumps(envelope), target),
    ).fetchone()[0]
    assert result["context"]["ledger_valid"] is False
    assert result["decision"]["verdict"] == "REVISE"
    assert conn.execute(
        "SELECT schema_type FROM nerode.ledger_state WHERE session_id = %s", (target,),
    ).fetchone()[0] == target_type
    assert conn.execute(
        "SELECT count(*) FROM nerode.session_log WHERE session_id = %s", (target,),
    ).fetchone()[0] == 0


def test_exact_typed_target_roundtrips_and_replays_snapshot(carry_conn):
    conn, source, target = carry_conn, _session(), _session()
    envelope = _close_ledger(conn, source)
    conn.execute(
        "SELECT nerode.ledger_unpack(%s, %s::jsonb)", (target, json.dumps(envelope["ledger"])),
    )
    for _ in range(2):
        result = conn.execute(
            "SELECT nerode.open_with_ledger(%s::jsonb, %s)", (json.dumps(envelope), target),
        ).fetchone()[0]
        assert result["ledger_valid"] is True
        assert result["ledger_restored"] == 1
        assert result["ledger"] == {"reservation": {"permitted": True}}
    assert conn.execute(
        "SELECT nerode.verify_carried_ledger(%s::jsonb), nerode.replay_ledger(%s::jsonb)",
        (json.dumps(envelope), json.dumps(envelope["ledger"])),
    ).fetchone() == (True, True)
    forged = copy.deepcopy(envelope)
    forged["ledger"]["entries"][0]["schema_type"] = "untrusted-type"
    assert conn.execute(
        "SELECT nerode.verify_carried_ledger(%s::jsonb)", (json.dumps(forged),),
    ).fetchone()[0] is False


def test_certifying_snapshot_serializes_concurrent_source_updates(carry_conn, nerode_dsn):
    source = _session()
    # Commit the initial value on a different connection: the certifying
    # connection must acquire its own lock, not inherit ledger_absorb's lock.
    with psycopg.connect(nerode_dsn, connect_timeout=3) as setup:
        setup.execute(
            "SELECT nerode.ledger_absorb(%s, 'reservation', %s::jsonb)",
            (source, json.dumps({"permitted": True})),
        )
    carry_conn.execute("SELECT nerode.certify_ledger(%s)", (source,))
    with (
        psycopg.connect(nerode_dsn, connect_timeout=3) as concurrent,
        pytest.raises(psycopg.errors.LockNotAvailable),
        concurrent.transaction(),
    ):
        concurrent.execute("SET LOCAL lock_timeout = '100ms'")
        concurrent.execute(
            "SELECT nerode.ledger_absorb(%s, 'reservation', %s::jsonb)",
            (source, json.dumps({"permitted": False})),
        )


def test_precacher_close_open_has_stored_certificate_and_roundtrip_evidence(
    nerode_dsn, apply_schema_once,
):
    assert apply_schema_once
    source, target, key = _session(), _session(), _session()
    pc = Precacher(source, dsn=nerode_dsn)
    pc.connect()
    try:
        pc.store(key, {"source": "live-test", "count": 3})
        envelope = pc.close(attention_hint="carry cached result")
        context = pc.open_for(target)
        assert context["prior_session"]["cert_valid"] is True
        assert context["resolved"][key] == {"source": "live-test", "count": 3}
        conn = pc._conn
        certificate, witness = conn.execute(
            "SELECT ce.status, w.body FROM cert.certificate ce "
            "JOIN cert.witness w ON w.certificate_id = ce.id "
            "WHERE ce.id = %s AND ce.claim_id = %s AND w.kind = 'trace'",
            (envelope["cert_certificate_id"], envelope["cert_bundle_id"]),
        ).fetchone()
        assert certificate == "valid"
        assert witness["cache_keys"] == [key]
        assert witness["attention_hint"] == "carry cached result"
        ok, evidence = conn.execute("SELECT * FROM nerode.verify_roundtrip()").fetchone()
        assert ok is True
        assert evidence["total_sessions_checked"] >= 1
        assert evidence["sessions_still_valid"] == evidence["total_sessions_checked"]
    finally:
        pc.disconnect()


@pytest.fixture
def bundle_conn(calx_dsn):
    with psycopg.connect(calx_dsn, connect_timeout=3) as conn:
        assert conn.execute(
            "SELECT to_regprocedure('cert.export_bundle(bigint[])')",
        ).fetchone()[0], "apply unified schema to the dedicated test database"
        try:
            yield conn
        finally:
            conn.rollback()


@pytest.mark.parametrize("claim_ids", [[], [-1], None])
def test_empty_export_selection_has_json_array(bundle_conn, claim_ids):
    bundle = bundle_conn.execute(
        "SELECT cert.export_bundle(%s::bigint[])", (claim_ids,),
    ).fetchone()[0]
    assert bundle["claims"] == []


def test_shipped_export_retains_stored_hashes_when_ledger_columns_exist(bundle_conn):
    conn = bundle_conn
    # Model the optional extension's stored columns inside this rollback-only
    # transaction. CI need not have any ignored local SQL files installed.
    for table, columns in (
        ("cert.certificate", [("row_hash", "text"), ("prev_hash", "text"),
                              ("premise_hashes", "text[]")]),
        ("cert.witness", [("row_hash", "text")]),
        ("curry.inferences", [("row_hash", "text")]),
    ):
        for column, kind in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {kind}")
    # Reapply the SHIPPED exporter after the extension columns exist. It must
    # preserve stored commitments rather than replace them with v1 metadata.
    lifecycle = Path(__file__).resolve().parents[1] / "src/calx/sql/100_cert_lifecycle.sql"
    conn.execute(lifecycle.read_text(encoding="utf-8"))
    body = {"schema": "factorization", "n": 12, "factors": [[2, 2], [3, 1]]}
    claim = conn.execute(
        "INSERT INTO cert.claim AS cl (subject_kind, subject_ref, statement, claim_kind, method) "
        "VALUES ('porter_export_test', %s::jsonb, %s, 'computational', 'comp_sql') "
        "RETURNING to_jsonb(cl)",
        (json.dumps(witness_subject_ref(body)), _session()),
    ).fetchone()[0]
    valid_under = {"claim_digest_kind": CLAIM_DIGEST_KIND, "claim_digest": claim_digest(claim)}
    stored_hash = certificate_row_hash(claim["id"], 1, "valid", {}, valid_under, None, None, [])
    cert_id = conn.execute(
        "INSERT INTO cert.certificate "
        "(claim_id, seq, status, valid_under, row_hash, premise_hashes) "
        "VALUES (%s, 1, 'valid', %s::jsonb, %s, ARRAY[]::text[]) RETURNING id",
        (claim["id"], json.dumps(valid_under), stored_hash),
    ).fetchone()[0]
    # A real installed ledger trigger may replace the supplied hash with its
    # predecessor-bound value. Use that exact stored value for the witness.
    stored_hash = conn.execute(
        "SELECT row_hash FROM cert.certificate WHERE id = %s", (cert_id,),
    ).fetchone()[0]
    stored_witness_hash = witness_row_hash(cert_id, stored_hash, "term", body)
    conn.execute(
        "INSERT INTO cert.witness (certificate_id, kind, body, row_hash) "
        "VALUES (%s, 'term', %s::jsonb, %s)",
        (cert_id, json.dumps(body), stored_witness_hash),
    )
    bundle = conn.execute("SELECT cert.export_bundle(ARRAY[%s])", (claim["id"],)).fetchone()[0]
    entry, = bundle["claims"]
    assert bundle["trunk_bundle_version"] == 2
    assert bundle["ledger_root"] == stored_hash
    assert entry["certificate"]["cert_id"] == cert_id
    assert entry["certificate"]["row_hash"] == stored_hash
    assert entry["certificate_history"][0]["row_hash"] == stored_hash
    assert entry["witness"]["row_hash"] == stored_witness_hash
    checked, = verify_chain(bundle)
    assert checked["content_ok"] and checked["witness_ok"]
    forged = copy.deepcopy(bundle)
    forged["claims"][0]["certificate"]["evidence"] = {"forged": True}
    assert verify_chain(forged)[0]["content_ok"] is False