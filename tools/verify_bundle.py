#!/usr/bin/env python3
"""verify_bundle.py — consumer-side bundle verifier (no database required).

Reads a `cert.export_bundle` JSON document (from a file or stdin) and
independently re-checks every kernel-backed claim using calx.kernel — with no
psycopg, no Postgres, no Trunkit install on the producer's side. This is the
proof-carrying-code consumer: the proof object travelled with the result, and a
small independent checker re-verifies it.

Usage:
    trunkit export 1 2 3 > bundle.json   # producer
    python tools/verify_bundle.py bundle.json   # consumer (this script)
    cat bundle.json | python tools/verify_bundle.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
src_dir_str = str(SRC_DIR)
if src_dir_str not in sys.path:
    sys.path.insert(0, src_dir_str)

from calx.kernel import verify_bundle


def main() -> int:
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as fh:
            raw = fh.read()
    else:
        raw = sys.stdin.read()
    bundle = json.loads(raw)
    results = verify_bundle(bundle)

    exit_code = 0
    for r in results:
        cid = r["claim_id"]
        if not r["checkable"]:
            print(f"[·] claim {cid}: not kernel-checkable offline — {r['note']}")
            continue
        agree = r["agrees_with_ledger"]
        verdict = r["independent_verdict"]
        flag = {"valid": "✓", "refuted": "✗", "unverified": "?"}[verdict]
        mismatch = "" if agree else f"  !! ledger said {r['ledger_status']}"
        print(f"[{flag}] claim {cid}: {verdict}{mismatch}  — {r['statement']}")
        if not agree or verdict == "refuted":
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
