"""Tests for carrying comb objects and the bound bridge (113).

Two things are pinned. That a construction leaves the database intact -- the
serialised object lands in cert.witness, so a consumer re-derives the verdict
from the object rather than trusting that someone once ran a probe. And that
the bridge to the bound tier composes instead of coupling: 105 never learns
what a comb object is, the attainment is its own claim, and cert.derivation
joins them.

The end-to-end case is Mantel at n = 5. The maximum number of edges in a
triangle-free graph on 5 vertices is 6, attained by K(2,3); the construction
gives the achievable side and the theorem gives the other, and the enclosure
closes to width 0.

Skips cleanly when no test DSN is set.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import psycopg
import pytest


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
            cur.execute("SELECT to_regprocedure('comb.attest_attainment(bigint)')")
            if cur.fetchone()[0] is None:
                pytest.skip("comb.attest_attainment missing — apply 113_comb_witness.sql")
        yield c
    finally:
        c.close()


def _structure(cur, kind, n, source="") -> str:
    subject = f"test-{kind}-{uuid.uuid4()}"
    cur.execute("SELECT (comb.register_structure(%s, %s, %s, %s)).id",
                (subject, kind, n, source))
    return subject


def _edges(cur, subject, pairs):
    for u, v in pairs:
        cur.execute("SELECT comb.add_edge(%s, %s, %s)", (subject, u, v))


def _check_with_witness(cur, claim_id) -> int:
    cur.execute("SELECT cert.check_with_witness(%s)", (claim_id,))
    return cur.fetchone()[0]


def _cert_status(cur, cert_id) -> str:
    cur.execute("SELECT status FROM cert.certificate WHERE id = %s", (cert_id,))
    return cur.fetchone()[0]


def _status(cur, claim_id) -> str:
    cur.execute("SELECT (cert.check(%s)).status", (claim_id,))
    return cur.fetchone()[0]


def _witness_body(cur, cert_id):
    cur.execute("SELECT body FROM cert.witness WHERE certificate_id = %s", (cert_id,))
    row = cur.fetchone()
    return None if row is None else row[0]


def _probe(cur, sql, *args):
    cur.execute(f"SELECT ok, evidence FROM {sql}", args)
    return cur.fetchone()


@pytest.fixture()
def k23(conn):
    """K(2,3): parts {0,1} and {2,3,4}. Six edges, triangle-free, and the
    extremal graph for Mantel's theorem at n = 5."""
    with conn.cursor() as cur:
        subject = _structure(cur, "graph", 5, "Mantel extremal example")
        _edges(cur, subject, [(0, 2), (0, 3), (0, 4), (1, 2), (1, 3), (1, 4)])
    return subject


# --- serialisation ----------------------------------------------------------

def test_a_structure_serialises_whole(conn, k23):
    with conn.cursor() as cur:
        cur.execute("SELECT comb.serialize_structure(%s)", (k23,))
        body = cur.fetchone()[0]
    assert body["witness_type"] == "comb_structure"
    assert (body["kind"], body["ground_n"]) == ("graph", 5)
    assert len(body["blocks"]) == 6
    assert sorted(tuple(b["elements"]) for b in body["blocks"]) == [
        (0, 2), (0, 3), (0, 4), (1, 2), (1, 3), (1, 4)
    ]


def test_serialisation_keeps_arc_direction(conn):
    """A digraph that round-tripped without positions would come back as its
    underlying graph."""
    with conn.cursor() as cur:
        subject = _structure(cur, "digraph", 2)
        cur.execute("SELECT comb.add_arc(%s, 1, 0)", (subject,))
        cur.execute("SELECT comb.serialize_structure(%s)", (subject,))
        block = cur.fetchone()[0]["blocks"][0]
    assert block["elements"] == [0, 1]
    assert block["positions"] == [1, 0]      # element 0 is the head


