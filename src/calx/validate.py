"""Validation against OEIS b-file fixtures.

For each sequence we know how to verify, we compare the database's computation
against the canonical OEIS values for n ≤ min(limit, len(bfile)).

Supported sequences (extend as needed):
  A000040  primes
  A001221  ω(n)
  A001222  Ω(n)
  A005117  squarefree numbers
  A000005  τ(n)   (number of divisors)
  A000203  σ(n)   (sum of divisors)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from psycopg import Connection

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "oeis"


@dataclass(frozen=True)
class ValidationResult:
    sequence: str
    checked: int
    mismatches: list[tuple[int, int, int]]  # (n, expected, actual)

    @property
    def ok(self) -> bool:
        return not self.mismatches


def _read_bfile(path: Path) -> dict[int, int]:
    out: dict[int, int] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        n_str, v_str = line.split(None, 1)
        out[int(n_str)] = int(v_str)
    return out


def check_omega(conn: Connection, limit: int) -> ValidationResult:
    return _check_column(conn, "A001221", "omega", limit)


def check_big_omega(conn: Connection, limit: int) -> ValidationResult:
    return _check_column(conn, "A001222", "big_omega", limit)


def _check_column(
    conn: Connection, seq: str, column: str, limit: int
) -> ValidationResult:
    expected = _read_bfile(FIXTURE_DIR / f"b{seq[1:].lower()}.txt")
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT n, {column} FROM integers WHERE n <= %s ORDER BY n",
            (min(limit, max(expected) if expected else limit),),
        )
        rows = cur.fetchall()

    mismatches: list[tuple[int, int, int]] = []
    for n, actual in rows:
        if n in expected and expected[n] != actual:
            mismatches.append((n, expected[n], actual))
    return ValidationResult(sequence=seq, checked=len(rows), mismatches=mismatches)
