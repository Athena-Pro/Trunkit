"""
nerode.precache — Pre-cache external data before a model session.

Usage (context manager — Model A side)
---------------------------------------
    from nerode.precache import Precacher
    from nerode.adapters import HttpSource

    with Precacher("pre-2026-05-19-a") as pc:
        pc.fetch("github:issues:open",  "https://api.github.com/repos/org/repo/issues")
        pc.fetch("metric:latency:p99",  lambda: fetch_latency())
        pc.store("arith_deriv:50",      compute_arith_deriv(50))

    envelope = pc.envelope   # pass to Model B

Usage (static open — Model B side)
------------------------------------
    from nerode.precache import Precacher

    ctx = Precacher.open(envelope, "model-b-001")
    # ctx["resolved"]["github:issues:open"]  → list of issues, no tool call
    # ctx["prior_session"]["cert_valid"]      → True

Usage (manual / single-connection)
------------------------------------
    pc = Precacher("my-session")
    pc.connect()
    pc.store("key", value)
    envelope = pc.close(attention_hint="key pre-cached")
    ctx = pc.open_for("model-b-001")   # re-uses open connection
    pc.disconnect()

API summary
-----------
    store(key, value)              — store a pre-computed value directly
    fetch(key, source, retries=2)  — resolve source via adapter, then store
    close(attention_hint=...)      — call close_session(), return envelope
    open_for(new_session_id)       — call open_session() on open connection
    Precacher.open(env, new_id)    — classmethod; opens its own connection

Each store()/fetch() call:
  1. Calls nerode.build_sequence_cache(key, mode='store') — upserts the value.
  2. Calls nerode.tag_cache_key(session_id, key) — links it to this session.

The session itself has no session_log events (no tool-call trace), so the DFA
states in the envelope will be the initial states — correct for a pre-cache
run that is not itself a model session.
"""

from __future__ import annotations

import json
import uuid
from types import TracebackType
from typing import Any

import psycopg

from nerode.adapters import with_retry
from nerode.db import CONNECT_TIMEOUT, resolve_dsn