def test_a_configuration_serialises_with_its_field(conn):
    """Coordinates mean nothing without the field they are written in."""
    with conn.cursor() as cur:
        cur.execute("SELECT comb.register_field(%s, %s::numeric[])",
                    ("Q(sqrt3)", [-3, 0, 1]))
        config = f"test-config-{uuid.uuid4()}"
        cur.execute("SELECT comb.register_configuration(%s, 2, 'algebraic', 'Q(sqrt3)')",
                    (config,))
        cur.execute("SELECT comb.set_coordinate(%s, 0, 0, %s::numeric[], 2)",
                    (config, [1, 0]))
        cur.execute("SELECT comb.set_coordinate(%s, 0, 1, %s::numeric[], 2)",
                    (config, [0, 1]))
        cur.execute("SELECT comb.serialize_configuration(%s)", (config,))
        body = cur.fetchone()[0]
    assert body["witness_type"] == "comb_configuration"
    assert [int(c) for c in body["min_poly"]] == [-3, 0, 1]
    assert body["field"] == "Q(sqrt3)"
    coords = body["points"][0]["coords"]
    assert [int(x) for x in coords[1]["num"]] == [0, 1]     # sqrt3 / 2
    assert int(coords[1]["den"]) == 2


def test_serialising_an_absent_structure_is_refused(conn):
    with conn.cursor() as cur, pytest.raises(psycopg.Error, match="no structure"):
        cur.execute("SELECT comb.serialize_structure(%s)", (f"absent-{uuid.uuid4()}",))


# --- carried claims ---------------------------------------------------------

def test_a_construction_claim_files_the_object_as_its_witness(conn, k23):
    with conn.cursor() as cur:
        cur.execute("SELECT comb.construction_claim(%s, %s)", (k23, "triangle_free"))
        claim = cur.fetchone()[0]
        cert_id = _check_with_witness(cur, claim)
        assert _cert_status(cur, cert_id) == "valid"
        body = _witness_body(cur, cert_id)
    assert body["witness_type"] == "comb_structure"
    assert body["property"] == "triangle_free"
    assert len(body["blocks"]) == 6


def test_the_object_travels_even_when_the_verdict_is_refuted(conn):
    """A refuted construction is exactly when a reader most wants to see what
    was built."""
    with conn.cursor() as cur:
        subject = _structure(cur, "graph", 3)
        _edges(cur, subject, [(0, 1), (1, 2), (0, 2)])
        cur.execute("SELECT comb.construction_claim(%s, %s)", (subject, "triangle_free"))
        cert_id = _check_with_witness(cur, cur.fetchone()[0])
        assert _cert_status(cur, cert_id) == "refuted"
        body = _witness_body(cur, cert_id)
    assert body is not None
    assert len(body["blocks"]) == 3


def test_a_carried_claim_uses_the_witness_carry_tier(conn, k23):
    with conn.cursor() as cur:
        cur.execute("SELECT comb.construction_claim(%s, %s)", (k23, "wellformed"))
        cur.execute("SELECT method FROM cert.claim WHERE id = %s", (cur.fetchone()[0],))
        assert cur.fetchone()[0] == "witness_carry"


def test_an_unknown_property_is_refused(conn, k23):
    with conn.cursor() as cur, pytest.raises(psycopg.Error, match="unknown property"):
        cur.execute("SELECT comb.construction_claim(%s, %s)", (k23, "four_colourable"))


def test_uniformity_needs_its_k(conn, k23):
    with conn.cursor() as cur, pytest.raises(psycopg.Error, match="needs a k"):
        cur.execute("SELECT ok FROM comb.carried_property(%s, %s)", (k23, "uniform"))


def test_a_carried_uniformity_claim_passes_its_argument_through(conn, k23):
    with conn.cursor() as cur:
        cur.execute("SELECT comb.construction_claim(%s, %s, %s)", (k23, "uniform", 2))
        cert_id = _check_with_witness(cur, cur.fetchone()[0])
        assert _cert_status(cur, cert_id) == "valid"
        assert _witness_body(cur, cert_id)["arg"] == 2


