"""Versioned Porter handoffs with immutable snapshots and explicit lease ownership.

The default open is read-only and manifest-first. Select ``payload_keys`` to
inline values in the same request, within ``max_payload_bytes``. Hashes use the
server's PostgreSQL JSONB text representation; they are not file signatures.
The existing stored session certificate, not a caller-supplied hash, binds each
snapshot. This module uses only the Nerode DSN; Trunk proof links stay by value.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Literal, TypedDict

import psycopg

from nerode.db import CONNECT_TIMEOUT, resolve_dsn

Mode = Literal["reopenable", "claim_once"]


class PayloadManifestEntry(TypedDict):
    key: str
    sha256: str
    bytes: int


class EnvelopeV2(TypedDict):
    envelope_v: Literal[2]
    sender_session: str
    intended_receiver: str | None
    created_at: str
    closed_at: str
    mode: Mode
    include_ledger: bool
    attention_hint: str
    payload_manifest: list[PayloadManifestEntry]
    health: dict[str, Any]
    content_hash: str
    envelope_hash: str
    session: dict[str, Any]


class EnvelopeVersionError(ValueError):
    """The caller supplied a legacy or unknown envelope version."""


def _require_v2(envelope: dict[str, Any]) -> None:
    if type(envelope.get("envelope_v")) is not int or envelope["envelope_v"] != 2:
        version = envelope.get("envelope_v", envelope.get("handoff_v", "missing"))
        raise EnvelopeVersionError(
            f"Unsupported Porter envelope version {version!r}; "
            "v1 envelopes require nerode.precache.Precacher.open()"
        )


def _query(sql: str, params: tuple[Any, ...], dsn: str | None, *, readonly: bool = False) -> dict:
    with psycopg.connect(dsn or resolve_dsn(), connect_timeout=CONNECT_TIMEOUT) as conn:
        if readonly:
            conn.execute("SET TRANSACTION READ ONLY")
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else {}


def close(
    session_id: str,
    *,
    dsn: str | None = None,
    intended_receiver: str | None = None,
    mode: Mode = "reopenable",
    attention_hint: str | None = None,
    cache_keys: Sequence[str] | None = None,
    max_envelope_bytes: int = 65536,
    include_ledger: bool = True,
    trunkit: dict[str, Any] | None = None,
    payloads: dict[str, Any] | None = None,
    ledger_entries: Sequence[dict[str, Any]] | None = None,
) -> EnvelopeV2:
    """Close, certify and snapshot a sender session atomically.

    ``intended_receiver`` is a routing constraint, not user authentication.
    Leave it unset for an open receiver. History is append-only: re-closing a
    session creates a new envelope and certificate, preserving prior snapshots.
    """
    if mode not in ("reopenable", "claim_once"):
        raise ValueError("mode must be 'reopenable' or 'claim_once'")
    options: dict[str, Any] = dict(
        intended_receiver=intended_receiver,
        mode=mode,
        max_envelope_bytes=max_envelope_bytes,
        include_ledger=include_ledger,
    )
    if attention_hint is not None:
        options["attention_hint"] = attention_hint
    if cache_keys is not None:
        options["cache_keys"] = list(cache_keys)
    if trunkit is not None:
        options["trunkit"] = trunkit
    encoded_options = json.dumps(options, allow_nan=False)
    encoded_payloads = [
        (key, json.dumps(value, allow_nan=False)) for key, value in sorted((payloads or {}).items())
    ]
    entries = list(ledger_entries or [])
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("path"), str)
            or not entry["path"]
            or "value" not in entry
            or not isinstance(entry.get("schema_type"), str)
        ):
            raise ValueError("ledger_entries require path, value and schema_type")
    with psycopg.connect(dsn or resolve_dsn(), connect_timeout=CONNECT_TIMEOUT) as conn:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            ("nerode.porter:" + session_id,),
        )
        for key, value in encoded_payloads:
            conn.execute(
                "INSERT INTO nerode.sequence_cache(seq_key, automaton_ids, length, mode, result) "
                "VALUES (%s, ARRAY[]::bigint[], 0, 'store', %s::jsonb) "
                "ON CONFLICT(seq_key) DO UPDATE SET result=EXCLUDED.result, built_at=now()",
                (key, value),
            )
            conn.execute("SELECT nerode.tag_cache_key(%s, %s)", (session_id, key))
        for entry in entries:
            conn.execute(
                "SELECT nerode.ledger_absorb(%s, %s, %s::jsonb, %s, NULL)",
                (
                    session_id,
                    entry["path"],
                    json.dumps(entry["value"], allow_nan=False),
                    entry["schema_type"],
                ),
            )
        row = conn.execute(
            "SELECT nerode.porter_close(%s, %s::jsonb)", (session_id, encoded_options)
        ).fetchone()
        return row[0] if row else {}  # type: ignore[return-value]


def open(
    envelope: dict[str, Any],
    *,
    dsn: str | None = None,
    receiver_session: str | None = None,
    payload_keys: Sequence[str] | None = None,
    max_payload_bytes: int = 65536,
    claim_token: str | None = None,
) -> dict[str, Any]:
    """Re-verify and inspect a handoff without writing a receipt or ledger.

    Returns ``verification`` (valid/refuted), ``prior_session.cert_valid``,
    ``payload_manifest``, ``health``, ``ledger_valid`` and ``resolved``. With
    no selected keys, ``resolved`` is empty. A budget failure omits all selected
    values and sets ``payload_budget_exceeded``; no truncated value is returned.
    Audit opens remain repeatable while a claim lease is held.
    """
    _require_v2(envelope)
    return _query(
        "SELECT nerode.porter_open(%s::jsonb, %s, %s::jsonb, %s, %s)",
        (
            json.dumps(envelope),
            receiver_session,
            json.dumps(list(payload_keys or [])),
            max_payload_bytes,
            claim_token,
        ),
        dsn,
        readonly=True,
    )


def claim(
    envelope: dict[str, Any],
    receiver_session: str,
    *,
    dsn: str | None = None,
    lease_seconds: int = 300,
) -> dict[str, Any]:
    """Atomically acquire a claim_once lease; simultaneous callers have one winner."""
    _require_v2(envelope)
    return _query(
        "SELECT nerode.porter_claim(%s::jsonb, %s, %s)",
        (json.dumps(envelope), receiver_session, lease_seconds),
        dsn,
    )


def release(
    envelope: dict[str, Any],
    claim_token: str,
    *,
    dsn: str | None = None,
) -> dict[str, Any]:
    """Release an unexpired lease only when its current claim token matches."""
    _require_v2(envelope)
    return _query(
        "SELECT nerode.porter_release(%s::jsonb, %s)", (json.dumps(envelope), claim_token), dsn
    )


def open_and_gate(
    envelope: dict[str, Any],
    receiver_session: str,
    tool: str,
    args: dict[str, Any] | None = None,
    *,
    dsn: str | None = None,
    claim_token: str | None = None,
) -> dict[str, Any]:
    """Verify v2, restore the carried ledger and certify a first-action decision.

    This writes receiver state. In claim_once mode an active matching lease is
    required. The result contains the bounded v2 context and a gate decision;
    the legacy open's mutable cache values are deliberately not returned.
    """
    _require_v2(envelope)
    with psycopg.connect(dsn or resolve_dsn(), connect_timeout=CONNECT_TIMEOUT) as conn:
        context = conn.execute(
            "SELECT nerode.porter_open(%s::jsonb, %s)", (json.dumps(envelope), receiver_session)
        ).fetchone()[0]
        if context.get("verification") != "valid" or not context.get("ledger_valid"):
            return {
                "context": context,
                "decision": {
                    "verdict": "REVISE",
                    "feedback": "handoff or carried ledger verification failed",
                },
            }
        if envelope["mode"] == "claim_once":
            lease = conn.execute(
                "SELECT expires_at FROM nerode.porter_claims WHERE envelope_hash=%s "
                "AND receiver_session=%s AND claim_token=%s AND released_at IS NULL FOR UPDATE",
                (envelope["envelope_hash"], receiver_session, claim_token),
            ).fetchone()
            # The row lock may have waited past expiry. Evaluate the clock
            # after acquiring it, before restoring or writing receiver state.
            active = (
                lease is not None
                and conn.execute("SELECT %s > clock_timestamp()", (lease[0],)).fetchone()[0]
            )
            if not active:
                return {
                    "context": context,
                    "decision": {
                        "verdict": "REVISE",
                        "feedback": "active claim token required for this receiver",
                    },
                }
        row = conn.execute(
            "SELECT nerode.open_and_gate(%s::jsonb, %s, %s, %s::jsonb)",
            (json.dumps(envelope["session"]), receiver_session, tool, json.dumps(args or {})),
        ).fetchone()
        result = row[0] if row else {}
        gate_context = result.get("context", {})
        if gate_context.get("error"):
            context["error"] = gate_context["error"]
        context["ledger_restored"] = gate_context.get("ledger_restored", 0)
        context["ledger_valid"] = gate_context.get("ledger_valid", False)
        return {"context": context, "decision": result.get("decision", {})}
