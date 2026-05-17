"""Smoke test: verify Curry-wrapped calx functions produce correct call plans
and (when a Postgres DB is reachable) execute end-to-end.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from calx import curry_adapter  # noqa: E402


def _check_plan(label, plan, expected):
    ok = all(plan.get(k) == v for k, v in expected.items())
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}: {plan}")
    if not ok:
        print(f"        expected superset of: {expected}")
    return ok


def main() -> int:
    fail = 0

    print("== plan_only ==")
    if not _check_plan(
        "calx_aliquot_step(n=220)",
        curry_adapter.plan_only("calx_aliquot_step", 1, {"n": 220}),
        {
            "sql": "function",
            "proc": "aliquot_step",
            "args": [220],
            "returns": "scalar",
            "schema_version": 1,
        },
    ):
        fail += 1

    if not _check_plan(
        "calx_ext_gcd(a=240, b=46)",
        curry_adapter.plan_only("calx_ext_gcd", 1, {"a": 240, "b": 46}),
        {
            "sql": "function",
            "proc": "ext_gcd",
            "args": [240, 46],
            "returns": "rows",
            "schema_version": 1,
        },
    ):
        fail += 1

    if not _check_plan(
        "calx_crt(remainders=[2,3,2], moduli=[3,5,7])",
        curry_adapter.plan_only(
            "calx_crt", 1, {"remainders": [2, 3, 2], "moduli": [3, 5, 7]}
        ),
        {
            "sql": "function",
            "proc": "crt",
            "args": [[2, 3, 2], [3, 5, 7]],
            "returns": "scalar",
            "schema_version": 1,
        },
    ):
        fail += 1

    if not _check_plan(
        "calx_trace_orbit(220, ALIQUOT, 20, 1000000)",
        curry_adapter.plan_only(
            "calx_trace_orbit",
            1,
            {"start_n": 220, "rel_type": "ALIQUOT", "max_steps": 20, "lim": 1_000_000},
        ),
        {
            "sql": "procedure",
            "proc": "trace_orbit",
            "args": [220, "ALIQUOT", 20, 1_000_000],
            "returns": "none",
            "schema_version": 1,
        },
    ):
        fail += 1

    # --- optional live execution ---
    print()
    print("== live execution (best-effort) ==")
    try:
        import psycopg

        dsn = os.environ.get("CALX_DSN") or "postgresql://trunk:trunk@localhost:5434/trunk"
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            print(f"  connected: {dsn}")
            # aliquot_step is a pure SELECT — no schema state needed beyond the function definition.
            try:
                result = curry_adapter.run("calx_aliquot_step", 1, {"n": 220}, dsn=dsn)
                print(f"  [OK ] calx_aliquot_step(220) → {result}  (expect 284: σ(220)−220)")
                if result != 284:
                    fail += 1
            except Exception as exc:
                print(f"  [FAIL] calx_aliquot_step(220): {exc}")
                fail += 1
    except Exception as exc:
        print(f"  [SKIP] could not connect: {exc}")

    print()
    print(f"failures: {fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