def test_a_realisation_claim_carries_both_halves(conn):
    with conn.cursor() as cur:
        config = f"test-config-{uuid.uuid4()}"
        cur.execute("SELECT comb.register_configuration(%s, 2, 'rational')", (config,))
        for idx, xy in enumerate([(0, 0), (1, 0), (1, 1), (0, 1)]):
            cur.execute("SELECT comb.add_rational_point(%s, %s, %s::numeric[])",
                        (config, idx, list(xy)))
        graph = _structure(cur, "graph", 4)
        _edges(cur, graph, [(0, 1), (1, 2), (2, 3), (0, 3)])

        cur.execute("SELECT comb.realisation_claim(%s, %s)", (config, graph))
        cert_id = _check_with_witness(cur, cur.fetchone()[0])
        assert _cert_status(cur, cert_id) == "valid"
        body = _witness_body(cur, cert_id)
    assert body["witness_type"] == "comb_realisation"
    assert body["configuration"]["coord_domain"] == "rational"
    assert len(body["structure"]["blocks"]) == 4


def test_a_float_realisation_claim_is_still_shielded(conn):
    """The 112 rule survives the trip through witness_carry."""
    with conn.cursor() as cur:
        config = f"test-config-{uuid.uuid4()}"
        cur.execute("SELECT comb.register_configuration(%s, 2, 'float_heuristic')",
                    (config,))
        for idx, xy in enumerate([(0, 0), (1, 0)]):
            cur.execute("SELECT comb.add_rational_point(%s, %s, %s::numeric[])",
                        (config, idx, list(xy)))
        graph = _structure(cur, "graph", 2)
        _edges(cur, graph, [(0, 1)])

        cur.execute("SELECT comb.realisation_claim(%s, %s)", (config, graph))
        cert_id = _check_with_witness(cur, cur.fetchone()[0])
        assert _cert_status(cur, cert_id) == "unverified"


# --- the bound bridge -------------------------------------------------------

def _quantity(cur, description="a quantity under test") -> str:
    subject = f"test-quantity-{uuid.uuid4()}"
    cur.execute("SELECT (cert.register_quantity(%s, %s)).id", (subject, description))
    return subject


def _bound(cur, quantity, direction, value, source="", claim_id=None) -> int:
    cur.execute(
        "SELECT (cert.register_bound(%s, %s, %s, false, '{}', %s, %s)).id",
        (quantity, direction, Decimal(str(value)), source, claim_id),
    )
    return cur.fetchone()[0]


def test_attaining_a_bound_points_it_at_the_object(conn, k23):
    with conn.cursor() as cur:
        q = _quantity(cur)
        b = _bound(cur, q, "lower", 6)
        cur.execute("SELECT (comb.attain_bound(%s, %s)).attained_by", (b, k23))
        assert cur.fetchone()[0] == {"comb_structure": k23}


def test_attaining_a_bound_with_an_absent_object_is_refused(conn):
    with conn.cursor() as cur:
        q = _quantity(cur)
        b = _bound(cur, q, "lower", 6)
        with pytest.raises(psycopg.Error, match="no structure"):
            cur.execute("SELECT comb.attain_bound(%s, %s)", (b, f"absent-{uuid.uuid4()}"))


def test_attainment_resolves_to_a_deposited_object(conn, k23):
    with conn.cursor() as cur:
        q = _quantity(cur)
        b = _bound(cur, q, "lower", 6)
        cur.execute("SELECT comb.attain_bound(%s, %s)", (b, k23))
        ok, ev = _probe(cur, "comb.bound_attainment(%s)", b)
    assert ok is True
    assert ev["attaining_object"] == k23
    assert ev["witness"]["ground_n"] == 5


def test_a_dangling_witness_is_refuted(conn, k23):
    """Worse than no witness, because it reads as evidence."""
    with conn.cursor() as cur:
        q = _quantity(cur)
        b = _bound(cur, q, "lower", 6)
        cur.execute("SELECT comb.attain_bound(%s, %s)", (b, k23))
        cur.execute("DELETE FROM comb.structure WHERE subject_id = %s", (k23,))
        ok, ev = _probe(cur, "comb.bound_attainment(%s)", b)
    assert ok is False
    assert ev["names"] == k23


