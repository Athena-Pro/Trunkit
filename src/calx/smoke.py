"""Installed-package checks for arithmetic, DFA execution, and Porter handoff.

Run ``python -m calx.smoke --help`` for connection options. Check transactions
are rolled back, so running this module does not append demo certificates.
"""

from __future__ import annotations

import argparse
import sys
import uuid

import psycopg
from psycopg.types.json import Jsonb

from calx import db as calx_db
from nerode import db as nerode_db


def check_trunkit(dsn: str | None = None) -> None:
    with calx_db.connect(dsn) as conn:
        try:
            conn.execute(f"SET LOCAL search_path = {calx_db.SEARCH_PATH}")
            row = conn.execute(
                "SELECT calx.crt(ARRAY[2,3]::bigint[], ARRAY[3,5]::bigint[])"
            ).fetchone()
            if row != (8,):
                raise RuntimeError(f"CRT smoke check failed: expected 8, received {row!r}")
            methods = conn.execute("SELECT count(*) FROM cert.method").fetchone()[0]
            if not methods:
                raise RuntimeError("No certificate methods installed; run trunkit init")
        finally:
            conn.rollback()
    print("trunkit: CRT result verified; certificate methods available")


def check_nerode(dsn: str | None = None) -> None:
    with nerode_db.connect(dsn) as conn:
        try:
            automaton_id = conn.execute("SELECT nerode.from_regex('a*b+')").fetchone()[0]
            for word, expected in (("aaab", True), ("aaa", False)):
                result = conn.execute(
                    "SELECT accept FROM nerode.run(%s, %s)", (automaton_id, word),
                ).fetchone()[0]
                if result is not expected:
                    raise RuntimeError(f"DFA smoke check failed for {word!r}: {result!r}")

            session = f"trunkit-smoke-{uuid.uuid4().hex}"
            key = f"{session}:brief"
            brief = {"message": "Ready for the next session"}
            conn.execute(
                "SELECT nerode.build_sequence_cache("
                "%s, ARRAY[]::bigint[], 0, 'store', 'a', false, %s)",
                (key, Jsonb(brief)),
            )
            conn.execute("SELECT nerode.tag_cache_key(%s, %s)", (session, key))
            envelope = conn.execute(
                "SELECT nerode.close_session(%s)", (session,),
            ).fetchone()[0]
            context = conn.execute(
                "SELECT nerode.open_session(%s, %s)", (Jsonb(envelope), f"{session}-b"),
            ).fetchone()[0]
            if context["prior_session"]["cert_valid"] is not True:
                raise RuntimeError("Porter handoff certificate failed verification")
            if context["resolved"].get(key) != brief:
                raise RuntimeError("Porter handoff did not recover the cached brief")
        finally:
            conn.rollback()
    print("nerode: DFA accept/reject verified; Porter handoff certificate and brief verified")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=("trunkit", "nerode"))
    parser.add_argument("--trunk-dsn", help="Trunkit DSN (default: CALX_DSN or native default)")
    parser.add_argument(
        "--nerode-dsn", help="Nerode DSN (default: NERODE_DSN or container default)",
    )
    args = parser.parse_args(argv)
    try:
        if args.only != "nerode":
            check_trunkit(args.trunk_dsn)
        if args.only != "trunkit":
            check_nerode(args.nerode_dsn)
    except (psycopg.Error, RuntimeError) as exc:
        print(f"smoke check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
