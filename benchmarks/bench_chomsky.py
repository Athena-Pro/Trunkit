"""
benchmarks/bench_chomsky.py
============================
Chomsky hierarchy simulator performance benchmark.

Measures throughput (ops/s) and latency (median/p95/p99 ms) for each machine
at a range of input sizes. Also compares traced vs fast mode and the classify()
orchestrator overhead.

Usage:
    python benchmarks/bench_chomsky.py [--dsn DSN] [--reps N]

Machines benchmarked
--------------------
  C1  chomsky.run_dfa(input)        — Type 3: a*b*      — inline 3-state DFA
  C2  chomsky.run_pda(input)        — Type 2: a^n b^n   — TEXT[] stack
  C3  chomsky.run_lba(input)        — Type 1: a^n b^n c^n — tape array, O(n) rounds
  C4  chomsky.run_tm(input)         — Type 0: 0^(2^k)   — halving rounds, O(log n)
  C5  chomsky.classify(input)       — orchestrator: all four machines
  C6  traced vs fast comparison     — p_trace=TRUE vs FALSE per machine/size
"""

from __future__ import annotations

import argparse
import statistics
import time
from typing import Callable

import psycopg

from nerode.db import apply_schema, resolve_dsn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bench(fn: Callable, reps: int) -> dict:
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
    print(f"\n{'-'*74}")
    print(f"  {title}")
    print(f"{'-'*74}")
    print(f"  {'Label':<34} {'ops/s':>8}  {'med ms':>8}  {'p95 ms':>8}  {'p99 ms':>8}")
    print(f"  {'-'*34} {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}")


def _row(label: str, s: dict, note: str = "") -> None:
    suffix = f"  {note}" if note else ""
    print(
        f"  {label:<34} {s['ops_s']:>8.1f}  "
        f"{s['median_ms']:>8.3f}  {s['p95_ms']:>8.3f}  {s['p99_ms']:>8.3f}{suffix}"
    )


def _sep() -> None:
    print(f"  {'-'*34} {'-'*8}  {'-'*8}  {'-'*8}  {'-'*8}")


# ---------------------------------------------------------------------------
# C1 — DFA: a*b*
# ---------------------------------------------------------------------------

def bench_dfa(conn, reps: int) -> None:
    _hdr("C1  chomsky.run_dfa()  —  Type 3: a*b*")

    cases = [
        ("dfa len=1    'a'",         "a",              True),
        ("dfa len=10   'aaaa...bbbb'","a"*5 + "b"*5,  True),
        ("dfa len=50   a^25 b^25",   "a"*25 + "b"*25, True),
        ("dfa len=100  a^50 b^50",   "a"*50 + "b"*50, True),
        ("dfa len=500  a^250 b^250", "a"*250+"b"*250,  True),
        ("dfa reject   'ba'",        "ba",             True),
        ("dfa reject   'aba'",       "aba",            True),
    ]

    for label, word, traced in cases:
        s = _bench(
            lambda w=word: conn.execute(
                "SELECT accept FROM chomsky.run_dfa(%s)", (w,)
            ).fetchone(),
            reps,
        )
        _row(label, s)


# ---------------------------------------------------------------------------
# C2 — PDA: a^n b^n
# ---------------------------------------------------------------------------

def bench_pda(conn, reps: int) -> None:
    _hdr("C2  chomsky.run_pda()  —  Type 2: a^n b^n  (stack depth = n)")

    for n in (1, 5, 10, 25, 50, 100, 250):
        word = "a" * n + "b" * n
        label = f"pda n={n:<4}  len={2*n:<5}"

        s_traced = _bench(
            lambda w=word: conn.execute(
                "SELECT accept FROM chomsky.run_pda(%s, TRUE)", (w,)
            ).fetchone(),
            reps,
        )
        s_fast = _bench(
            lambda w=word: conn.execute(
                "SELECT accept FROM chomsky.run_pda(%s, FALSE)", (w,)
            ).fetchone(),
            reps,
        )
        _row(label + " traced", s_traced)
        _row(label + " fast  ", s_fast,
             note=f"speedup {s_fast['ops_s']/s_traced['ops_s']:.1f}x")

    _sep()
    # Rejection paths
    for word, desc in [("b", "reject 'b' (no a's)"),
                       ("aab", "reject 'aab' (unbalanced)"),
                       ("a"*50 + "b"*49, "reject a^50 b^49")]:
        s = _bench(
            lambda w=word: conn.execute(
                "SELECT accept FROM chomsky.run_pda(%s, FALSE)", (w,)
            ).fetchone(),
            reps,
        )
        _row(f"pda {desc:<22} fast  ", s)


