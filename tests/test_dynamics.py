"""Dynamical-layer correctness tests.

Covers the step functions, orbit tracer (cycles + fixed points), and
characterize_relation. Requires the populated_module fixture from
test_factorizations.py via _initialized_db.
"""

from __future__ import annotations

import pytest

from calx import generate


@pytest.fixture(scope="module")
def populated(_initialized_db):
    from calx import db as _db

    with _db.connect(_initialized_db) as c:
        with c.cursor() as cur:
            cur.execute(
                "TRUNCATE factorizations, primes, integers, "
                "sequences, sequence_membership, integer_relations, orbits "
                "RESTART IDENTITY CASCADE"
            )
            cur.execute("ALTER SEQUENCE orbit_id_seq RESTART WITH 1")
        generate.generate_pure(c, 1000)
        yield c


def _scalar(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


@pytest.mark.parametrize(
    "n, expected",
    [
        (6, 6),         # perfect → fixed point of s(n)
        (28, 28),       # perfect
        (12, 16),       # σ(12)=28, s(12)=16
        (220, 284),     # amicable
        (284, 220),     # amicable
    ],
)
def test_aliquot_step(populated, n, expected):
    assert _scalar(populated, "SELECT aliquot_step(%s)", n) == expected


@pytest.mark.parametrize(
    "n, expected",
    [
        (1, 0),
        (2, 1),
        (3, 1),
        (7, 1),
        (4, 4),         # D(2²) = 2·2 = 4
        (12, 16),       # D(2²·3) = 4·3 + 4·1 = 16
        (15, 8),        # D(3·5) = 5 + 3 = 8
        (30, 31),       # D(2·3·5) = 15 + 10 + 6 = 31
    ],
)
def test_arithmetic_derivative(populated, n, expected):
    assert _scalar(populated, "SELECT arithmetic_derivative(%s)", n) == expected


@pytest.mark.parametrize(
    "n, expected",
    [
        (12, 18),       # both shape [2,1]
        (18, 20),       # 20 = 2²·5, also [2,1]
        (8, 27),        # 8=2³, 27=3³, both [3]
    ],
)
def test_signature_step(populated, n, expected):
    assert _scalar(populated, "SELECT signature_step(%s)", n) == expected


@pytest.mark.parametrize(
    "n, expected",
    [
        (12, 6),        # rad(2²·3) = 6
        (72, 6),        # rad(2³·3²) = 6
        (30, 30),       # squarefree → fixed point
        (97, 97),       # prime → fixed point
        (1000, 10),     # rad(2³·5³) = 10
    ],
)
def test_radical_step(populated, n, expected):
    assert _scalar(populated, "SELECT radical_step(%s)", n) == expected


def test_aliquot_orbit_220_is_2_cycle(populated):
    """220 → 284 → 220 (amicable pair = period-2 orbit)."""
    with populated.cursor() as cur:
        cur.execute("CALL trace_orbit(%s, 'ALIQUOT', %s)", (220, 20))
        cur.execute(
            "SELECT step, n, cycle_close FROM orbits "
            "WHERE orbit_id = (SELECT MAX(orbit_id) FROM orbits) "
            "ORDER BY step"
        )
        rows = cur.fetchall()

    ns = [r[1] for r in rows]
    closes = [r[2] for r in rows]
    assert ns[:2] == [220, 284]
    assert any(closes), "no cycle_close marker on a known amicable orbit"


def test_aliquot_orbit_6_is_fixed_point(populated):
    with populated.cursor() as cur:
        cur.execute("CALL trace_orbit(%s, 'ALIQUOT', %s)", (6, 5))
        cur.execute(
            "SELECT n, cycle_close FROM orbits "
            "WHERE orbit_id = (SELECT MAX(orbit_id) FROM orbits) "
            "ORDER BY step"
        )
        rows = cur.fetchall()

    assert rows[0] == (6, True)


def test_radical_orbit_72_collapses_in_one_step(populated):
    with populated.cursor() as cur:
        cur.execute("CALL trace_orbit(%s, 'RADICAL', %s)", (72, 5))
        cur.execute(
            "SELECT step, n FROM orbits "
            "WHERE orbit_id = (SELECT MAX(orbit_id) FROM orbits) "
            "ORDER BY step"
        )
        rows = cur.fetchall()

    assert rows[0] == (0, 72)
    assert rows[1][1] == 6
    # 6 is squarefree → fixed under radical → cycle_close fires at the second hit


def test_characterize_relation_12_18(populated):
    """12 and 18 share signature shape [2,1] and gcd=6."""
    with populated.cursor() as cur:
        cur.execute("SELECT rel_type FROM characterize_relation(12, 18)")
        types = {r[0] for r in cur.fetchall()}

    assert "SIGNATURE_TWIN" in types
    assert "COMMON_FACTOR" in types
    assert "OMEGA_EQUAL" in types
    assert "BIG_OMEGA_EQUAL" in types
    assert "CRT_CLASS" in types  # both ≡ 0 (mod 2), both ≡ 0 (mod 6)


def test_characterize_relation_360_divides_720(populated):
    with populated.cursor() as cur:
        cur.execute("SELECT rel_type FROM characterize_relation(720, 360)")
        types = {r[0] for r in cur.fetchall()}

    assert "DIVISOR" in types  # 360 divides 720


def test_arith_deriv_orbit_exits_when_out_of_range(populated):
    """Regression: arithmetic-derivative orbit from 12 escapes a 1000-range DB.
    trace_orbit must clamp `lim` to MAX(integers.n) so the FK never fires."""
    with populated.cursor() as cur:
        cur.execute("CALL trace_orbit(%s, 'ARITH_DERIV', %s)", (12, 20))
        cur.execute(
            "SELECT n FROM orbits "
            "WHERE orbit_id = (SELECT MAX(orbit_id) FROM orbits) "
            "ORDER BY step"
        )
        ns = [r[0] for r in cur.fetchall()]

    assert ns[0] == 12
    assert all(n <= 1000 for n in ns), f"orbit stored out-of-range n: {ns}"


def test_crt_class_neighbors_depth_shrinks(populated):
    """Higher depth → fewer neighbors in the same range."""
    counts = []
    for depth in (1, 2, 3, 4):
        with populated.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM crt_class_neighbors(%s, %s) WHERE distance <= 200",
                (100, depth),
            )
            counts.append(cur.fetchone()[0])
    assert counts == sorted(counts, reverse=True), f"non-monotone: {counts}"
