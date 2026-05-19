"""
benchmarks/bench.py
====================
Nerode throughput benchmark — measures ops/s for each cost tier.

Usage:
    python benchmarks/bench.py [--dsn DSN] [--reps N] [--threads T]

Tiers measured
--------------
  T1  nerode.run()         — membership query at string lengths 1,5,10,50,100
  T2  nerode.from_regex()  — construction at increasing pattern complexity
  T3  nerode.product()     — synchronous product at increasing state counts
  T4  nerode.equivalent()  — equivalence check (bisimulation vs counterexample)
  T5  concurrent run()     — T threads each firing 100 queries in parallel

All times are wall-clock.  Each tier reports: ops/s, median ms, p95 ms, p99 ms.
"""

from __future__ import annotations

import argparse
import statistics
import threading
import time
from typing import Callable

import psycopg

from nerode.db import apply_schema, resolve_dsn

# ---------------------------------------------------------------------------
# Diagnostic variants installed transiently for profiling (not in schema)
# ---------------------------------------------------------------------------

_MICRO_SQL = """
-- Micro-probe A: loop with only jsonb_build_object per step, no accumulation
CREATE OR REPLACE FUNCTION nerode._micro_build_only(p_input TEXT)
RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE v_i INTEGER; v_obj JSONB;
BEGIN
    FOR v_i IN 1..length(p_input) LOOP
        v_obj := jsonb_build_object('step', v_i, 'state', 0, 'sym', substring(p_input, v_i, 1), 'next', 1);
    END LOOP;
END $$;

-- Micro-probe B: loop with jsonb_build_object + jsonb_build_array wrapper, no accumulation
CREATE OR REPLACE FUNCTION nerode._micro_wrap_only(p_input TEXT)
RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE v_i INTEGER; v_arr JSONB;
BEGIN
    FOR v_i IN 1..length(p_input) LOOP
        v_arr := jsonb_build_array(
            jsonb_build_object('step', v_i, 'state', 0, 'sym', substring(p_input, v_i, 1), 'next', 1)
        );
    END LOOP;
END $$;

-- Micro-probe C: loop with the full accumulation — the actual line 74
-- v_steps grows by 1 element each iteration: at step k, || must copy k elements
CREATE OR REPLACE FUNCTION nerode._micro_accumulate(p_input TEXT)
RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE v_i INTEGER; v_steps JSONB := '[]'::JSONB;
BEGIN
    FOR v_i IN 1..length(p_input) LOOP
        v_steps := v_steps || jsonb_build_array(
            jsonb_build_object('step', v_i, 'state', 0, 'sym', substring(p_input, v_i, 1), 'next', 1)
        );
    END LOOP;
END $$;

-- Micro-probe D: accumulate into TEXT[] (indexed assignment), convert once at end
-- Pre-allocates the array so indexed assignment hits pre-existing slots
CREATE OR REPLACE FUNCTION nerode._micro_textarr(p_input TEXT)
RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE
    v_i    INTEGER;
    v_len  INTEGER := length(p_input);
    v_parts TEXT[];
    v_steps JSONB;
BEGIN
    FOR v_i IN 1..v_len LOOP
        v_parts[v_i] := format('{"step":%s,"state":0,"sym":"%s","next":1}',
                               v_i, substring(p_input, v_i, 1));
    END LOOP;
    v_steps := to_jsonb(v_parts);  -- one parse at the end
END $$;

-- Micro-probe E: accumulate via string_agg pattern — build one long string, parse once
CREATE OR REPLACE FUNCTION nerode._micro_strconcat(p_input TEXT)
RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE
    v_i    INTEGER;
    v_buf  TEXT := '[';
    v_sep  TEXT := '';
    v_steps JSONB;
BEGIN
    FOR v_i IN 1..length(p_input) LOOP
        v_buf := v_buf || v_sep ||
                 format('{"step":%s,"state":0,"sym":"%s","next":1}',
                        v_i, substring(p_input, v_i, 1));
        v_sep := ',';
    END LOOP;
    v_steps := (v_buf || ']')::JSONB;
END $$;
""";

