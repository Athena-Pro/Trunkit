"""The cert.witness kind vocabulary has exactly two authoritative definitions —
calx sql/84_cert_witness.sql (owns the table) and nerode sql/00_bootstrap.sql
(the co-location stub) — which must stay identical, and no other schema file may
touch the constraint.

History: five files once did drop/re-add surgery on this constraint with five
different kind lists, so whichever applied last rejected another layer's
legitimate writes, and a populated ledger refused the next schema apply
outright. These tests pin the invariant that prevents a recurrence. DB-free.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CALX_SQL = REPO / "src" / "calx" / "sql"
NERODE_SQL = REPO / "src" / "nerode" / "sql"

AUTHORITIES = (
    CALX_SQL / "84_cert_witness.sql",
    NERODE_SQL / "00_bootstrap.sql",
)

# Every kind any writer in the repo records (see METHODS.md / the bridge files).
EXPECTED_KINDS = {
    "term", "trace", "counterexample", "hash_chain", "kan_diagram",
    "arith_constraint", "snark_proof",
    "construction_record", "computation_trace",
    "nerode_partition", "bisimulation", "state_map",
    "betti",
}

_CONSTRAINT_RE = re.compile(
    r"ADD CONSTRAINT cert_witness_kind_check CHECK \(kind IN \((?P<body>.*?)\)\)",
    re.DOTALL,
)


def constraint_kinds(path: Path) -> set[str]:
    m = _CONSTRAINT_RE.search(path.read_text(encoding="utf-8"))
    assert m, f"{path.name}: canonical cert_witness_kind_check block not found"
    return set(re.findall(r"'([a-z_]+)'", m.group("body")))


def test_authoritative_lists_are_identical():
    calx_kinds, nerode_kinds = (constraint_kinds(p) for p in AUTHORITIES)
    assert calx_kinds == nerode_kinds


def test_authoritative_list_covers_every_writer():
    for path in AUTHORITIES:
        assert constraint_kinds(path) == EXPECTED_KINDS


def test_no_other_file_touches_the_constraint():
    offenders = []
    for sql_dir in (CALX_SQL, NERODE_SQL):
        for path in sorted(sql_dir.glob("*.sql")):
            if path in AUTHORITIES:
                continue
            text = path.read_text(encoding="utf-8")
            if re.search(r"(ADD|DROP) CONSTRAINT.*witness_kind_check", text):
                offenders.append(f"{sql_dir.name}/{path.name}")
    assert not offenders, (
        "cert_witness_kind_check may only be defined in 84_cert_witness.sql / "
        f"00_bootstrap.sql; also found in: {offenders}"
    )