# ---------------------------------------------------------------------------
# C3 — LBA: a^n b^n c^n  (O(n) rounds each O(n) → O(n²) total)
# ---------------------------------------------------------------------------

def bench_lba(conn, reps: int) -> None:
    _hdr("C3  chomsky.run_lba()  —  Type 1: a^n b^n c^n  [O(n^2)]")

    for n in (1, 5, 10, 25, 50, 100):
        word = "a" * n + "b" * n + "c" * n
        label = f"lba n={n:<4}  len={3*n:<5}"
        r = max(10, reps // max(1, n // 10))   # reduce reps for large n

        s_traced = _bench(
            lambda w=word: conn.execute(
                "SELECT accept FROM chomsky.run_lba(%s, TRUE)", (w,)
            ).fetchone(),
            r,
        )
        s_fast = _bench(
            lambda w=word: conn.execute(
                "SELECT accept FROM chomsky.run_lba(%s, FALSE)", (w,)
            ).fetchone(),
            r,
        )
        _row(label + " traced", s_traced)
        _row(label + " fast  ", s_fast,
             note=f"speedup {s_fast['ops_s']/s_traced['ops_s']:.1f}x")

    _sep()
    # Complexity growth: show O(n²) scaling
    print(f"\n  Scaling analysis (traced, relative to n=1):")
    print(f"  {'n':<6} {'len':<6} {'med ms':>10}  {'relative':>10}")
    base_ms = None
    for n in (1, 5, 10, 25, 50, 100):
        word = "a" * n + "b" * n + "c" * n
        r = max(10, reps // max(1, n // 5))
        s = _bench(
            lambda w=word: conn.execute(
                "SELECT accept FROM chomsky.run_lba(%s, TRUE)", (w,)
            ).fetchone(),
            r,
        )
        if base_ms is None:
            base_ms = s["median_ms"]
        print(f"  {n:<6} {3*n:<6} {s['median_ms']:>10.3f}ms  {s['median_ms']/base_ms:>10.1f}x")


# ---------------------------------------------------------------------------
# C4 — TM: 0^(2^k)   (O(log n) rounds)
# ---------------------------------------------------------------------------

def bench_tm(conn, reps: int) -> None:
    _hdr("C4  chomsky.run_tm()   —  Type 0: 0^(2^k)  [O(log n) rounds]")

    print(f"\n  Accept path (input = 0^(2^k)):")
    for k in range(7):    # k=0..6 → lengths 1,2,4,8,16,32,64
        n = 2 ** k
        word = "0" * n
        label = f"tm k={k}  len={n:<4}"

        s_traced = _bench(
            lambda w=word: conn.execute(
                "SELECT accept FROM chomsky.run_tm(%s, 64, TRUE)", (w,)
            ).fetchone(),
            reps,
        )
        s_fast = _bench(
            lambda w=word: conn.execute(
                "SELECT accept FROM chomsky.run_tm(%s, 64, FALSE)", (w,)
            ).fetchone(),
            reps,
        )
        _row(label + " traced", s_traced)
        _row(label + " fast  ", s_fast,
             note=f"speedup {s_fast['ops_s']/s_traced['ops_s']:.1f}x")

    _sep()
    print(f"\n  Reject path (odd length — early exit after 1 round):")
    for n in (3, 5, 7, 15, 31):
        word = "0" * n
        s = _bench(
            lambda w=word: conn.execute(
                "SELECT accept FROM chomsky.run_tm(%s, 64, FALSE)", (w,)
            ).fetchone(),
            reps,
        )
        _row(f"tm reject len={n:<4} fast  ", s)

    _sep()
    print(f"\n  Scaling: rounds grow as log2(n):")
    print(f"  {'k':<4} {'len':<6} {'rounds':>8}  {'med ms':>10}")
    for k in range(7):
        n = 2 ** k
        word = "0" * n
        r_count = k + 1    # accept at round k+1 (count hits 1)
        s = _bench(
            lambda w=word: conn.execute(
                "SELECT accept FROM chomsky.run_tm(%s, 64, FALSE)", (w,)
            ).fetchone(),
            reps,
        )
        print(f"  {k:<4} {n:<6} {r_count:>8}  {s['median_ms']:>10.3f}ms")


# ---------------------------------------------------------------------------
# C5 — classify(): all four machines per input
# ---------------------------------------------------------------------------

def bench_classify(conn, reps: int) -> None:
    _hdr("C5  chomsky.classify()  —  orchestrator overhead (all 4 machines)")

    r = max(20, reps // 5)

    cases = [
        # (label,       input,              which machines accept)
        ("classify ''  (DFA only)",     "",           "Type3 only (DFA)"),
        ("classify 'aabb'  (DFA+PDA)",  "aabb",       "Type3+2   (DFA,PDA)"),
        ("classify 'aabbcc' (LBA only)", "aabbcc",    "Type1     (LBA)"),
        ("classify '0000'  (TM only)",  "0000",       "Type0     (TM)"),
        ("classify 'aaabbbccc' n=3",    "a"*3+"b"*3+"c"*3, "Type1 (LBA)"),
        ("classify a^10 b^10",          "a"*10+"b"*10,     "Type3+2 (DFA,PDA)"),
    ]

    for label, word, which in cases:
        s = _bench(
            lambda w=word: conn.execute(
                "SELECT machine, accept FROM chomsky.classify(%s)", (w,)
            ).fetchall(),
            r,
        )
        _row(label, s, note=which)

    # Compare classify() vs individual machines for same input
    _sep()
    print(f"\n  Classify overhead vs individual machine calls (input='aabb'):")
    word = "aabb"
    s_dfa = _bench(
        lambda: conn.execute("SELECT accept FROM chomsky.run_dfa(%s, FALSE)", (word,)).fetchone(),
        r,
    )
    s_pda = _bench(
        lambda: conn.execute("SELECT accept FROM chomsky.run_pda(%s, FALSE)", (word,)).fetchone(),
        r,
    )
    s_lba = _bench(
        lambda: conn.execute("SELECT accept FROM chomsky.run_lba(%s, FALSE)", (word,)).fetchone(),
        r,
    )
    s_tm = _bench(
        lambda: conn.execute("SELECT accept FROM chomsky.run_tm(%s, 64, FALSE)", (word,)).fetchone(),
        r,
    )
    s_all = _bench(
        lambda: conn.execute("SELECT machine, accept FROM chomsky.classify(%s)", (word,)).fetchall(),
        r,
    )
    sum_individual = s_dfa["median_ms"] + s_pda["median_ms"] + s_lba["median_ms"] + s_tm["median_ms"]
    overhead_pct = (s_all["median_ms"] / sum_individual - 1) * 100 if sum_individual else 0

    _row("  run_dfa  (fast)", s_dfa)
    _row("  run_pda  (fast)", s_pda)
    _row("  run_lba  (fast)", s_lba)
    _row("  run_tm   (fast)", s_tm)
    print(f"  {'  sum of 4 individual calls':<34} {'':>8}  {sum_individual:>8.3f}ms")
    _row("  classify() (all 4 traced)", s_all)
    print(f"\n  classify() overhead vs sum of individual fast calls: {overhead_pct:+.1f}%")


# ---------------------------------------------------------------------------
# C6 — Traced vs fast mode: how much does witness-building cost?
# ---------------------------------------------------------------------------

def bench_trace_overhead(conn, reps: int) -> None:
    _hdr("C6  Witness-trace overhead  —  p_trace=TRUE vs FALSE per machine")

    machines = [
        # (func,        input_fn,           sizes,           label_prefix)
        ("run_dfa",   lambda n: "a"*n+"b"*n,   [1,10,50,100,500], "DFA"),
        ("run_pda",   lambda n: "a"*n+"b"*n,   [1,10,50,100,250], "PDA"),
        ("run_lba",   lambda n: "a"*n+"b"*n+"c"*n, [1,10,25,50],  "LBA"),
        ("run_tm",    lambda n: "0"*(2**n),    [0,1,2,3,4,5],     "TM (k=n)"),
    ]

    for func, inp_fn, sizes, prefix in machines:
        print(f"\n  {prefix} — chomsky.{func}()")
        print(f"  {'size':<8} {'traced ops/s':>14} {'fast ops/s':>14} {'speedup':>10} {'trace ms':>10} {'fast ms':>10}")
        print(f"  {'-'*8} {'-'*14} {'-'*14} {'-'*10} {'-'*10} {'-'*10}")

        for sz in sizes:
            word = inp_fn(sz)
            length = len(word)
            r = max(10, reps // max(1, length // 50))

            if func == "run_tm":
                sql_traced = f"SELECT accept FROM chomsky.{func}(%s, 64, TRUE)"
                sql_fast   = f"SELECT accept FROM chomsky.{func}(%s, 64, FALSE)"
            else:
                sql_traced = f"SELECT accept FROM chomsky.{func}(%s, TRUE)"
                sql_fast   = f"SELECT accept FROM chomsky.{func}(%s, FALSE)"

            s_t = _bench(lambda w=word, s=sql_traced: conn.execute(s, (w,)).fetchone(), r)
            s_f = _bench(lambda w=word, s=sql_fast:   conn.execute(s, (w,)).fetchone(), r)

            if func == "run_tm":
                size_label = f"k={sz} len={length}"
            else:
                size_label = f"len={length}"

            speedup = s_f["ops_s"] / s_t["ops_s"] if s_t["ops_s"] > 0 else 0
            print(f"  {size_label:<8} {s_t['ops_s']:>14.1f} {s_f['ops_s']:>14.1f} "
                  f"{speedup:>10.2f}x {s_t['median_ms']:>10.3f} {s_f['median_ms']:>10.3f}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Chomsky hierarchy benchmark")
    p.add_argument("--dsn",      default=None,  help="PostgreSQL DSN")
    p.add_argument("--reps",     type=int, default=200, help="Reps per micro-bench (default 200)")
    p.add_argument("--sections", default="all",
                   help="Comma-separated sections: dfa,pda,lba,tm,classify,trace (default: all)")
    p.add_argument("--no-setup", action="store_true", help="Skip schema apply")
    return p.parse_args()


def main() -> None:
    args = _parse()
    dsn = args.dsn or resolve_dsn()
    want = set(args.sections.split(",")) if args.sections != "all" else None

    print(f"\nChomsky hierarchy benchmark  —  reps={args.reps}")
    print(f"DSN: {dsn}")

    if not args.no_setup:
        with psycopg.connect(dsn) as conn:
            apply_schema(conn)
        print("Schema applied.")

    with psycopg.connect(dsn, autocommit=False) as conn:
        if want is None or "dfa" in want:
            bench_dfa(conn, args.reps)
        if want is None or "pda" in want:
            bench_pda(conn, args.reps)
        if want is None or "lba" in want:
            bench_lba(conn, args.reps)
        if want is None or "tm" in want:
            bench_tm(conn, args.reps)
        if want is None or "classify" in want:
            bench_classify(conn, args.reps)
        if want is None or "trace" in want:
            bench_trace_overhead(conn, args.reps)
        conn.rollback()

    print(f"\n{'-'*74}")
    print("  Done.")
    print(f"{'-'*74}\n")


if __name__ == "__main__":
    main()
