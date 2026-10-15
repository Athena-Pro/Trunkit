#!/usr/bin/env python3
"""verify_bundle.py — consumer-side bundle verifier (no database required).

Reads a `cert.export_bundle` JSON document (from a file or stdin) and
independently re-checks every kernel-backed claim using calx.bundle — with no
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

from calx.bundle import verify_bundle  # noqa: E402


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        base_dir = Path(sys.argv[1]).resolve().parent if len(sys.argv) > 1 else Path.cwd()
        raw = (Path(sys.argv[1]).read_text(encoding="utf-8")
               if len(sys.argv) > 1 else sys.stdin.read())
        results = verify_bundle(json.loads(raw), base_dir=base_dir)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    exit_code = 0
    for r in results:
        print(f"[{r.mark}] claim {r.claim_id}: {r.status.lower()} — {r.statement}")
        for note in r.notes:
            print(f"    {note}")
        if r.ok is not True:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
