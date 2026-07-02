"""Schema apply-order tests (calx.db.schema_order / is_numbered_sql).

The core SQL numbering crossed into three digits when the two-digit space
filled at 99. Plain lexical filename sort breaks there ('100_...' sorts
before '10_curry.sql'), so every apply path orders by (numeric prefix,
remainder) instead: calx.db for `trunkit init` / `--local` extensions, and
`LC_ALL=C sort -n` in the Makefile / CI psql loops. These tests pin that
contract. DB-free.
"""

from __future__ import annotations

from calx.db import SCHEMA_FILES, UNIFIED_FILES, is_numbered_sql, schema_order


def test_three_digit_prefixes_apply_after_two_digit():
    names = [
        "100_kan_next_engine.sql",
        "10_curry.sql",
        "99_cert_vacuity.sql",
        "00_rehome_to_calx.sql",
        "41a_cert_formal_lean.sql",
        "41_cert_formal.sql",
        "42_cert_gap_homology.sql",
    ]
    assert sorted(names, key=schema_order) == [
        "00_rehome_to_calx.sql",
        "10_curry.sql",
        "41_cert_formal.sql",       # base number before its letter suffix
        "41a_cert_formal_lean.sql",
        "42_cert_gap_homology.sql",
        "99_cert_vacuity.sql",
        "100_kan_next_engine.sql",  # after 99, not before 10
    ]


def test_numbered_sql_filter():
    assert is_numbered_sql("00_rehome_to_calx.sql")
    assert is_numbered_sql("41a_cert_formal_lean.sql")
    assert is_numbered_sql("100_kan_next_engine.sql")
    assert not is_numbered_sql("1_too_short.sql")
    assert not is_numbered_sql("examples")
    assert not is_numbered_sql("10_notes.txt")
    assert not is_numbered_sql("README.sql.bak")


def test_unified_files_are_in_numeric_order():
    assert UNIFIED_FILES, "no schema files discovered"
    keys = [schema_order(n) for n in UNIFIED_FILES]
    assert keys == sorted(keys)
    assert UNIFIED_FILES[0] == "00_rehome_to_calx.sql"
    assert all(is_numbered_sql(n) for n in UNIFIED_FILES)


def test_base_schema_files_lead_the_unified_order():
    # the calx bedrock (00-07) must apply before everything else
    assert list(UNIFIED_FILES[: len(SCHEMA_FILES)]) == list(SCHEMA_FILES)