_PROBE_SQL = """
-- T7a: bare loop — transitions only, zero JSONB
CREATE OR REPLACE FUNCTION nerode._probe_bare(p_id BIGINT, p_input TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql AS $$
DECLARE
    v_state INTEGER;
    v_next  INTEGER;
    v_i     INTEGER;
BEGIN
    SELECT state_id INTO v_state FROM nerode.states
    WHERE automaton_id = p_id AND is_initial = TRUE LIMIT 1;
    FOR v_i IN 1..length(p_input) LOOP
        SELECT to_state INTO v_next FROM nerode.transitions
        WHERE automaton_id = p_id
          AND from_state   = v_state
          AND symbol       = substring(p_input, v_i, 1);
        IF v_next IS NULL THEN RETURN FALSE; END IF;
        v_state := v_next;
    END LOOP;
    RETURN COALESCE(
        (SELECT is_accepting FROM nerode.states
         WHERE automaton_id = p_id AND state_id = v_state),
        FALSE
    );
END $$;

-- T7b: loop + O(n^2) jsonb trace build, no transition SELECT (fake state 0 always)
CREATE OR REPLACE FUNCTION nerode._probe_trace_only(p_id BIGINT, p_input TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql AS $$
DECLARE
    v_trace JSONB := '[]'::JSONB;
    v_i     INTEGER;
BEGIN
    FOR v_i IN 1..length(p_input) LOOP
        v_trace := v_trace || jsonb_build_array(
            jsonb_build_object('step', v_i, 'state', 0,
                               'sym', substring(p_input, v_i, 1))
        );
    END LOOP;
    RETURN TRUE;
END $$;

-- T7c: loop + transitions + jsonb_agg via temp table (O(n) inserts, one agg)
CREATE OR REPLACE FUNCTION nerode._probe_temptable(p_id BIGINT, p_input TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql AS $$
DECLARE
    v_state INTEGER;
    v_next  INTEGER;
    v_i     INTEGER;
BEGIN
    SELECT state_id INTO v_state FROM nerode.states
    WHERE automaton_id = p_id AND is_initial = TRUE LIMIT 1;

    CREATE TEMP TABLE IF NOT EXISTS _probe_trace_rows (
        step INTEGER, state INTEGER, sym TEXT
    ) ON COMMIT DROP;
    TRUNCATE _probe_trace_rows;

    INSERT INTO _probe_trace_rows VALUES (0, v_state, NULL);
    FOR v_i IN 1..length(p_input) LOOP
        SELECT to_state INTO v_next FROM nerode.transitions
        WHERE automaton_id = p_id
          AND from_state   = v_state
          AND symbol       = substring(p_input, v_i, 1);
        IF v_next IS NULL THEN
            DROP TABLE IF EXISTS _probe_trace_rows;
            RETURN FALSE;
        END IF;
        v_state := v_next;
        INSERT INTO _probe_trace_rows VALUES (v_i, v_state, substring(p_input, v_i, 1));
    END LOOP;

    -- Aggregate trace (this is what run() has to do one way or another)
    PERFORM jsonb_agg(jsonb_build_object('step', step, 'state', state, 'sym', sym)
                      ORDER BY step)
    FROM _probe_trace_rows;

    RETURN COALESCE(
        (SELECT is_accepting FROM nerode.states
         WHERE automaton_id = p_id AND state_id = v_state),
        FALSE
    );
END $$;
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dsn",     default=None,  help="PostgreSQL DSN (default: env/config)")
    p.add_argument("--reps",    type=int, default=200, help="Repetitions per micro-bench (default 200)")
    p.add_argument("--threads", type=int, default=8,   help="Thread count for T5 (default 8)")
    p.add_argument("--no-setup", action="store_true",  help="Skip schema apply (DB already ready)")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------

def _bench(fn: Callable, reps: int) -> dict:
    """
    Run *fn* (a zero-arg callable) *reps* times and return timing stats.
    Returns dict with keys: ops_s, median_ms, p95_ms, p99_ms, min_ms, max_ms.
    """
    samples: list[float] = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)

    samples_sorted = sorted(samples)
    n = len(samples_sorted)
    return {
        "ops_s":     1000 / statistics.median(samples),
        "median_ms": statistics.median(samples),
        "p95_ms":    samples_sorted[int(n * 0.95)],
        "p99_ms":    samples_sorted[int(n * 0.99)],
        "min_ms":    samples_sorted[0],
        "max_ms":    samples_sorted[-1],
    }


def _hdr(title: str) -> None:
    print(f"\n{'-'*72}")
    print(f"  {title}")
    print(f"{'-'*72}")
    print(f"  {'Label':<32} {'ops/s':>8}  {'med ms':>8}  {'p95 ms':>8}  {'p99 ms':>8}")
    print(f"  {'-'*32} {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}")


def _row(label: str, s: dict) -> None:
    print(
        f"  {label:<32} {s['ops_s']:>8.1f}  "
        f"{s['median_ms']:>8.2f}  {s['p95_ms']:>8.2f}  {s['p99_ms']:>8.2f}"
    )


# ---------------------------------------------------------------------------
# Fixture builders (run once before benchmarks)
# ---------------------------------------------------------------------------

def _build(conn, pattern: str, name: str = None, symbols=None) -> int:
    if symbols:
        return conn.execute(
            "SELECT nerode.from_regex(%s, %s, %s)", (pattern, name, symbols)
        ).fetchone()[0]
    return conn.execute(
        "SELECT nerode.from_regex(%s, %s)", (pattern, name)
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# T1 — nerode.run() at various string lengths
# ---------------------------------------------------------------------------

def bench_run(conn, reps: int) -> None:
    _hdr("T1  nerode.run() — membership query throughput")

    # DFA: accepts strings matching a+ over {a}
    aid = _build(conn, "a+", "bench_aplus")

    for length in (1, 5, 10, 50, 100):
        word = "a" * length
        s_full = _bench(
            lambda w=word, a=aid: conn.execute(
                "SELECT accept FROM nerode.run(%s, %s)", (a, w)
            ).fetchone(),
            reps,
        )
        s_fast = _bench(
            lambda w=word, a=aid: conn.execute(
                "SELECT accept FROM nerode.run(%s, %s, FALSE)", (a, w)
            ).fetchone(),
            reps,
        )
        _row(f"run(a+, len={length:>3}) traced", s_full)
        _row(f"run(a+, len={length:>3}) fast  ", s_fast)

    # reject path
    s = _bench(
        lambda a=aid: conn.execute(
            "SELECT accept FROM nerode.run(%s, %s, FALSE)", (a, "b" * 10)
        ).fetchone(),
        reps,
    )
    _row("run(a+, reject len=10) fast", s)

    # Longer DFA: pattern with more states
    aid2 = _build(conn, "ab+c*d?e", "bench_complex_run")
    for length in (1, 10, 50):
        word = ("abbbcccde" * (length // 9 + 1))[:length]
        s = _bench(
            lambda w=word, a=aid2: conn.execute(
                "SELECT accept FROM nerode.run(%s, %s)", (a, w)
            ).fetchone(),
            reps,
        )
        _row(f"run(ab+c*d?e, len={length:>3})", s)


# ---------------------------------------------------------------------------
# T2 — nerode.from_regex() construction throughput
# ---------------------------------------------------------------------------

PATTERNS = [
    ("literala",           "a",                  None),
    ("literalabcde",       "abcde",              None),
    ("kleenea*",           "a*",                 None),
    ("plusa+b+",           "a+b+",               None),
    ("optionalab?c",       "ab?c",               None),
    ("uniona|b|c|d",       "a|b|c|d",            None),
    ("concatabcdefgh",     "abcdefgh",           None),
    ("complex(a|b)*c",     "(a|b)*c",            None),
    ("complex(ab|cd)*ef",  "(ab|cd)*ef",         None),
    ("email-likea+b*c?d+", "a+b*c?d+",           None),
    ("shared-alpha{a,b}",  "a",                  list("ab")),
    ("shared-alpha{0-9}",  "a",                  list("0123456789")),
]


def bench_from_regex(conn, reps: int) -> None:
    _hdr("T2  nerode.from_regex() — construction throughput")

    for label, pattern, symbols in PATTERNS:
        # Reduce reps for heavier patterns to keep total runtime sane
        r = max(20, reps // 5)
        s = _bench(
            lambda p=pattern, sym=symbols: (
                conn.execute("SELECT nerode.from_regex(%s, NULL, %s)", (p, sym)).fetchone()
                if sym else
                conn.execute("SELECT nerode.from_regex(%s)", (p,)).fetchone()
            ),
            r,
        )
        _row(label, s)


# ---------------------------------------------------------------------------
# T3 — nerode.product() at increasing state-space size
# ---------------------------------------------------------------------------

PRODUCT_PAIRS = [
    # (label, pattern1, pattern2, op, shared_symbols)
    ("intersectiona*xa+",       "a*",      "a+",      "intersection", None),
    ("unionaub {a,b}",          "a",       "b",       "union",        list("ab")),
    ("intersection(a|b)*xa+b*", "(a|b)*",  "a+b*",    "intersection", list("ab")),
    ("union(ab|cd)*u(ef|gh)*",  "(ab|cd)*","(ef|gh)*","union",        list("abcdefgh")),
    ("intersectioncomplex",
        "(a|b|c)*d",  "(b|c|d)*e", "intersection", list("abcde")),
]


def bench_product(conn, reps: int) -> None:
    _hdr("T3  nerode.product() — synchronous product throughput")

    r = max(10, reps // 10)

    for label, p1, p2, op, sym in PRODUCT_PAIRS:
        if sym:
            aid1 = _build(conn, p1, symbols=sym)
            aid2 = _build(conn, p2, symbols=sym)
        else:
            aid1 = _build(conn, p1)
            aid2 = _build(conn, p2)

        s = _bench(
            lambda a1=aid1, a2=aid2, o=op: conn.execute(
                "SELECT nerode.product(%s, %s, %s)", (a1, a2, o)
            ).fetchone(),
            r,
        )
        _row(label, s)


# ---------------------------------------------------------------------------
# T4 — nerode.equivalent() — bisimulation vs counterexample
# ---------------------------------------------------------------------------

def bench_equivalent(conn, reps: int) -> None:
    _hdr("T4  nerode.equivalent() — equivalence check throughput")

    r = max(10, reps // 10)

    cases = [
        ("equiva* vs a*",           "a*",     "a*",     None),
        ("non-equiva* vs a+",        "a*",     "a+",     None),
        ("equiv(a|b)* vs (a|b)*",    "(a|b)*", "(a|b)*", list("ab")),
        ("non-equiva{ab} vs b{ab}",  "a",      "b",      list("ab")),
        ("equivcomplex same pattern",
            "(ab|cd)*ef", "(ab|cd)*ef", list("abcdef")),
    ]

    for label, p1, p2, sym in cases:
        if sym:
            aid1 = _build(conn, p1, symbols=sym)
            aid2 = _build(conn, p2, symbols=sym)
        else:
            aid1 = _build(conn, p1)
            aid2 = _build(conn, p2)

        s = _bench(
            lambda a1=aid1, a2=aid2: conn.execute(
                "SELECT equivalent FROM nerode.equivalent(%s, %s)", (a1, a2)
            ).fetchone(),
            r,
        )
        _row(label, s)


# ---------------------------------------------------------------------------
# T5 — concurrent run() queries across N threads
# ---------------------------------------------------------------------------

def bench_concurrent(dsn: str, thread_count: int, queries_per_thread: int = 100) -> None:
    _hdr(f"T5  concurrent nerode.run() — {thread_count} threads × {queries_per_thread} queries")

    # Build the DFA once in a setup connection
    with psycopg.connect(dsn) as setup_conn:
        aid = setup_conn.execute(
            "SELECT nerode.from_regex(%s, %s)", ("a+b*", "bench_concurrent")
        ).fetchone()[0]
        setup_conn.commit()

    timings: list[float] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def _worker():
        try:
            with psycopg.connect(dsn) as wconn:
                local = []
                for _ in range(queries_per_thread):
                    t0 = time.perf_counter()
                    wconn.execute(
                        "SELECT accept FROM nerode.run(%s, %s)", (aid, "aabbb")
                    ).fetchone()
                    local.append((time.perf_counter() - t0) * 1000)
            with lock:
                timings.extend(local)
        except Exception as exc:
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(thread_count)]
    wall_start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall_elapsed = time.perf_counter() - wall_start

    total_ops = thread_count * queries_per_thread
    if errors:
        print(f"  ERRORS ({len(errors)}): {errors[0]}")

    if timings:
        timings_sorted = sorted(timings)
        n = len(timings_sorted)
        print(f"  {'Label':<32} {'ops/s':>8}  {'med ms':>8}  {'p95 ms':>8}  {'p99 ms':>8}")
        print(f"  {'-'*32} {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}")
        print(
            f"  {'concurrent run() aabbb':<32} {total_ops/wall_elapsed:>8.1f}  "
            f"{statistics.median(timings):>8.2f}  "
            f"{timings_sorted[int(n*0.95)]:>8.2f}  "
            f"{timings_sorted[int(n*0.99)]:>8.2f}"
        )
        print(
            f"\n  Wall time for {total_ops} ops across {thread_count} threads: "
            f"{wall_elapsed*1000:.1f} ms"
        )


# ---------------------------------------------------------------------------
# T7 — profiling: isolate transition-SELECT cost vs JSONB-trace-build cost
# ---------------------------------------------------------------------------

def bench_micro(conn, reps: int) -> None:
    _hdr("T7a  Micro-probes: cost of each piece of the trace line")

    conn.execute(_MICRO_SQL)

    print(f"\n  {'Label':<42} {'ops/s':>8}  {'med ms':>8}  growth")
    print(f"  {'-'*42} {'-'*8}  {'-'*8}  ------")

    for length in (1, 5, 10, 25, 50, 100):
        word = "a" * length
        results = {}
        for key, sql in [
            ("A  jsonb_build_object only",   "SELECT nerode._micro_build_only(%s)"),
            ("B  + jsonb_build_array wrap",  "SELECT nerode._micro_wrap_only(%s)"),
            ("C  + || accumulate (line 74)", "SELECT nerode._micro_accumulate(%s)"),
            ("D  TEXT[] indexed + to_jsonb", "SELECT nerode._micro_textarr(%s)"),
            ("E  str concat + cast JSONB",   "SELECT nerode._micro_strconcat(%s)"),
        ]:
            results[key] = _bench(
                lambda s=sql, w=word: conn.execute(s, (w,)).fetchone(),
                reps,
            )

        baseline = results["A  jsonb_build_object only"]["median_ms"]
        print(f"\n  len={length:>3}")
        for key, s in results.items():
            extra = s["median_ms"] - baseline
            print(f"  {key:<42} {s['ops_s']:>8.1f}  {s['median_ms']:>8.2f}ms  "
                  f"{'':>3}{extra:>+.2f}ms vs A")


def bench_profile_run(conn, reps: int) -> None:
    _hdr("T7  run() cost breakdown — transitions vs JSONB trace build")

    # Install probe functions (session-scoped, rolled back with the connection)
    conn.execute(_PROBE_SQL)

    aid = _build(conn, "a+", "probe_aplus")

    print(f"\n  {'Label':<38} {'ops/s':>8}  {'med ms':>8}  {'p95 ms':>8}  {'p99 ms':>8}")
    print(f"  {'-'*38} {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}")

    for length in (1, 5, 10, 25, 50, 100):
        word_a   = "a" * length
        word_sym = "x" * length   # fake word for trace-only probe (no real DFA needed)

        # --- run() baseline (current implementation, full JSONB) ---
        s_full = _bench(
            lambda a=aid, w=word_a: conn.execute(
                "SELECT accept FROM nerode.run(%s, %s)", (a, w)
            ).fetchone(),
            reps,
        )

        # --- bare: transitions only, no JSONB whatsoever ---
        s_bare = _bench(
            lambda a=aid, w=word_a: conn.execute(
                "SELECT nerode._probe_bare(%s, %s)", (a, w)
            ).fetchone(),
            reps,
        )

        # --- trace-only: O(n^2) jsonb || loop, zero transition SELECTs ---
        s_trace = _bench(
            lambda a=aid, w=word_sym: conn.execute(
                "SELECT nerode._probe_trace_only(%s, %s)", (a, w)
            ).fetchone(),
            reps,
        )

        # --- temp-table: transitions + O(n) inserts + one jsonb_agg ---
        s_tmp = _bench(
            lambda a=aid, w=word_a: conn.execute(
                "SELECT nerode._probe_temptable(%s, %s)", (a, w)
            ).fetchone(),
            reps,
        )

        transition_ms  = s_bare["median_ms"]
        trace_build_ms = s_trace["median_ms"]
        temptable_ms   = s_tmp["median_ms"]
        full_ms        = s_full["median_ms"]
        overhead_ms    = full_ms - transition_ms   # attributed to JSONB build

        print(f"\n  len={length:>3}")
        print(f"  {'  run() full (CTE-based)':<38} {s_full['ops_s']:>8.1f}  {full_ms:>8.2f}")
        print(f"  {'  _probe_bare (transitions, no JSON)':<38} {s_bare['ops_s']:>8.1f}  {transition_ms:>8.2f}  <- transition cost")
        print(f"  {'  _probe_trace_only (jsonb||, no SELECT)':<38} {s_trace['ops_s']:>8.1f}  {trace_build_ms:>8.2f}  <- O(n^2) trace cost")
        print(f"  {'  _probe_temptable (tmp tbl + jsonb_agg)':<38} {s_tmp['ops_s']:>8.1f}  {temptable_ms:>8.2f}  <- temp-table baseline")
        pct = (overhead_ms / full_ms * 100) if full_ms else 0
        print(f"  {'  JSONB overhead (full - bare)':<38} {'':<8}  {overhead_ms:>+8.2f}  ({pct:.0f}% of full cost)")


# ---------------------------------------------------------------------------
# T6 — certify_run() overhead vs plain run()
# ---------------------------------------------------------------------------

def bench_certify_overhead(conn, reps: int) -> None:
    _hdr("T6  certify_run() overhead vs plain run()")

    r = max(20, reps // 5)
    aid = _build(conn, "a+b*c", "bench_certify_cmp")
    word = "aaabbc"

    s_plain = _bench(
        lambda a=aid, w=word: conn.execute(
            "SELECT accept FROM nerode.run(%s, %s)", (a, w)
        ).fetchone(),
        r,
    )
    s_cert = _bench(
        lambda a=aid, w=word: conn.execute(
            "SELECT accept, claim_id FROM nerode.certify_run(%s, %s)", (a, w)
        ).fetchone(),
        r,
    )

    print(f"  {'Label':<32} {'ops/s':>8}  {'med ms':>8}  {'p95 ms':>8}  {'p99 ms':>8}")
    print(f"  {'-'*32} {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}")
    _row("plain run()", s_plain)
    _row("certify_run()", s_cert)
    overhead = s_cert["median_ms"] - s_plain["median_ms"]
    print(f"\n  Certification overhead: {overhead:+.2f} ms / query")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = _parse()
    dsn = args.dsn or resolve_dsn()

    print(f"\nNerode benchmark  —  reps={args.reps}, threads={args.threads}")
    print(f"DSN: {dsn}")

    if not args.no_setup:
        with psycopg.connect(dsn) as conn:
            apply_schema(conn)
        print("Schema applied.")

    with psycopg.connect(dsn, autocommit=False) as conn:
        bench_run(conn, args.reps)
        bench_micro(conn, args.reps)
        bench_profile_run(conn, args.reps)
        bench_from_regex(conn, args.reps)
        bench_product(conn, args.reps)
        bench_equivalent(conn, args.reps)
        bench_certify_overhead(conn, args.reps)
        conn.rollback()   # leave DB clean

    bench_concurrent(dsn, args.threads)

    print(f"\n{'-'*72}")
    print("  Done.")
    print(f"{'-'*72}\n")


if __name__ == "__main__":
    main()
