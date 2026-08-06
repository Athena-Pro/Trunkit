"""Tests for exact comb geometry (112) and its Python mirror.

The unit tests pin the field arithmetic without a database. The DB tests pin
the probes. The section that matters most is the last one, which computes the
same squared distances in SQL and in calx.numberfield and diffs them: two
statements of one arithmetic are only worth having if something checks they
agree, and a silent divergence here would mean a consumer verifying a
counterexample offline and getting a different answer from the ledger.

Two worked configurations recur. The unit square over Q realises C4 faithfully
and has its diagonals at squared distance 2. The equilateral triangle over
Q(sqrt3) -- (0,0), (1,0), (1/2, sqrt3/2) -- realises K3 with every side at
squared distance exactly 1, which is the smallest case that genuinely needs the
field and not just fractions.

Skips cleanly when no test DSN is set.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from calx import numberfield as nf

# x^2 - 3, ascending and monic. Q itself is m(x) = x.
SQRT3 = (-3, 0, 1)
RATIONALS = (0, 1)


# --- unit: the field arithmetic ---------------------------------------------

def test_reduction_rewrites_alpha_squared():
    # x^2 = 3 in Q(sqrt3)
    assert nf.reduce_mod([0, 0, 1], SQRT3) == [3, 0]
    # x^3 = 3x
    assert nf.reduce_mod([0, 0, 0, 1], SQRT3) == [0, 3]


def test_reduction_pads_short_inputs():
    assert nf.reduce_mod([5], SQRT3) == [5, 0]


def test_a_minimal_polynomial_must_be_monic():
    with pytest.raises(ValueError, match="monic"):
        nf.reduce_mod([1], (-3, 0, 2))


def test_a_minimal_polynomial_needs_a_degree():
    with pytest.raises(ValueError, match="degree"):
        nf.reduce_mod([1], (1,))


def test_elements_are_canonical():
    assert nf.Elem.of([2, 4], 6) == nf.Elem.of([1, 2], 3)
    assert nf.Elem.of([1, 0], -2) == nf.Elem.of([-1, 0], 2)   # sign in the numerator
    assert nf.Elem.of([0, 0], 7) == nf.Elem.of([0, 0], 1)     # one zero, not many


def test_a_zero_denominator_is_refused():
    with pytest.raises(ValueError, match="zero denominator"):
        nf.Elem.of([1], 0)


def test_floats_cannot_enter_the_field():
    """The one thing this module exists to make impossible."""
    with pytest.raises(ValueError, match="integers"):
        nf.Elem.of([0.5], 1)


def test_alpha_squared_is_three():
    alpha = nf.Elem.of([0, 1])
    assert nf.mul(alpha, alpha, SQRT3) == nf.Elem.of([3, 0], 1)


def test_multiplication_clears_denominators():
    half_alpha = nf.Elem.of([0, 1], 2)          # sqrt3 / 2
    assert nf.mul(half_alpha, half_alpha, SQRT3) == nf.Elem.of([3, 0], 4)


def test_the_unit_square_has_exact_sides_and_diagonals():
    p = [[nf.rational(0), nf.rational(0)], [nf.rational(1), nf.rational(0)],
         [nf.rational(1), nf.rational(1)], [nf.rational(0), nf.rational(1)]]
    assert nf.sq_distance(p[0], p[1], RATIONALS) == nf.Elem.of([1], 1)
    assert nf.sq_distance(p[0], p[2], RATIONALS) == nf.Elem.of([2], 1)


def test_half_integer_coordinates_stay_fractions():
    a = [nf.rational(0), nf.rational(0)]
    b = [nf.rational(1, 2), nf.rational(1, 2)]
    assert nf.sq_distance(a, b, RATIONALS) == nf.Elem.of([1], 2)


def test_the_equilateral_triangle_has_three_unit_sides():
    """The smallest configuration that needs the field rather than fractions."""
    zero, one = nf.Elem.of([0, 0]), nf.Elem.of([1, 0])
    a = [zero, zero]
    b = [one, zero]
    c = [nf.Elem.of([1, 0], 2), nf.Elem.of([0, 1], 2)]      # (1/2, sqrt3/2)
    unit = nf.Elem.of([1, 0], 1)
    assert nf.sq_distance(a, b, SQRT3) == unit
    assert nf.sq_distance(b, c, SQRT3) == unit
    assert nf.sq_distance(a, c, SQRT3) == unit


def test_points_must_agree_in_dimension():
    with pytest.raises(ValueError, match="dimension"):
        nf.sq_distance([nf.rational(0)], [nf.rational(0), nf.rational(1)], RATIONALS)


# --- DB fixtures ------------------------------------------------------------

def _dsn():
    dsn = os.environ.get("CALX_TEST_DSN") or os.environ.get("ARITHMETIC_DB_TEST_DSN")
    if not dsn:
        pytest.skip("No test DSN provided. Refusing to write to default/production ledger.")
    return dsn


@pytest.fixture()
def conn():
    try:
        c = psycopg.connect(_dsn(), connect_timeout=3)
    except psycopg.Error as exc:
        pytest.skip(f"test DB not reachable: {exc}")
    c.autocommit = True
    try:
        with c.cursor() as cur:
            cur.execute("SELECT to_regclass('comb.configuration')")
            if cur.fetchone()[0] is None:
                pytest.skip("comb.configuration missing — apply 112_comb_geometry.sql")
        yield c
    finally:
        c.close()


def _config(cur, dim, domain, field=None) -> str:
    subject = f"test-config-{uuid.uuid4()}"
    cur.execute("SELECT comb.register_configuration(%s, %s, %s, %s)",
                (subject, dim, domain, field))
    return subject


def _structure(cur, kind, n) -> str:
    subject = f"test-{kind}-{uuid.uuid4()}"
    cur.execute("SELECT (comb.register_structure(%s, %s, %s)).id", (subject, kind, n))
    return subject


def _rational_points(cur, config, points):
    for idx, coords in enumerate(points):
        cur.execute("SELECT comb.add_rational_point(%s, %s, %s::numeric[])",
                    (config, idx, list(coords)))


def _sq(cur, config, i, j) -> nf.Elem:
    cur.execute("SELECT (comb.sq_distance(%s, %s, %s)).num,"
                "       (comb.sq_distance(%s, %s, %s)).den",
                (config, i, j, config, i, j))
    num, den = cur.fetchone()
    return nf.Elem(tuple(int(x) for x in num), int(den))


def _probe(cur, sql, *args):
    cur.execute(f"SELECT ok, evidence FROM {sql}", args)
    return cur.fetchone()


def _unit_square(cur) -> str:
    config = _config(cur, 2, "rational")
    _rational_points(cur, config, [(0, 0), (1, 0), (1, 1), (0, 1)])
    return config


def _c4(cur) -> str:
    subject = _structure(cur, "graph", 4)
    for u, v in ((0, 1), (1, 2), (2, 3), (0, 3)):
        cur.execute("SELECT comb.add_edge(%s, %s, %s)", (subject, u, v))
    return subject


def _equilateral(cur) -> str:
    cur.execute("SELECT comb.register_field(%s, %s::numeric[])",
                ("Q(sqrt3)", [-3, 0, 1]))
    config = _config(cur, 2, "algebraic", "Q(sqrt3)")
    coords = {
        (0, 0): ([0, 0], 1), (0, 1): ([0, 0], 1),
        (1, 0): ([1, 0], 1), (1, 1): ([0, 0], 1),
        (2, 0): ([1, 0], 2), (2, 1): ([0, 1], 2),      # (1/2, sqrt3/2)
    }
    for (point, axis), (num, den) in coords.items():
        cur.execute("SELECT comb.set_coordinate(%s, %s, %s, %s::numeric[], %s)",
                    (config, point, axis, num, den))
    return config


# --- registration guards ----------------------------------------------------

def test_a_field_must_be_monic(conn):
    with conn.cursor() as cur, pytest.raises(psycopg.Error, match="monic"):
        cur.execute("SELECT comb.register_field(%s, %s::numeric[])",
                    (f"bad-{uuid.uuid4()}", [-3, 0, 2]))


def test_field_coefficients_must_be_integers(conn):
    with conn.cursor() as cur, pytest.raises(psycopg.Error, match="must be integers"):
        cur.execute("SELECT comb.register_field(%s, %s::numeric[])",
                    (f"bad-{uuid.uuid4()}", ["-0.5", "0", "1"]))


def test_an_algebraic_configuration_needs_a_field(conn):
    with conn.cursor() as cur, pytest.raises(psycopg.Error):
        cur.execute("SELECT comb.register_configuration(%s, 2, 'algebraic', NULL)",
                    (f"test-config-{uuid.uuid4()}",))


def test_a_rational_configuration_may_not_carry_one(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT comb.register_field(%s, %s::numeric[])",
                    ("Q(sqrt3)", [-3, 0, 1]))
        with pytest.raises(psycopg.Error):
            cur.execute(
                "SELECT comb.register_configuration(%s, 2, 'rational', 'Q(sqrt3)')",
                (f"test-config-{uuid.uuid4()}",))


def test_an_axis_outside_the_dimension_is_refused(conn):
    with conn.cursor() as cur:
        config = _config(cur, 2, "rational")
        with pytest.raises(psycopg.Error, match="axis 2 outside"):
            cur.execute("SELECT comb.set_coordinate(%s, 0, 2, %s::numeric[], 1)",
                        (config, [1]))


def test_a_missing_coordinate_is_reported_not_assumed_zero(conn):
    with conn.cursor() as cur:
        config = _config(cur, 2, "rational")
        cur.execute("SELECT comb.set_coordinate(%s, 0, 0, %s::numeric[], 1)",
                    (config, [1]))
        with pytest.raises(psycopg.Error, match="no coordinate"):
            cur.execute("SELECT comb.sq_distance(%s, 0, 0)", (config,))


# --- exactness --------------------------------------------------------------

def test_decimal_input_becomes_an_exact_fraction(conn):
    """0.5 is stored as 1/2, not as the nearest binary approximation."""
    with conn.cursor() as cur:
        config = _config(cur, 2, "rational")
        _rational_points(cur, config, [(0, 0), ("0.5", "0.5")])
        assert _sq(cur, config, 0, 1) == nf.Elem((1,), 2)


def test_the_unit_square_is_exact_in_sql(conn):
    with conn.cursor() as cur:
        config = _unit_square(cur)
        assert _sq(cur, config, 0, 1) == nf.Elem((1,), 1)
        assert _sq(cur, config, 0, 2) == nf.Elem((2,), 1)


def test_the_equilateral_triangle_is_exact_in_sql(conn):
    with conn.cursor() as cur:
        config = _equilateral(cur)
        for i, j in ((0, 1), (1, 2), (0, 2)):
            assert _sq(cur, config, i, j) == nf.Elem((1, 0), 1), (i, j)


def test_sq_distance_is_compares_against_a_rational(conn):
    with conn.cursor() as cur:
        config = _unit_square(cur)
        ok, ev = _probe(cur, "comb.sq_distance_is(%s, 0, 1, %s)", config, 1)
        assert ok is True
        assert ev["squared_distance"] == "(1)/1"
        assert _probe(cur, "comb.sq_distance_is(%s, 0, 2, %s)", config, 1)[0] is False
        assert _probe(cur, "comb.sq_distance_is(%s, 0, 2, %s)", config, 2)[0] is True


def test_coincident_points_are_found(conn):
    with conn.cursor() as cur:
        config = _config(cur, 2, "rational")
        _rational_points(cur, config, [(0, 0), (1, 0), (0, 0)])
        ok, ev = _probe(cur, "comb.points_are_distinct(%s)", config)
    assert ok is False
    assert ev["coincident"] == [[0, 2]]


def test_distinct_points_pass(conn):
    with conn.cursor() as cur:
        config = _unit_square(cur)
        ok, ev = _probe(cur, "comb.points_are_distinct(%s)", config)
    assert ok is True
    assert ev["points"] == 4


# --- unit-distance realisation ----------------------------------------------

def test_the_unit_square_faithfully_realises_c4(conn):
    with conn.cursor() as cur:
        config, graph = _unit_square(cur), _c4(cur)
        ok, ev = _probe(cur, "comb.is_unit_distance(%s, %s)", config, graph)
    assert ok is True
    assert (ev["edges"], ev["unit_non_edges"]) == (4, 0)


def test_the_equilateral_triangle_realises_k3_over_a_number_field(conn):
    with conn.cursor() as cur:
        config = _equilateral(cur)
        graph = _structure(cur, "graph", 3)
        for u, v in ((0, 1), (1, 2), (0, 2)):
            cur.execute("SELECT comb.add_edge(%s, %s, %s)", (graph, u, v))
        ok, ev = _probe(cur, "comb.is_unit_distance(%s, %s)", config, graph)
    assert ok is True
    assert ev["coord_domain"] == "algebraic"


def test_a_non_edge_at_distance_one_breaks_faithfulness_only(conn):
    """The square drawn against a path: every edge is at distance 1, but the
    missing edge 0-3 is too. A drawing, not a faithful realisation."""
    with conn.cursor() as cur:
        config = _unit_square(cur)
        path = _structure(cur, "graph", 4)
        for u, v in ((0, 1), (1, 2), (2, 3)):
            cur.execute("SELECT comb.add_edge(%s, %s, %s)", (path, u, v))

        ok, ev = _probe(cur, "comb.is_unit_distance(%s, %s, %s)", config, path, True)
        assert ok is False
        assert ev["reason"].startswith("a non-edge is at distance 1")
        assert ev["unit_non_edges"] == [{"u": 0, "v": 3}]

        assert _probe(cur, "comb.is_unit_distance(%s, %s, %s)",
                      config, path, False)[0] is True


def test_an_edge_off_unit_length_refutes_and_is_named(conn):
    with conn.cursor() as cur:
        config = _unit_square(cur)
        graph = _structure(cur, "graph", 4)
        cur.execute("SELECT comb.add_edge(%s, 0, 2)", (graph,))     # a diagonal
        ok, ev = _probe(cur, "comb.is_unit_distance(%s, %s)", config, graph)
    assert ok is False
    assert ev["bad_edges"] == [{"u": 0, "v": 2, "squared_distance": "(2)/1"}]


def test_a_size_mismatch_is_reported_before_any_distance(conn):
    with conn.cursor() as cur:
        config = _unit_square(cur)
        graph = _structure(cur, "graph", 3)
        ok, ev = _probe(cur, "comb.is_unit_distance(%s, %s)", config, graph)
    assert ok is False
    assert (ev["points"], ev["vertices"]) == (4, 3)


def test_unit_distance_declines_a_non_graph(conn):
    with conn.cursor() as cur:
        config = _unit_square(cur)
        hyper = _structure(cur, "hypergraph", 4)
        ok, ev = _probe(cur, "comb.is_unit_distance(%s, %s)", config, hyper)
    assert ok is None
    assert ev["kind"] == "hypergraph"


# --- the float shield -------------------------------------------------------

def test_a_float_configuration_can_never_record_a_valid_certificate(conn):
    """The probe returns true and the claim still must not. An approximate
    construction is a candidate, and step 94's shield says so without anyone
    having to remember that the coordinates were only nearly right."""
    with conn.cursor() as cur:
        config = _config(cur, 2, "float_heuristic")
        _rational_points(cur, config, [(0, 0), (1, 0), (1, 1), (0, 1)])
        graph = _c4(cur)

        assert _probe(cur, "comb.is_unit_distance(%s, %s)", config, graph)[0] is True

        cur.execute("SELECT comb.unit_distance_claim(%s, %s)", (config, graph))
        claim = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (claim,))
        assert cur.fetchone()[0] == "unverified"
        cur.execute("SELECT domain FROM cert.claim WHERE id = %s", (claim,))
        assert cur.fetchone()[0] == "float_heuristic"


def test_an_exact_configuration_does_record_a_valid_certificate(conn):
    with conn.cursor() as cur:
        config, graph = _unit_square(cur), _c4(cur)
        cur.execute("SELECT comb.unit_distance_claim(%s, %s)", (config, graph))
        claim = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (claim,))
        assert cur.fetchone()[0] == "valid"
        cur.execute("SELECT domain FROM cert.claim WHERE id = %s", (claim,))
        assert cur.fetchone()[0] == "rational"


def test_a_unit_distance_claim_flips_when_a_point_moves(conn):
    with conn.cursor() as cur:
        config, graph = _unit_square(cur), _c4(cur)
        cur.execute("SELECT comb.unit_distance_claim(%s, %s)", (config, graph))
        claim = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (claim,))
        assert cur.fetchone()[0] == "valid"

        cur.execute("SELECT comb.add_rational_point(%s, 1, %s::numeric[])",
                    (config, [2, 0]))
        cur.execute("SELECT (cert.check(%s)).status", (claim,))
        assert cur.fetchone()[0] == "refuted"


# --- SQL and Python must agree ----------------------------------------------

def test_python_squared_distances_match_sql_over_the_rationals(conn):
    """Two statements of one arithmetic; a silent divergence would mean a
    consumer verifying offline gets a different answer from the ledger."""
    points = [(0, 0), (1, 0), ("1.5", "-2.25"), ("0.5", "0.5"), (-3, 7)]
    with conn.cursor() as cur:
        config = _config(cur, 2, "rational")
        _rational_points(cur, config, points)
        py = [[nf.rational(int(str(c).replace(".", "")),
                           10 ** (len(str(c).split(".")[1]) if "." in str(c) else 0))
               for c in coords] for coords in points]
        for i in range(len(points)):
            for j in range(len(points)):
                assert _sq(cur, config, i, j) == nf.sq_distance(py[i], py[j], RATIONALS), (i, j)


def test_python_squared_distances_match_sql_over_a_number_field(conn):
    with conn.cursor() as cur:
        config = _equilateral(cur)
        py = [
            [nf.Elem.of([0, 0], 1), nf.Elem.of([0, 0], 1)],
            [nf.Elem.of([1, 0], 1), nf.Elem.of([0, 0], 1)],
            [nf.Elem.of([1, 0], 2), nf.Elem.of([0, 1], 2)],
        ]
        for i in range(3):
            for j in range(3):
                assert _sq(cur, config, i, j) == nf.sq_distance(py[i], py[j], SQRT3), (i, j)


def test_python_reduction_matches_sql(conn):
    vectors = [[0, 0, 1], [0, 0, 0, 1], [5], [1, 2, 3, 4, 5], [0, 0, 0, 0, 0, 7]]
    with conn.cursor() as cur:
        for v in vectors:
            cur.execute("SELECT comb.nf_reduce(%s::numeric[], %s::numeric[])",
                        (v, list(SQRT3)))
            assert [int(x) for x in cur.fetchone()[0]] == nf.reduce_mod(v, SQRT3), v


def test_python_multiplication_matches_sql(conn):
    pairs = [(([0, 1], 1), ([0, 1], 1)),       # sqrt3 * sqrt3 = 3
             (([1, 0], 2), ([0, 1], 2)),
             (([3, -2], 5), ([-1, 4], 7)),
             (([0, 0], 1), ([5, 5], 3))]
    with conn.cursor() as cur:
        for (an, ad), (bn, bd) in pairs:
            cur.execute(
                "SELECT (comb.nf_mul(ROW(%s::numeric[], %s)::comb.nf_elem,"
                "                    ROW(%s::numeric[], %s)::comb.nf_elem,"
                "                    %s::numeric[])).*",
                (an, ad, bn, bd, list(SQRT3)),
            )
            num, den = cur.fetchone()
            sql_elem = nf.Elem(tuple(int(x) for x in num), int(den))
            assert sql_elem == nf.mul(nf.Elem.of(an, ad), nf.Elem.of(bn, bd), SQRT3)


def test_python_normalisation_matches_sql(conn):
    cases = [([2, 4], 6), ([1, 0], -2), ([0, 0], 7), ([-6, -9], 3)]
    with conn.cursor() as cur:
        for num, den in cases:
            cur.execute("SELECT (comb.nf_normalize(%s::numeric[], %s)).*", (num, den))
            sn, sd = cur.fetchone()
            sql_elem = nf.Elem(tuple(int(x) for x in sn), int(sd))
            assert sql_elem == nf.Elem.of(num, den), (num, den)
