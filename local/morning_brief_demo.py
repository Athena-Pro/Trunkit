"""
scripts/morning_brief_demo.py
==============================
End-to-end demo of the Nerode model-handoff stack with cybernetic monitoring.

What this demo does
-------------------
1.  Pre-pack four external data sources (weather, two tickers, HN headlines)
    into the Nerode sequence cache via Precacher.  Model B can retrieve all
    four with zero tool calls by calling Precacher.open(envelope).

2.  Fetch AAPL 5-day price history and encode each day-over-day change as a
    metric symbol: U (up >0.1%), D (down >0.1%), S (stable).

3.  Simulate a control sequence: the first step is an Action (A -- "placed a
    buy order") and every subsequent step is idle (_).  Each time step
    produces one paired symbol from the metric_x_control alphabet, e.g. "UA"
    (price rose and an order was placed) or "D_" (price fell, nothing done).

4.  Log the paired symbols to nerode.cybernetic_log and let scan_cybernetic()
    run every registered DFA for the metric_x_control alphabet -- including
    the composite dead_time_5_x_metric_oscillate.

5.  Close the session: close_session() certifies the boundary, embeds DFA
    states in the envelope, and fires pg_notify('nerode_session_ready').

6.  Open the session as Model B: Precacher.open(envelope) re-verifies the
    cert, fetches all pre-cached values, and returns the resolved context.

7.  Print a formatted summary of everything Model B receives on entry.

Usage
-----
    python scripts/morning_brief_demo.py

Requires the Nerode PostgreSQL instance (port 5435) to be reachable.
Network access is needed to fetch live data from open-meteo, Yahoo Finance,
and the HN Algolia API.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import psycopg

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
src_dir_str = str(SRC_DIR)
if src_dir_str not in sys.path:
    sys.path.insert(0, src_dir_str)

from nerode.db import resolve_dsn
from nerode.precache import Precacher
from nerode.sources import (
    HNSource,
    TickerHistorySource,
    TickerSource,
    WeatherSource,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TODAY       = date.today().isoformat()
SESSION_A   = f"morning-brief-{TODAY}"
SESSION_B   = f"morning-brief-{TODAY}-b"

WEATHER_LAT, WEATHER_LON, WEATHER_LABEL = 51.5074, -0.1278, "London"
TICKERS     = ["AAPL", "MSFT"]
HN_N        = 5

COMPOSITE_NAME = "dead_time_5_x_metric_oscillate"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def hr(title: str = "") -> None:
    width = 70
    if title:
        pad = (width - len(title) - 2) // 2
        print(f"\n{'-' * pad} {title} {'-' * (width - pad - len(title) - 2)}")
    else:
        print("-" * width)


def section(title: str, data: object) -> None:
    hr(title)
    print(json.dumps(data, indent=2, default=str))


def encode_control(i: int) -> str:
    """Step 0 = Action (order placed); all later steps = idle."""
    return "A" if i == 0 else "_"


def composite_dfa_state(conn, session_id: str, dfa_name: str) -> dict:
    """Return the composite DFA state for the session's cybernetic_log."""
    row = conn.execute(
        """
        SELECT a.id, a.state_count
        FROM   nerode.automata a
        WHERE  a.name = %s
        """,
        (dfa_name,),
    ).fetchone()
    if row is None:
        return {"error": f"DFA {dfa_name!r} not found"}

    dfa_id, _ = row

    # Build the input as a TEXT[] array from the session's metric_x_control log
    symbols = conn.execute(
        """
        SELECT array_agg(symbol ORDER BY seq)
        FROM   nerode.cybernetic_log
        WHERE  session_id = %s
          AND  alphabet   = 'metric_x_control'
        """,
        (session_id,),
    ).fetchone()[0]

    if not symbols:
        return {"state_id": None, "accepting": False, "note": "no events logged"}

    state_id = conn.execute(
        "SELECT nerode.run_to_state_arr(%s, %s)", (dfa_id, symbols)
    ).fetchone()[0]

    if state_id is None:
        return {"state_id": None, "accepting": None, "note": "missing transition"}

    row = conn.execute(
        "SELECT label, is_accepting FROM nerode.states "
        "WHERE automaton_id = %s AND state_id = %s",
        (dfa_id, state_id),
    ).fetchone()
    label, accepting = (row[0], row[1]) if row else (None, None)

    return {
        "state_id":  state_id,
        "label":     label,
        "accepting": accepting,
        "input":     symbols,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    dsn = resolve_dsn()

    # ------------------------------------------------------------------
    # Step 1: Pre-pack external data
    # ------------------------------------------------------------------
    hr("Step 1 -- Pre-packing external data")
    print(f"Session A: {SESSION_A}")

    with Precacher(SESSION_A, dsn=dsn) as pc:
        print("  fetching weather ...", end=" ", flush=True)
        pc.fetch(
            f"weather:{WEATHER_LABEL.lower()}:{TODAY}",
            WeatherSource(WEATHER_LAT, WEATHER_LON, label=WEATHER_LABEL),
            retries=2,
            force_rebuild=True,
        )
        print("ok")

        for sym in TICKERS:
            print(f"  fetching ticker:{sym} ...", end=" ", flush=True)
            pc.fetch(
                f"ticker:{sym}:{TODAY}",
                TickerSource(sym),
                retries=2,
                force_rebuild=True,
            )
            print("ok")

        print(f"  fetching HN top-{HN_N} ...", end=" ", flush=True)
        pc.fetch(
            f"news:hn:top{HN_N}:{TODAY}",
            HNSource(HN_N),
            retries=2,
            force_rebuild=True,
        )
        print("ok")

        print("  fetching AAPL 5d history ...", end=" ", flush=True)
        pc.fetch(
            f"metric:AAPL:5d:{TODAY}",
            TickerHistorySource("AAPL", range_="5d"),
            retries=2,
            force_rebuild=True,
        )
        print("ok")

    envelope = pc.envelope
    print(f"\nEnvelope keys: {envelope.get('cache_keys')}")

    # ------------------------------------------------------------------
    # Step 2: Encode history -> paired symbols and log to cybernetic_log
    # ------------------------------------------------------------------
    hr("Step 2 -- Encoding metric history + logging cybernetic events")

    with psycopg.connect(dsn, autocommit=True) as conn:
        # Retrieve the cached history
        history_raw = conn.execute(
            "SELECT result FROM nerode.sequence_cache WHERE seq_key = %s",
            (f"metric:AAPL:5d:{TODAY}",),
        ).fetchone()

        if history_raw is None:
            print("ERROR: history not found in cache", file=sys.stderr)
            sys.exit(1)

        history: list[dict] = history_raw[0]

        print(f"\nAAPL 5-day history ({len(history)} trading days):\n")
        print(f"  {'Date':<12}  {'Close':>8}  {'Direction':>10}  {'Control':>8}  {'Paired':>7}")
        print(f"  {'-'*12}  {'-'*8}  {'-'*10}  {'-'*8}  {'-'*7}")

        # Clear any previous cybernetic log for this session
        conn.execute(
            "DELETE FROM nerode.cybernetic_log "
            "WHERE session_id = %s AND alphabet = 'metric_x_control'",
            (SESSION_A,),
        )

        for i, day in enumerate(history):
            ctrl = encode_control(i)
            paired = day["direction"] + ctrl
            print(
                f"  {day['date']:<12}  {day['close']:>8.2f}"
                f"  {day['direction']:>10}  {ctrl:>8}  {paired:>7}"
            )
            conn.execute(
                "SELECT nerode.log_cybernetic(%s, 'metric_x_control', %s, %s)",
                (
                    SESSION_A,
                    paired,
                    json.dumps({"date": day["date"], "close": day["close"]}),
                ),
            )

    # ------------------------------------------------------------------
    # Step 3: Check composite DFA state
    # ------------------------------------------------------------------
    hr("Step 3 -- Composite DFA state")

    with psycopg.connect(dsn, autocommit=True) as conn:
        state = composite_dfa_state(conn, SESSION_A, COMPOSITE_NAME)

    print(f"\n  DFA:        {COMPOSITE_NAME}")
    print("  Pattern:    A_{5,} AND (UD){3,}  (oscillating AND unresponsive)")
    print(f"  Input:      {state.get('input')}")
    print(f"  State:      {state.get('state_id')}  ({state.get('label')})")
    print(f"  Accepting:  {state.get('accepting')}")

    if state.get("accepting"):
        print("\n  *** ALERT: metric is oscillating AND action is unacknowledged ***")
    else:
        print("\n  (pattern not yet triggered with this history)")

    # ------------------------------------------------------------------
    # Step 4: Close session A
    # ------------------------------------------------------------------
    hr("Step 4 -- Closing session A (certifying handoff)")

    # Re-open Precacher against the existing session to call close_session
    pc2 = Precacher(SESSION_A, dsn=dsn)
    pc2.connect()
    envelope = pc2.close(
        attention_hint=(
            f"Pre-packed: weather/{WEATHER_LABEL}, tickers={TICKERS}, "
            f"HN top-{HN_N}, AAPL 5d metric history. "
            f"Composite DFA {'ALARM' if state.get('accepting') else 'nominal'}."
        )
    )
    pc2.disconnect()

    print(f"\n  cert_bundle_id: {envelope.get('cert_bundle_id')}")
    print(f"  cache_keys:     {envelope.get('cache_keys')}")
    print(f"  session_dfa_states: {envelope.get('session_dfa_states')}")
    print(f"  attention_hint: {envelope.get('attention_hint', '')[:80]}...")

    # ------------------------------------------------------------------
    # Step 5: Open as Model B
    # ------------------------------------------------------------------
    hr("Step 5 -- Opening as Model B")
    print(f"Session B: {SESSION_B}\n")

    ctx = Precacher.open(envelope, SESSION_B, dsn=dsn)

    prior = ctx["prior_session"]
    print(f"  cert_valid:       {prior['cert_valid']}")
    print(f"  session_id:       {prior['session_id']}")
    print(f"  cert_claim_id:    {prior.get('cert_claim_id')}")

    section("Resolved cache (Model B sees this on entry)", ctx["resolved"])

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    hr("Summary")
    resolved = ctx["resolved"]
    weather_key = f"weather:{WEATHER_LABEL.lower()}:{TODAY}"
    weather = resolved.get(weather_key, {})
    print(f"\nWeather ({WEATHER_LABEL}):  {weather.get('condition')}, "
          f"{weather.get('temperature_2m')} C")

    for sym in TICKERS:
        q = resolved.get(f"ticker:{sym}:{TODAY}", {})
        pct = q.get("regularMarketChangePercent", 0)
        print(f"{sym}:  ${q.get('regularMarketPrice', '?'):.2f}  "
              f"({pct:+.2f}%)")

    news = resolved.get(f"news:hn:top{HN_N}:{TODAY}", [])
    print(f"\nHN top-{HN_N}:")
    for s in news:
        print(f"  [{s.get('score', '?'):>4}] {s['title'][:65]}")

    print(f"\nComposite DFA ({COMPOSITE_NAME}):")
    print(f"  state={state.get('state_id')} ({state.get('label')})  "
          f"accepting={state.get('accepting')}")

    hr()
    print(f"\nModel B is ready.  All {len(resolved)} cache keys resolved with zero tool calls.")


if __name__ == "__main__":
    main()