def test_a_bound_naming_no_comb_object_is_not_this_layers_business(conn):
    with conn.cursor() as cur:
        q = _quantity(cur)
        b = _bound(cur, q, "lower", 6)
        ok, ev = _probe(cur, "comb.bound_attainment(%s)", b)
        assert ok is None
        assert ev["status"] == "nothing for this layer to check"

        cur.execute("UPDATE cert.bound SET attained_by = %s WHERE id = %s",
                    ('{"note": "see the paper"}', b))
        assert _probe(cur, "comb.bound_attainment(%s)", b)[0] is None


def test_an_unknown_bound_is_refused(conn):
    with conn.cursor() as cur:
        ok, ev = _probe(cur, "comb.bound_attainment(%s)", -1)
    assert ok is False
    assert ev["reason"] == "no such bound"


def test_attesting_records_exactly_one_derivation_edge(conn, k23):
    with conn.cursor() as cur:
        q = _quantity(cur)
        b = _bound(cur, q, "lower", 6)
        cur.execute("SELECT comb.attain_bound(%s, %s)", (b, k23))
        cur.execute("SELECT comb.attest_attainment(%s)", (b,))
        optimal = cur.fetchone()[0]
        cur.execute("SELECT comb.attest_attainment(%s)", (b,))       # replay
        cur.execute("SELECT premise_ids, rule FROM cert.derivation"
                    " WHERE conclusion_id = %s", (optimal,))
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][1] == "comb_attainment"

    with conn.cursor() as cur:
        cur.execute("SELECT comb.attainment_claim(%s)", (b,))
        assert rows[0][0] == [cur.fetchone()[0]]


# --- Mantel at n = 5, end to end --------------------------------------------

def test_a_construction_and_a_theorem_close_an_extremal_value(conn, k23):
    """The whole point of T2 meeting T3: the construction gives the achievable
    side, the theorem gives the other, and the enclosure closes to nothing."""
    with conn.cursor() as cur:
        cur.execute("SELECT comb.construction_claim(%s, %s)", (k23, "triangle_free"))
        construction = cur.fetchone()[0]
        _check_with_witness(cur, construction)

        q = _quantity(cur, "max edges in a triangle-free graph on 5 vertices")
        low = _bound(cur, q, "lower", 6, "K(2,3) construction", construction)
        _bound(cur, q, "upper", 6, "Mantel 1907")

        cur.execute("SELECT lower_value, upper_value, width FROM cert.enclosure(%s)",
                    (q,))
        assert cur.fetchone() == (Decimal(6), Decimal(6), Decimal(0))

        cur.execute("SELECT comb.attain_bound(%s, %s)", (low, k23))
        cur.execute("SELECT comb.attest_attainment(%s)", (low,))
        optimal = cur.fetchone()[0]
        assert _status(cur, optimal) == "valid"


def test_breaking_the_construction_drops_optimality_but_not_resolution(conn, k23):
    """Two claims, two verdicts. Adding an edge to K(2,3) makes a triangle, so
    the construction is refuted and optimality falls back to unverified -- while
    the attainment stays valid, because "the bound names an object that is
    here" is a different statement from "that object has the property"."""
    with conn.cursor() as cur:
        cur.execute("SELECT comb.construction_claim(%s, %s)", (k23, "triangle_free"))
        construction = cur.fetchone()[0]
        _check_with_witness(cur, construction)

        q = _quantity(cur, "max edges in a triangle-free graph on 5 vertices")
        low = _bound(cur, q, "lower", 6, "K(2,3) construction", construction)
        cur.execute("SELECT comb.attain_bound(%s, %s)", (low, k23))
        cur.execute("SELECT comb.attest_attainment(%s)", (low,))
        optimal = cur.fetchone()[0]
        assert _status(cur, optimal) == "valid"

        cur.execute("SELECT comb.add_edge(%s, 2, 3)", (k23,))       # makes a triangle
        assert _probe(cur, "comb.is_triangle_free(%s)", k23)[0] is False

        cert_id = _check_with_witness(cur, construction)
        assert _cert_status(cur, cert_id) == "refuted"
        assert _status(cur, optimal) == "unverified"

        cur.execute("SELECT comb.attainment_claim(%s)", (low,))
        assert _status(cur, cur.fetchone()[0]) == "valid"
