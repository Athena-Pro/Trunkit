"""
tools/stream_interceptor.py
===========================
Intercepts ``nerode_query`` tool calls in an AI event stream.

When the AI emits a tool call of the form::

    {"tool": "nerode_query", "arguments": {"spec": "<regex>"}}

this interceptor:
  1. Builds a minimal DFA via ``nerode.from_regex()``.
  2. Exports the automaton JSON.
  3. Renders the transition table.
  4. Returns the result dict that should be injected back as the
     tool-call response.

Usage (STDIN / STDOUT streaming)
---------------------------------
The interceptor reads newline-delimited JSON events from ``stdin`` and
writes them to ``stdout``, injecting a synthetic ``tool_result`` event
immediately after any ``nerode_query`` tool_call event.

    python -m tools.stream_interceptor < events.jsonl

or use :func:`handle_event` from your own streaming loop.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
src_dir_str = str(SRC_DIR)
if src_dir_str not in sys.path:
    sys.path.insert(0, src_dir_str)

from nerode.automata import export_from_db, print_transition_table
from nerode.db import TRUNKIT_DSN, connect, resolve_dsn

# ---------------------------------------------------------------------------
# Core handler
# ---------------------------------------------------------------------------


def handle_nerode_query(spec: str, dsn: str | None = None) -> dict[str, Any]:
    """
    Build a certified minimal DFA from *spec* (a regex) and return a result
    dict suitable for injection as a tool response.

    Steps:
      1. nerode.from_regex(spec) — Thompson → subset → Hopcroft minimal DFA.
      2. nerode.certify(..., 'from_regex', ...) — issues cert.claim +
         cert.certificate + cert.witness(construction_record).
      3. nerode.calx_state_facts(id) — arithmetic annotation of |Q|
         (prime factorization, squarefreeness, arithmetic derivative).
      4. Export JSON + render transition table.

    Returns a dict with:
      automaton_id, state_count, alphabet,
      transition_table (plain text),
      nerode_partition (Myhill-Nerode equivalence classes, from cert witness),
      calx_facts (arithmetic annotation from Trunkit calx schema if available),
      automaton_json (canonical JSON export),
      cert_claim_id.
    """
    dsn = dsn or resolve_dsn()
    with connect(dsn) as conn:
        # 1. Build minimal DFA
        auto_id: int = conn.execute(
            "SELECT nerode.from_regex(%s)", (spec,)
        ).fetchone()[0]

        state_count: int = conn.execute(
            "SELECT state_count FROM nerode.automata WHERE id = %s", (auto_id,)
        ).fetchone()[0]

        # 2. Issue a construction cert and read back the partition witness.
        #    Pass evidence and witness as explicit JSONB literals to avoid
        #    indeterminate-type errors from jsonb_build_object with %s params.
        from psycopg.types.json import Jsonb
        evidence = Jsonb({"regex": spec, "pipeline": "thompson->subset->hopcroft"})
        witness_body = Jsonb({
            "regex":       spec,
            "state_count": state_count,
            "pipeline":    ["thompson_nfa", "subset_dfa", "hopcroft_min"],
        })
        cert_claim_id: int = conn.execute(
            "SELECT nerode.certify(%s, 'from_regex', %s, 'construction_record', %s)",
            (auto_id, evidence, witness_body),
        ).fetchone()[0]

        # Read the Nerode partition from the construction_log (written by minimize)
        partition_row = conn.execute(
            """
            SELECT result->'partition'
            FROM nerode.construction_log
            WHERE automaton_id = %s AND operation = 'minimize'
            ORDER BY id DESC LIMIT 1
            """,
            (auto_id,),
        ).fetchone()
        partition = partition_row[0] if partition_row else None

        # 3. Arithmetic annotation (delegates to calx when Trunkit is co-deployed)
        calx_facts = conn.execute(
            "SELECT nerode.calx_state_facts(%s)", (auto_id,)
        ).fetchone()[0]

        # 4. Export full automaton JSON
        data = export_from_db(conn, auto_id)
        conn.commit()

    # Render transition table as a plain-text string
    import io
    buf = io.StringIO()
    _stdout = sys.stdout
    sys.stdout = buf
    try:
        print_transition_table(data)
    finally:
        sys.stdout = _stdout
    table_text = buf.getvalue()

    return {
        "automaton_id":     auto_id,
        "state_count":      state_count,
        "alphabet":         data["alphabet"],
        "transition_table": table_text,
        "nerode_partition": partition,
        "calx_facts":       calx_facts,
        "cert_claim_id":    cert_claim_id,
        "automaton_json":   data,
    }


# ---------------------------------------------------------------------------
# Stream filtering (newline-delimited JSON)
# ---------------------------------------------------------------------------


def iter_events(stream) -> Iterator[dict[str, Any]]:
    """Yield parsed JSON objects from a newline-delimited event stream."""
    for line in stream:
        line = line.rstrip("\n")
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            # Pass through non-JSON lines as a special wrapper
            yield {"_raw": line}


def process_stream(
    in_stream=None,
    out_stream=None,
    dsn: str | None = None,
) -> None:
    """
    Read newline-delimited JSON events from *in_stream* (default: stdin),
    write them to *out_stream* (default: stdout).

    When a ``tool_call`` event with ``tool == "nerode_query"`` is seen, an
    additional synthetic ``tool_result`` event is emitted immediately after.
    """
    if in_stream is None:
        in_stream = sys.stdin
    if out_stream is None:
        out_stream = sys.stdout

    for event in iter_events(in_stream):
        # Pass through the original event
        if "_raw" in event:
            out_stream.write(event["_raw"] + "\n")
        else:
            out_stream.write(json.dumps(event) + "\n")
        out_stream.flush()

        # Intercept nerode_query tool calls
        if event.get("type") == "tool_call" and event.get("tool") == "nerode_query":
            spec = (event.get("arguments") or {}).get("spec", "")
            call_id = event.get("id") or event.get("call_id")
            try:
                result = handle_nerode_query(spec, dsn=dsn)
                response = {
                    "type":    "tool_result",
                    "tool":    "nerode_query",
                    "call_id": call_id,
                    "result":  result,
                }
            except Exception as exc:  # noqa: BLE001
                response = {
                    "type":    "tool_result",
                    "tool":    "nerode_query",
                    "call_id": call_id,
                    "error":   str(exc),
                }
            out_stream.write(json.dumps(response) + "\n")
            out_stream.flush()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(
        prog="stream_interceptor",
        description="Intercept nerode_query tool calls in an AI event stream.",
    )
    p.add_argument("--dsn", metavar="DSN", help="PostgreSQL DSN override.")
    p.add_argument("--trunkit", action="store_true",
                   help="Use the Trunkit co-deployment DB (port 5434, live calx facts).")
    p.add_argument(
        "--query",
        metavar="REGEX",
        help="Run a single nerode_query directly and print the result as JSON.",
    )
    args = p.parse_args()

    dsn = args.dsn or (TRUNKIT_DSN if args.trunkit else None)
    if args.query:
        result = handle_nerode_query(args.query, dsn=dsn)
        print(json.dumps(result, indent=2, default=str))
    else:
        process_stream(dsn=dsn)


if __name__ == "__main__":
    main()
