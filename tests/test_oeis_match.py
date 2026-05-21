"""OEIS orbit prefix matching — unit tests with mocked HTTP."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from calx import generate


@pytest.fixture(scope="module")
def populated(_initialized_calx_db):
    from calx import db as _db

    with _db.connect(_initialized_calx_db) as c:
        with c.cursor() as cur:
            cur.execute(
                "TRUNCATE factorizations, primes, integers, "
                "sequences, sequence_membership, integer_relations, "
                "orbits, oeis_match_candidates "
                "RESTART IDENTITY CASCADE"
            )
            cur.execute("ALTER SEQUENCE orbit_id_seq RESTART WITH 1")
        generate.generate_pure(c, 1000)
        yield c


def _load_oeis_match():
    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "tools" / "oeis_match.py"
    spec = importlib.util.spec_from_file_location("oeis_match", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["oeis_match"] = mod
    spec.loader.exec_module(mod)
    return mod


FIB_JSON = [
    {
        "number": 45,
        "offset": "0",
        "data": "0,1,1,2,3,5,8,13,21,34,55",
        "name": "Fibonacci numbers.",
    },
    {
        "number": 1,
        "data": "1,2,3,4,5,6,7,8,9,10",
        "name": "The natural numbers.",
    },
]

AMICABLE_JSON = [
    {
        "number": 63990,
        "offset": "1",
        "data": "220,284,1184,1210,2620,2924,5020,5564",
        "name": "Amicable numbers.",
    },
    {
        "number": 45,
        "offset": "0",
        "data": "0,1,1,2,3,5,8,13,21,34,55",
        "name": "Fibonacci numbers.",
    },
]

ALIQUOT_TAUTO_JSON = [
    {
        "number": 143090,
        "offset": "1",
        "data": "12,16,15,9,4,3,1",
        "name": "Aliquot sequence starting at 12.",
    },
    {
        "number": 54753,
        "offset": "1",
        "data": "12,18,20,28,44,45,50",
        "name": "Numbers which are the product of a prime and the square of another prime.",
    },
]

def test_fibonacci_requires_offset_alignment():
    om = _load_oeis_match()
    # Offset 0: no match; offset 1 aligns with standard Fib indexing.
    strict = om.score_alignment(
        [1, 1, 2, 3],
        [0, 1, 1, 2, 3],
        orbit_start=1,
        oeis_id="A000045",
        oeis_name="Fibonacci",
        oeis_offset=0,
    )
    assert strict.offset == 1
    assert strict.matched == 4

    align = om.score_alignment(
        [1, 1, 2, 3, 5],
        [0, 1, 1, 2, 3, 5, 8],
        orbit_start=1,
        oeis_id="A000045",
        oeis_name="Fibonacci numbers.",
        oeis_offset=0,
    )
    assert align.matched == 5
    assert align.match_kind == "identification"

    ranked = om.score_hits([0, 1, 1, 2, 3, 5, 8], FIB_JSON, orbit_start=0)
    assert ranked[0]["oeis_id"] == "A000045"
    assert ranked[0]["match_kind"] == "identification"


def test_amicable_beats_fibonacci_for_220_284():
    om = _load_oeis_match()
    query = [220, 284, 1184, 1210]
    ranked = om.score_hits(query, AMICABLE_JSON, orbit_start=220)
    assert ranked[0]["oeis_id"] != "A000045"
    assert ranked[0]["oeis_id"] == "A063990"
    assert ranked[0]["match_kind"] == "identification"
    assert ranked[0]["confidence"] >= 0.9


def test_tautological_aliquot_is_capped_and_demoted():
    om = _load_oeis_match()
    align = om.score_alignment(
        [12, 16, 15, 9, 4],
        [12, 16, 15, 9, 4, 3, 1],
        orbit_start=12,
        oeis_id="A143090",
        oeis_name="Aliquot sequence starting at 12.",
        oeis_offset=1,
    )
    assert align.match_kind == "tautology"
    assert align.confidence <= om.TAUTOLOGY_CONFIDENCE_CAP

    ranked = om.score_hits([12, 18, 20, 28], ALIQUOT_TAUTO_JSON, orbit_start=12)
    assert ranked[0]["oeis_id"] == "A054753"
    assert ranked[0]["match_kind"] == "identification"


def test_indexed_trajectory_kind_any_start_in_name():
    om = _load_oeis_match()
    assert om.indexed_trajectory_kind("Aliquot sequence starting at 38.", 22) == "coincidence"
    assert om.indexed_trajectory_kind("Aliquot sequence starting at 12.", 12) == "tautology"


def test_short_orbit_prefix_cannot_identify():
    om = _load_oeis_match()
    ranked = om.score_hits([220, 284], AMICABLE_JSON + FIB_JSON, orbit_start=220)
    assert all(r["match_kind"] != "identification" for r in ranked)
    amicable = next(r for r in ranked if r["oeis_id"] == "A063990")
    assert amicable["match_kind"] in ("suggestive", "coincidence")


def test_metadata_sequence_cannot_identify():
    om = _load_oeis_match()
    ranked = om.score_hits(
        [1, 0, 7, 1, 8, 5],
        [
            {
                "number": 20863,
                "data": "1,0,7,1,8,5,2,9,7,5,0,9,8,7,6,3,2,0,8,3",
                "name": "Decimal expansion of log_2(11).",
            }
        ],
        orbit_start=1,
    )
    assert ranked[0]["match_kind"] == "coincidence"


def test_wrong_indexed_aliquot_is_coincidence_not_identification():
    om = _load_oeis_match()
    ranked = om.score_hits(
        [38, 39, 20, 10],  # prefix of aliquot(38), not aliquot(22)
        [
            {
                "number": 143721,
                "data": "38,39,20,10,12,14,7,8,4,3,1",
                "name": "Aliquot sequence starting at 38.",
            }
        ],
        orbit_start=22,
    )
    assert ranked[0]["match_kind"] == "coincidence"
    assert ranked[0]["confidence"] <= 0.45


def test_search_orbit_persists_and_caches(populated):
    om = _load_oeis_match()
    with populated.cursor() as cur:
        cur.execute("CALL trace_orbit(%s, 'ALIQUOT', %s)", (220, 10))
        cur.execute("SELECT MAX(orbit_id) FROM orbits")
        oid = cur.fetchone()[0]

    fib_prefix = [0, 1, 1, 2, 3, 5, 8]
    payload = {
        "results": FIB_JSON,
        "prefix": fib_prefix,
        "prefix_hash": om.prefix_hash(fib_prefix),
    }

    with patch.object(om, "orbit_prefix", return_value=fib_prefix):
        with patch.object(om, "fetch_oeis_search", return_value=payload) as fetch:
            hits1 = om.search_orbit(populated, oid, prefix_len=7, sync_membership=False)
            hits2 = om.search_orbit(populated, oid, prefix_len=7, sync_membership=False)
            fetch.assert_called_once()

    assert len(hits1) >= 1
    assert hits1[0].oeis_id == "A000045"
    assert hits1[0].match_kind == "identification"

    with populated.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM oeis_match_candidates WHERE orbit_id = %s",
            (oid,),
        )
        assert cur.fetchone()[0] >= 1


def test_characterize_relation_shared_orbit_with_oeis(populated):
    om = _load_oeis_match()
    with populated.cursor() as cur:
        cur.execute("CALL trace_orbit(%s, 'ALIQUOT', %s)", (220, 10))
        cur.execute("SELECT MAX(orbit_id) FROM orbits")
        oid = cur.fetchone()[0]

    with patch.object(
        om,
        "fetch_oeis_search",
        return_value={
            "results": [],
            "prefix": [220, 284],
            "prefix_hash": om.prefix_hash([220, 284]),
        },
    ):
        om.search_orbit(populated, oid, prefix_len=2, sync_membership=False)

    with populated.cursor() as cur:
        cur.execute(
            """
            INSERT INTO oeis_match_candidates
                (orbit_id, candidate_id, oeis_id, oeis_name, prefix_len, confidence, raw_payload)
            VALUES (%s, 1, 'A063769', 'Amicable pair orbit', 2, 0.95, '{}'::jsonb)
            ON CONFLICT (orbit_id, candidate_id) DO UPDATE
              SET oeis_id = EXCLUDED.oeis_id,
                  confidence = EXCLUDED.confidence
            """,
            (oid,),
        )
        cur.execute(
            "SELECT rel_type, rel_params->>'oeis_match' "
            "FROM characterize_relation(220, 284)"
        )
        rows = cur.fetchall()

    types = {r[0] for r in rows}
    assert "SHARED_ORBIT_ID" in types
    oeis_hits = [r[1] for r in rows if r[0] == "SHARED_ORBIT_ID"]
    assert "A063769" in oeis_hits


def test_matches_for(populated):
    om = _load_oeis_match()
    with populated.cursor() as cur:
        cur.execute("CALL trace_orbit(%s, 'ALIQUOT', %s)", (6, 5))
        cur.execute("SELECT MAX(orbit_id) FROM orbits")
        oid = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO oeis_match_candidates
                (orbit_id, candidate_id, oeis_id, oeis_name, prefix_len, confidence, raw_payload)
            VALUES (%s, 1, 'A000396', 'Perfect numbers', 1, 1.0, '{}'::jsonb)
            """,
            (oid,),
        )

    found = om.matches_for(populated, 6)
    assert any(x[0] == oid and x[1] == "A000396" for x in found)
