"""Port the Curry SQLite store into the unified Postgres `curry` schema.

Source: .curry/curry.db (the local store the
        register_calx_in_curry.py task populated: 6 constants + 19 functions)
Target: curry.* in the live `calx` Postgres database.

Idempotent: every INSERT uses ON CONFLICT DO NOTHING, so re-running only adds
rows that are missing. JSON-in-TEXT columns become JSONB; BLOB becomes bytea;
SQLite timestamp strings are carried over to preserve declaration provenance.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

PROJECT_DIR = Path(__file__).resolve().parent.parent
SQLITE_PATH = PROJECT_DIR / ".curry" / "curry.db"
PG_DSN = os.environ.get(
    "CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk"
)


def _jsonb(raw):
    """SQLite stored JSON as TEXT (or NULL). Return a Jsonb wrapper or None."""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    return Jsonb(json.loads(raw))


def main() -> int:
    if not SQLITE_PATH.is_file():
        print(f"error: {SQLITE_PATH} not found", file=sys.stderr)
        return 1

    sq = sqlite3.connect(str(SQLITE_PATH))
    sq.row_factory = sqlite3.Row

    counts = {"retirement_tags": 0, "constants": 0, "functions": 0, "function_dependencies": 0}

    with psycopg.connect(PG_DSN) as pg:
        with pg.cursor() as cur:
            # retirement_tags (may be empty)
            for row in sq.execute("SELECT * FROM retirement_tags"):
                cur.execute(
                    """INSERT INTO curry.retirement_tags
                           (tag_id, created_at, reason, description)
                       VALUES (%s, %s, %s, %s)
                       ON CONFLICT (tag_id) DO NOTHING""",
                    (row["tag_id"], row["created_at"], row["reason"], row["description"]),
                )
                counts["retirement_tags"] += cur.rowcount

            # constants
            for row in sq.execute("SELECT * FROM constants"):
                cur.execute(
                    """INSERT INTO curry.constants
                           (id, version, value, type_signature,
                            declared_at, retired_at, retirement_tag_id, description)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (id, version) DO NOTHING""",
                    (
                        row["id"],
                        row["version"],
                        bytes(row["value"]),
                        row["type_signature"],
                        row["declared_at"],
                        row["retired_at"],
                        row["retirement_tag_id"],
                        row["description"],
                    ),
                )
                counts["constants"] += cur.rowcount

            # functions
            for row in sq.execute("SELECT * FROM functions"):
                cur.execute(
                    """INSERT INTO curry.functions
                           (name, version, body, constant_bindings, function_bindings,
                            is_pure, declared_at, retired_at, retirement_tag_id,
                            expected_args, description, arg_descriptions)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (name, version) DO NOTHING""",
                    (
                        row["name"],
                        row["version"],
                        row["body"],
                        _jsonb(row["constant_bindings"]) or Jsonb({}),
                        _jsonb(row["function_bindings"]) or Jsonb({}),
                        bool(row["is_pure"]),
                        row["declared_at"],
                        row["retired_at"],
                        row["retirement_tag_id"],
                        _jsonb(row["expected_args"]),
                        row["description"],
                        _jsonb(row["arg_descriptions"]),
                    ),
                )
                counts["functions"] += cur.rowcount

            # function_dependencies
            for row in sq.execute("SELECT * FROM function_dependencies"):
                cur.execute(
                    """INSERT INTO curry.function_dependencies
                           (function_name, function_version,
                            depends_on_constant_id, depends_on_constant_version,
                            depends_on_function_name, depends_on_function_version)
                       SELECT %s, %s, %s, %s, %s, %s
                       WHERE NOT EXISTS (
                           SELECT 1 FROM curry.function_dependencies d
                            WHERE d.function_name = %s
                              AND d.function_version = %s
                              AND d.depends_on_constant_id IS NOT DISTINCT FROM %s
                              AND d.depends_on_constant_version IS NOT DISTINCT FROM %s
                              AND d.depends_on_function_name IS NOT DISTINCT FROM %s
                              AND d.depends_on_function_version IS NOT DISTINCT FROM %s
                       )""",
                    (
                        row["function_name"],
                        row["function_version"],
                        row["depends_on_constant_id"],
                        row["depends_on_constant_version"],
                        row["depends_on_function_name"],
                        row["depends_on_function_version"],
                        row["function_name"],
                        row["function_version"],
                        row["depends_on_constant_id"],
                        row["depends_on_constant_version"],
                        row["depends_on_function_name"],
                        row["depends_on_function_version"],
                    ),
                )
                counts["function_dependencies"] += cur.rowcount
        pg.commit()

    sq.close()
    print("ported into curry.* (rows inserted this run):")
    for k, v in counts.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
