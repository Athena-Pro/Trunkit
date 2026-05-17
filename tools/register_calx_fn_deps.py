"""Register calx inter-function dependencies in both the SQLite Curry store
and the Postgres curry schema.

Idempotent: checks for an existing matching row before inserting, because
curry.function_dependencies has no unique constraint on the logical columns.

Dependencies are read from the SQL source: any calx-wrapped function that
calls another calx-wrapped function in its PL/pgSQL body gets a row here.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import psycopg

PROJECT_DIR = Path(__file__).resolve().parent.parent
SQLITE_PATH = PROJECT_DIR / ".curry" / "curry.db"
PG_DSN = "postgresql://trunk:trunk@localhost:5434/trunk"

# (caller_name, caller_version, callee_name, callee_version)
# Derived from reading the SQL source in 04_crt.sql and 05_dynamics.sql.
FUNCTION_DEPS: list[tuple[str, int, str, int]] = [
    # 04_crt.sql call chain
    ("calx_mod_inverse",        1, "calx_ext_gcd",              1),
    ("calx_crt_combine",        1, "calx_mod_inverse",          1),
    ("calx_crt",                1, "calx_crt_combine",          1),
    ("calx_crt_reconstruct",    1, "calx_crt",                  1),
    ("calx_progression_intersect", 1, "calx_ext_gcd",           1),
    # 05_dynamics.sql
    ("calx_characterize_relation", 1, "calx_ext_gcd",           1),
    ("calx_trace_orbit",        1, "calx_aliquot_step",         1),
    ("calx_trace_orbit",        1, "calx_arithmetic_derivative",1),
    ("calx_trace_orbit",        1, "calx_signature_step",       1),
    ("calx_trace_orbit",        1, "calx_radical_step",         1),
]


def _register_sqlite(path: Path) -> dict[str, int]:
    counts = {"inserted": 0, "skipped": 0}
    if not path.is_file():
        print(f"  warning: SQLite store not found at {path} — skipping", file=sys.stderr)
        return counts

    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    try:
        for fn, fv, dep_fn, dep_fv in FUNCTION_DEPS:
            exists = db.execute(
                """SELECT 1 FROM function_dependencies
                    WHERE function_name = ?
                      AND function_version = ?
                      AND depends_on_function_name = ?
                      AND depends_on_function_version = ?""",
                (fn, fv, dep_fn, dep_fv),
            ).fetchone()
            if exists:
                counts["skipped"] += 1
                continue
            db.execute(
                """INSERT INTO function_dependencies
                       (function_name, function_version,
                        depends_on_function_name, depends_on_function_version)
                   VALUES (?, ?, ?, ?)""",
                (fn, fv, dep_fn, dep_fv),
            )
            counts["inserted"] += 1
        db.commit()
    finally:
        db.close()
    return counts


def _register_postgres(dsn: str) -> dict[str, int]:
    counts = {"inserted": 0, "skipped": 0}
    with psycopg.connect(dsn) as pg:
        with pg.cursor() as cur:
            for fn, fv, dep_fn, dep_fv in FUNCTION_DEPS:
                cur.execute(
                    """SELECT 1 FROM curry.function_dependencies
                        WHERE function_name = %s
                          AND function_version = %s
                          AND depends_on_function_name = %s
                          AND depends_on_function_version = %s""",
                    (fn, fv, dep_fn, dep_fv),
                )
                if cur.fetchone():
                    counts["skipped"] += 1
                    continue
                cur.execute(
                    """INSERT INTO curry.function_dependencies
                           (function_name, function_version,
                            depends_on_function_name, depends_on_function_version)
                       VALUES (%s, %s, %s, %s)""",
                    (fn, fv, dep_fn, dep_fv),
                )
                counts["inserted"] += 1
    return counts


def main() -> int:
    print(f"SQLite ({SQLITE_PATH}):")
    sq = _register_sqlite(SQLITE_PATH)
    print(f"  inserted: {sq['inserted']}  skipped: {sq['skipped']}")

    print(f"Postgres ({PG_DSN}):")
    pg = _register_postgres(PG_DSN)
    print(f"  inserted: {pg['inserted']}  skipped: {pg['skipped']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
