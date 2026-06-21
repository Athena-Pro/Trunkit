"""
nerode.warden — Porter policy gate (LedgerAgent on Postgres).

A thin Python surface over the B0–B3 SQL: maintain a schema-anchored ledger of
observed task state, render it for the prompt, and gate environment-changing
tool calls against domain policy predicates before they fire.

Implements the deterministic core of LedgerAgent (arXiv:2606.20529):
    Absorb  → Warden.absorb(path, value)
    Render  → Warden.render()
    Gate    → Warden.gate(tool, args)  →  Decision(verdict, feedback, claim_id)

The gate decision is proof-carrying: each call records a cert claim whose witness
pins the ledger snapshot, so a consumer re-verifies ALLOW/REVISE/BLOCK without
trusting the producer (see nerode.replay_gate / nerode.gate_drift).

Usage
-----
    from nerode.warden import Warden, register_policy

    register_policy(
        name="cancel_within_policy",
        tool="cancel_reservation",
        predicate_sql=(
            "($2->>'reservation_id') = ($1->'reservation'->>'id') AND ("
            " ($1->'reservation'->>'hours_since_booking')::numeric <= 24"
            " OR ($1->'reservation'->>'airline_cancelled')::boolean"
            " OR ($1->'reservation'->>'insurance_covered')::boolean )"
        ),
        message="Reservation is outside the 24-hour cancellation window "
                "and is not airline-cancelled or insured.",
    )

    with Warden("sess-julia") as w:
        w.absorb("reservation", {"id": "UX789", "hours_since_booking": 48,
                                 "airline_cancelled": False, "insurance_covered": False},
                 schema_type="reservation")
        decision = w.gate("cancel_reservation", {"reservation_id": "UX789"})
        if decision.verdict != "ALLOW":
            ...  # surface decision.feedback to the model; do NOT call the tool
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from types import TracebackType
from typing import Any

import psycopg

from nerode.db import resolve_dsn


@dataclass
class Decision:
    """Result of a policy gate evaluation."""

    verdict: str          # 'ALLOW' | 'REVISE' | 'BLOCK'
    feedback: str         # policy-grounded explanation
    claim_id: int | None  # cert claim id of the recorded decision
    witness: dict         # pinned ledger snapshot + per-rule results

    @property
    def allowed(self) -> bool:
        return self.verdict == "ALLOW"


def register_gated_tool(
    tool: str, description: str | None = None, *, dsn: str | None = None
) -> None:
    """Mark *tool* as environment-changing (governed by the gate)."""
    with psycopg.connect(dsn or resolve_dsn()) as conn:
        conn.execute("SELECT nerode.register_gated_tool(%s, %s)", (tool, description))
        conn.commit()


def register_policy(
    name: str,
    tool: str,
    predicate_sql: str,
    message: str,
    *,
    effect: str = "block",
    dsn: str | None = None,
) -> int:
    """Register/replace a policy predicate.

    *predicate_sql* is an ALLOW-condition boolean over ``$1`` (ledger JSONB) and
    ``$2`` (args JSONB); the action is permitted iff it evaluates TRUE.
    *effect* is 'block' (hard ⇒ BLOCK) or 'revise' (soft ⇒ REVISE).
    """
    with psycopg.connect(dsn or resolve_dsn()) as conn:
        row = conn.execute(
            "SELECT nerode.register_policy(%s, %s, %s, %s, %s)",
            (name, tool, predicate_sql, message, effect),
        ).fetchone()
        conn.commit()
        return row[0] if row else -1


class Warden:
    """Session-scoped ledger + policy gate."""

    def __init__(self, session_id: str | None = None, *, dsn: str | None = None) -> None:
        self.session_id = session_id or f"warden-{uuid.uuid4().hex[:8]}"
        self._dsn = dsn or resolve_dsn()
        self._conn: psycopg.Connection | None = None

    # ------------------------------------------------------------------ lifecycle
    def connect(self) -> None:
        self._conn = psycopg.connect(self._dsn)

    def disconnect(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Warden:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.disconnect()

    def _require(self) -> psycopg.Connection:
        if self._conn is None:
            raise RuntimeError("Warden used before connect() / outside its context manager")
        return self._conn

    # ------------------------------------------------------------------ Absorb
    def absorb(
        self,
        path: str,
        value: Any,
        *,
        schema_type: str | None = None,
        source_event: int | None = None,
    ) -> None:
        """Project a successful tool return into the ledger (latest wins)."""
        conn = self._require()
        conn.execute(
            "SELECT nerode.ledger_absorb(%s, %s, %s::jsonb, %s, %s)",
            (self.session_id, path, json.dumps(value), schema_type, source_event),
        )
        conn.commit()

    # ------------------------------------------------------------------ Render
    def render(self) -> dict:
        """The compact typed dictionary {path: value} for prompt re-injection."""
        conn = self._require()
        row = conn.execute(
            "SELECT nerode.ledger_render(%s)", (self.session_id,)
        ).fetchone()
        return row[0] if row and row[0] else {}

    def get(self, path: str) -> Any:
        conn = self._require()
        row = conn.execute(
            "SELECT nerode.ledger_get(%s, %s)", (self.session_id, path)
        ).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------ Gate
    def gate(self, tool: str, args: dict | None = None) -> Decision:
        """Evaluate a proposed environment-changing call and record the decision.

        Returns a Decision; call the tool only if ``decision.allowed``.
        """
        conn = self._require()
        args = args or {}
        verdict, feedback, witness = conn.execute(
            "SELECT verdict, feedback, witness FROM nerode.policy_gate(%s, %s, %s::jsonb)",
            (self.session_id, tool, json.dumps(args)),
        ).fetchone()
        claim_row = conn.execute(
            "SELECT nerode.certify_gate(%s, %s, %s::jsonb)",
            (self.session_id, tool, json.dumps(args)),
        ).fetchone()
        conn.commit()
        return Decision(
            verdict=verdict,
            feedback=feedback,
            claim_id=claim_row[0] if claim_row else None,
            witness=witness,
        )

    def drifted(self, witness: dict) -> bool:
        """True if the live ledger no longer matches the decision's snapshot."""
        conn = self._require()
        row = conn.execute(
            "SELECT nerode.gate_drift(%s, %s::jsonb)",
            (self.session_id, json.dumps(witness)),
        ).fetchone()
        return bool(row[0]) if row else False

    # ------------------------------------------------------------------ Carry
    def close(
        self, *, attention_hint: str | None = None, cache_keys: list[str] | None = None
    ) -> dict:
        """Pack this session's ledger into a cert-verified Porter envelope (Model A).

        The returned envelope carries the typed task-state ledger forward, so the
        next model opens it already populated and can gate with zero tool calls.
        """
        conn = self._require()
        detail: dict = {}
        if attention_hint:
            detail["attention_hint"] = attention_hint
        if cache_keys is not None:
            detail["cache_keys"] = cache_keys
        row = conn.execute(
            "SELECT nerode.close_with_ledger(%s, %s::jsonb)",
            (self.session_id, json.dumps(detail)),
        ).fetchone()
        conn.commit()
        return row[0] if row else {}

    @classmethod
    def open(
        cls,
        envelope: dict,
        new_session_id: str,
        *,
        dsn: str | None = None,
    ) -> tuple[dict, Warden]:
        """Open a carried envelope for Model B; restore the ledger; return a Warden.

        Returns (context, warden) where context includes the restored ``ledger``,
        ``ledger_valid`` (hash re-verification), and resolved pre-cache. The
        returned Warden is bound to *new_session_id* with an open connection,
        ready to ``gate`` the first action.
        """
        warden = cls(new_session_id, dsn=dsn)
        warden.connect()
        row = warden._require().execute(
            "SELECT nerode.open_with_ledger(%s::jsonb, %s)",
            (json.dumps(envelope), new_session_id),
        ).fetchone()
        warden._require().commit()
        return (row[0] if row else {}), warden

    @classmethod
    def open_and_gate(
        cls,
        envelope: dict,
        new_session_id: str,
        tool: str,
        args: dict | None = None,
        *,
        dsn: str | None = None,
    ) -> tuple[dict, Decision]:
        """Open the carried ledger and gate the first proposed action in one call.

        Porter handoff + LedgerAgent gate as one object — Model B decides whether
        its first environment-changing call is permitted with zero tool calls.
        """
        warden = cls(new_session_id, dsn=dsn)
        warden.connect()
        row = warden._require().execute(
            "SELECT nerode.open_and_gate(%s::jsonb, %s, %s, %s::jsonb)",
            (json.dumps(envelope), new_session_id, tool, json.dumps(args or {})),
        ).fetchone()
        warden._require().commit()
        result = row[0] if row else {}
        d = result.get("decision", {})
        decision = Decision(
            verdict=d.get("verdict", "REVISE"),
            feedback=d.get("feedback", ""),
            claim_id=d.get("claim_id"),
            witness=d.get("witness", {}),
        )
        warden.disconnect()
        return result.get("context", {}), decision
