"""Database connection helpers.

DSN resolution order:
  1. explicit ``dsn`` argument
  2. ``CALX_DSN`` env var
  3. the running docker-compose default
     (postgresql://trunk:trunk@localhost:5434/trunk)

The database hosts four sibling schemas — ``calx`` (integer/arithmetic data
and routines), ``curry`` (versioned-fact store), ``kan`` (schema-as-category
metadata), ``cert`` (proof-carrying attestation) — all reflected into ``kan``
by ``kan.sync_category``.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg import Connection

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"

# calx-only DDL (unqualified names; resolve to the `calx` schema via search_path)
SCHEMA_FILES = (
    "01_schema.sql",
    "02_views.sql",
    "03_generate.sql",
    "04_crt.sql",
    "05_dynamics.sql",
    "06_oeis_match.sql",
    "07_compositions.sql",
)

# Full unified bootstrap, in order: schemas + re-home, calx DDL, curry, kan, functors.
UNIFIED_FILES = (
    "00_rehome_to_calx.sql",
    *SCHEMA_FILES,
    "10_curry.sql",
    "20_kan.sql",
    "21_kan_functors.sql",
    "22_kan_elements.sql",
    "23_kan_monoidal.sql",
    "24_kan_natural_transformations.sql",
    "25_kan_extensions.sql",
    "26_kan_enrichment.sql",
    "27_kan_profunctors.sql",
    "28_kan_adjunctions.sql",
    "30_kan_corpus.sql",
    "40_cert.sql",
    "41_cert_formal.sql",
    "42_cert_gap_homology.sql",
    "43_kan_sequence_homology.sql",
    "44_cert_seq_homology.sql",
    "45_kan_factorial_homology.sql",
    "46_cert_factorial_homology.sql",
    "47_kan_combined_signature.sql",
    "48_cert_combined.sql",
    "49_kan_shared_prime_betti.sql",
    "50_cert_combined_scale.sql",
    "51_cert_shared_prime_h2.sql",
    "52_cert_developed_sequence.sql",
    "53_cert_omega_family.sql",
    "54_cert_omega_family_succ.sql",
    "55_kan_prime_members.sql",
    "56_cert_prime_members_functor.sql",
    "57_kan_strata_tower.sql",
    "58_cert_strata_tower.sql",
    "59_kan_grading.sql",
    "60_cert_grading.sql",
    "61_kan_identity_decomposition.sql",
    "62_cert_identity_decomposition.sql",
    "63_kan_bigrading.sql",
    "64_cert_bigrading.sql",
    "65_kan_chromatic.sql",
    "66_cert_chromatic.sql",
    "67_kan_lithon.sql",
    "68_cert_lithon.sql",
    "69_kan_shadow.sql",
    "70_cert_shadow.sql",
    "71_kan_self_syzygy.sql",
    "72_cert_self_syzygy.sql",
    "73_kan_self_shadow.sql",
    "74_cert_self_shadow.sql",
    "75_kan_f1_radix.sql",
    "76_cert_f1_radix.sql",
    "77_kan_moonshine.sql",
    "78_cert_moonshine.sql",
    "79_cert_kan_engines.sql",
    "80_kan_colimit_closure.sql",
    "81_cert_colimit_closure.sql",
    "82_kan_equipment.sql",
    "83_cert_equipment.sql",
    "84_cert_witness.sql",
    "85_cert_derivation.sql",
    "86_cert_verify.sql",
    "87_cert_export_bundle.sql",
    "88_cert_witness_carry.sql",
)

# Applied per-session so a fresh-DB bootstrap creates calx objects in `calx`
# (ALTER ROLE in 00_rehome only affects *future* sessions).
SEARCH_PATH = "calx, curry, kan, public"

DEFAULT_DSN = "postgresql://trunk:trunk@localhost:5434/trunk"


def resolve_dsn(dsn: str | None = None) -> str:
    if dsn:
        return dsn
    env = os.environ.get("CALX_DSN")
    if env:
        return env
    return DEFAULT_DSN


@contextmanager
def connect(dsn: str | None = None, *, autocommit: bool = False) -> Iterator[Connection]:
    conn = psycopg.connect(resolve_dsn(dsn), autocommit=autocommit)
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


def apply_schema(conn: Connection, files: tuple[str, ...] = SCHEMA_FILES) -> None:
    """Execute the calx DDL files (schema, views, procedures, CRT, dynamics).

    Idempotent — every statement uses ``CREATE OR REPLACE`` or ``IF NOT EXISTS``.
    Sets the session search_path first so unqualified objects land in ``calx``.
    """
    with conn.cursor() as cur:
        cur.execute(f"SET search_path = {SEARCH_PATH}")
        for fname in files:
            path = SQL_DIR / fname
            cur.execute(path.read_text(encoding="utf-8"))


def apply_unified(conn: Connection, *, sync_kan: bool = True) -> None:
    """Bootstrap the full unified model: schemas, calx, curry, kan.

    Safe on both a fresh database and the already-migrated live one
    (00_rehome's DO-loops are no-ops once ``public`` is empty).
    """
    with conn.cursor() as cur:
        for fname in UNIFIED_FILES:
            path = SQL_DIR / fname
            cur.execute(path.read_text(encoding="utf-8"))
            if fname == "00_rehome_to_calx.sql":
                cur.execute(f"SET search_path = {SEARCH_PATH}")
        if sync_kan:
            for cat in ("calx", "curry", "kan"):
                cur.execute("SELECT kan.sync_category(%s, %s)", (cat, cat))
            cur.execute("SELECT * FROM kan.populate_curry_calx_functor()")