class Precacher:
    """Build a pre-cached handoff envelope from external data sources."""

    def __init__(
        self,
        session_id: str | None = None,
        *,
        dsn: str | None = None,
        cert_root: str | None = None,
    ) -> None:
        self.session_id: str = session_id or f"precache-{uuid.uuid4().hex[:8]}"
        self._dsn = dsn or resolve_dsn()
        self._conn: psycopg.Connection | None = None
        self._keys: list[str] = []
        self.envelope: dict | None = None
        # Cross-layer entanglement: the cert ledger head (cert.ledger_root() in
        # the Trunkit DB) this handoff was packed against. Embedding it binds the
        # envelope to a specific proof-ledger state; the trunk side can close the
        # loop with cert.anchor_external('handoff_envelope', <envelope hash>).
        self._cert_root = cert_root

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        # connect_timeout is NOT optional here.  nerode.db already learned this
        # -- its own connect() passes CONNECT_TIMEOUT with the comment "Without
        # a timeout, an unreachable host hangs the CLI indefinitely" -- but this
        # method called psycopg.connect() bare, so it hung instead of failing.
        #
        # It bit on 2026-07-26: tests/test_sources.py's
        # test_envelope_attention_hint_lists_all_keys constructs
        # Precacher(session_id) with no dsn, which resolves to the default
        # nerode instance (5435, a stopped container), and the whole pytest run
        # blocked in select() with no indication of which test was responsible.
        # Three suite runs went into localising that.
        self._conn = psycopg.connect(self._dsn, connect_timeout=CONNECT_TIMEOUT)

    def disconnect(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> Precacher:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is None:
            hint = (
                f"{len(self._keys)} key(s) pre-cached: {', '.join(self._keys)}"
                if self._keys
                else "empty pre-cache session"
            )
            self.close(attention_hint=hint)
        self.disconnect()

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def store(
        self,
        key: str,
        value: Any,
        *,
        force_rebuild: bool = False,
    ) -> None:
        """Store *value* under *key* in sequence_cache and tag it to this session.

        *value* can be any JSON-serialisable object — list, dict, scalar.
        For list values the *length* field is set to len(value); otherwise 0.
        """
        if self._conn is None:
            raise RuntimeError("Precacher.store() called before connect()")

        length = len(value) if isinstance(value, (list, tuple)) else 0

        self._conn.execute(
            """
            SELECT nerode.build_sequence_cache(
                %s,
                ARRAY[]::BIGINT[],
                %s,
                'store',
                'a',
                %s,
                %s::jsonb
            )
            """,
            # default=str so DATE / TIMESTAMP / NUMERIC / UUID values coming
            # straight out of a query are storable.  Without it, store() raises
            # "Object of type date is not JSON serializable" on the most obvious
            # use of this class -- caching the result of a SELECT -- and every
            # caller has to hand-serialise its rows first.  str() is lossy in
            # principle; it is the right trade here because the envelope is a
            # briefing to be read, and a date rendered '2026-07-25' is exactly
            # what a reader wants.
            (key, length, force_rebuild, json.dumps(value, default=str)),
        )
        self._conn.execute(
            "SELECT nerode.tag_cache_key(%s, %s)",
            (self.session_id, key),
        )
        if key not in self._keys:
            self._keys.append(key)

    def fetch(
        self,
        key: str,
        source: Any,
        *,
        retries: int = 2,
        force_rebuild: bool = False,
    ) -> None:
        """Resolve *source* via an adapter, then store the result under *key*.

        *source* accepts the same forms as nerode.adapters.resolve():
          callable  — sync or async, called with no arguments
          str       — HTTP GET URL; response JSON is stored
          Source    — any object with a .fetch() method

        On failure the request is retried up to *retries* times with
        exponential back-off (1 s, 2 s, 4 s, …).
        """
        value = with_retry(source, retries=retries)
        self.store(key, value, force_rebuild=force_rebuild)

    def close(self, *, attention_hint: str | None = None, cert_root: str | None = None) -> dict:
        """Call nerode.close_session(), commit, and return the envelope dict.

        *cert_root* (or the value passed to __init__) is the Trunkit
        cert.ledger_root() this handoff was packed against.  It is recorded in
        the CLOSING CERTIFICATE's ``valid_under`` as ``trunkit.cert_ledger_root``
        — not as a top-level envelope field — and is reachable from the envelope
        via ``cert_bundle_id``.  That entangles the agent handoff with the proof
        ledger, and does so inside the ledger's own hash chain.
        """
        if self._conn is None:
            raise RuntimeError("Precacher.close() called before connect()")

        detail: dict = {}
        if attention_hint:
            detail["attention_hint"] = attention_hint
        root = cert_root or self._cert_root
        if root:
            # MUST go under 'trunkit'.  nerode.close_session() honours exactly
            # three p_detail keys -- cache_keys, attention_hint, trunkit -- and
            # silently drops everything else.  This previously set a top-level
            # 'cert_ledger_root', which the docstring claimed was "embedded in
            # the envelope" and which the SQL threw away without complaint, so
            # every envelope packed to date carried no ledger binding at all.
            #
            # Via 'trunkit' the root lands in the closing certificate's
            # valid_under, reachable from the envelope through cert_bundle_id --
            # which is better than a bare envelope field, because it is then
            # itself covered by the ledger's hash chain.
            detail["trunkit"] = {"cert_ledger_root": root}

        row = self._conn.execute(
            "SELECT nerode.close_session(%s, %s::jsonb)",
            (self.session_id, json.dumps(detail)),
        ).fetchone()

        self._conn.commit()
        self.envelope = row[0] if row else None
        return self.envelope  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def open_for(self, new_session_id: str) -> dict:
        """Call nerode.open_session() re-using this precacher's open connection.

        Requires close() to have been called first.  Use Precacher.open() when
        Model B runs in a separate process and needs its own connection.
        """
        if self.envelope is None:
            raise RuntimeError("open_for() called before close()")
        if self._conn is None:
            raise RuntimeError("open_for() requires an active connection")

        row = self._conn.execute(
            "SELECT nerode.open_session(%s::jsonb, %s)",
            (json.dumps(self.envelope), new_session_id),
        ).fetchone()
        return row[0] if row else {}

    @classmethod
    def open(
        cls,
        envelope: dict,
        new_session_id: str,
        *,
        dsn: str | None = None,
    ) -> dict:
        """Open a handoff envelope for Model B in its own connection.

        This is the Model B entry point when running in a separate process from
        Model A.  Opens a connection, calls nerode.open_session(), commits, and
        closes — no Precacher instance required.

        Returns the context object (resolved values, DFA context, cert status).
        """
        # Same reasoning as connect(): Model B is often a fresh process with no
        # idea whether the handoff DB is up, and a bare connect() would hang it
        # on the one code path whose entire purpose is a fast cold start.
        conn = psycopg.connect(dsn or resolve_dsn(), connect_timeout=CONNECT_TIMEOUT)
        try:
            row = conn.execute(
                "SELECT nerode.open_session(%s::jsonb, %s)",
                (json.dumps(envelope), new_session_id),
            ).fetchone()
            conn.commit()
            return row[0] if row else {}
        finally:
            conn.close()
