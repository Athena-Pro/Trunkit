"""Tests for the comb property probes (111).

The elementwise probes are ordinary: scan, decide, name the offender. The part
worth reading is the last two sections, which pin the rule the whole step is
arranged around -- that refuting a witness is not refuting the object.
comb.colouring_is_proper may return false; comb.chromatic_at_most may not, ever,
and there are tests here asserting `is not False` rather than `is None` so that
a future change which starts refuting on absence fails loudly.

Skips cleanly when no test DSN is set.
"""

from __future__ import annotations

import os
import uuid

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
            cur.execute("SELECT to_regclass('comb.witness')")
            if cur.fetchone()[0] is None:
                pytest.skip("comb.witness missing — apply 111_comb_probes.sql first")
        yield c
    finally:
        c.close()


def _register(cur, kind, ground_n) -> str:
    subject = f"test-{kind}-{uuid.uuid4()}"
    cur.execute("SELECT (comb.register_structure(%s, %s, %s)).id",
                (subject, kind, ground_n))
    return subject


def _edges(cur, subject, pairs):
    for u, v in pairs:
        cur.execute("SELECT comb.add_edge(%s, %s, %s)", (subject, u, v))


def _block(cur, subject, elements):
    cur.execute("SELECT comb.add_block(%s, %s)", (subject, list(elements)))
    return cur.fetchone()[0]


def _witness(cur, subject, name, kind, elements, values=None, target=None):
    cur.execute(
        "SELECT comb.register_witness(%s, %s, %s, %s, %s, %s)",
        (subject, name, kind, list(elements),
         None if values is None else list(values), target),
    )
    return cur.fetchone()[0]


def _probe(cur, sql, *args):
    cur.execute(f"SELECT ok, evidence FROM {sql}", args)
    return cur.fetchone()


def _status(cur, claim_id) -> str:
    cur.execute("SELECT (cert.check(%s)).status", (claim_id,))
    return cur.fetchone()[0]


@pytest.fixture()
def c5(conn):
    """The 5-cycle: triangle-free, chi = 3, alpha = 2. Small enough to reason
    about and big enough that every probe here has something to say."""
    with conn.cursor() as cur:
        subject = _register(cur, "graph", 5)
        _edges(cur, subject, [(0, 1), (1, 2), (2, 3), (3, 4), (0, 4)])
    return subject


# --- elementwise: uniformity and degrees ------------------------------------

def test_uniformity_holds_for_a_graph(conn, c5):
    with conn.cursor() as cur:
        ok, ev = _probe(cur, "comb.is_uniform(%s, %s)", c5, 2)
    assert ok is True
    assert ev["blocks"] == 5


def test_uniformity_names_the_offending_blocks(conn):
    with conn.cursor() as cur:
        subject = _register(cur, "hypergraph", 5)
        _block(cur, subject, [0, 1, 2])
        odd = _block(cur, subject, [3, 4])
        ok, ev = _probe(cur, "comb.is_uniform(%s, %s)", subject, 3)
    assert ok is False
    assert ev["offending"] == [{"block_idx": odd, "size": 2}]


def test_degree_sequence_counts_isolated_elements(conn):
    with conn.cursor() as cur:
        subject = _register(cur, "graph", 4)
        _edges(cur, subject, [(0, 1), (1, 2)])
        cur.execute("SELECT element_idx, degree FROM comb.degree_sequence(%s)",
                    (subject,))
        assert cur.fetchall() == [(0, 1), (1, 2), (2, 1), (3, 0)]


# --- elementwise: families --------------------------------------------------

def test_an_antichain_is_a_primitive_family(conn):
    with conn.cursor() as cur:
        subject = _register(cur, "set_system", 5)
        for block in ([0, 1], [1, 2], [0, 2]):
            _block(cur, subject, block)
        ok, ev = _probe(cur, "comb.is_primitive_family(%s)", subject)
    assert ok is True
    assert ev["blocks"] == 3


def test_a_containment_refutes_primitivity_and_is_named(conn):
    with conn.cursor() as cur:
        subject = _register(cur, "set_system", 5)
        small = _block(cur, subject, [0, 1])
        big = _block(cur, subject, [0, 1, 2])
        ok, ev = _probe(cur, "comb.is_primitive_family(%s)", subject)
    assert ok is False
    hit = ev["containments"][0]
    assert (hit["contained_block"], hit["container_block"]) == (small, big)


def test_equal_blocks_do_not_refute_primitivity(conn):
    """Containment is strict; a repeat is a well-formedness remark (110)."""
    with conn.cursor() as cur:
        subject = _register(cur, "set_system", 4)
        _block(cur, subject, [0, 1])
        sid = _sid(cur, subject)
        cur.execute("INSERT INTO comb.block (structure_id, idx) VALUES (%s, 1)", (sid,))
        cur.execute("INSERT INTO comb.incidence (structure_id, block_idx, element_idx)"
                    " VALUES (%s, 1, 0), (%s, 1, 1)", (sid, sid))
        ok, ev = _probe(cur, "comb.is_primitive_family(%s)", subject)
    assert ok is True
    assert ev["repeated_blocks"] == 1


def _sid(cur, subject) -> int:
    cur.execute("SELECT comb.structure_id(%s)", (subject,))
    return cur.fetchone()[0]


def test_a_pairwise_meeting_family_is_intersecting(conn):
    with conn.cursor() as cur:
        subject = _register(cur, "set_system", 4)
        for block in ([0, 1], [1, 2], [1, 3]):
            _block(cur, subject, block)
        assert _probe(cur, "comb.is_intersecting_family(%s)", subject)[0] is True


def test_a_disjoint_pair_refutes_intersecting_and_is_named(conn):
    with conn.cursor() as cur:
        subject = _register(cur, "set_system", 5)
        a = _block(cur, subject, [0, 1])
        b = _block(cur, subject, [2, 3])
        ok, ev = _probe(cur, "comb.is_intersecting_family(%s)", subject)
    assert ok is False
    assert (ev["disjoint_pairs"][0]["block_a"],
            ev["disjoint_pairs"][0]["block_b"]) == (a, b)


# --- elementwise: triangles -------------------------------------------------

def test_the_five_cycle_is_triangle_free(conn, c5):
    with conn.cursor() as cur:
        ok, ev = _probe(cur, "comb.is_triangle_free(%s)", c5)
    assert ok is True
    assert ev["edges"] == 5


def test_a_triangle_is_found_and_named(conn):
    with conn.cursor() as cur:
        subject = _register(cur, "graph", 3)
        _edges(cur, subject, [(0, 1), (1, 2), (0, 2)])
        ok, ev = _probe(cur, "comb.is_triangle_free(%s)", subject)
    assert ok is False
    assert ev["triangles"] == [[0, 1, 2]]


def test_triangle_freeness_declines_a_question_that_does_not_apply(conn):
    """A hypergraph gets unverified, not a confident yes."""
    with conn.cursor() as cur:
        subject = _register(cur, "hypergraph", 4)
        _block(cur, subject, [0, 1, 2])
        ok, ev = _probe(cur, "comb.is_triangle_free(%s)", subject)
    assert ok is None
    assert ev["kind"] == "hypergraph"


def test_triangle_freeness_of_an_unknown_structure_is_refused(conn):
    with conn.cursor() as cur:
        ok, ev = _probe(cur, "comb.is_triangle_free(%s)", f"absent-{uuid.uuid4()}")
    assert ok is False
    assert ev["reason"] == "no such structure"


# --- witness registration ---------------------------------------------------

def test_a_witness_may_not_assign_outside_the_ground_set(conn, c5):
    with conn.cursor() as cur, pytest.raises(psycopg.Error, match="not in the ground set"):
        _witness(cur, c5, "bad", "colouring", [0, 9], [0, 1])


def test_a_witness_may_not_assign_an_element_twice(conn, c5):
    with conn.cursor() as cur, pytest.raises(psycopg.Error, match="assigns an element twice"):
        _witness(cur, c5, "bad", "colouring", [0, 0], [0, 1])


def test_values_must_pair_with_elements(conn, c5):
    with conn.cursor() as cur, pytest.raises(psycopg.Error, match="differ in length"):
        _witness(cur, c5, "bad", "colouring", [0, 1], [0])


def test_a_colouring_needs_values(conn, c5):
    with conn.cursor() as cur, pytest.raises(psycopg.Error, match="needs values"):
        _witness(cur, c5, "bad", "colouring", [0, 1])


def test_a_subset_needs_no_values(conn, c5):
    with conn.cursor() as cur:
        _witness(cur, c5, "s", "subset", [0, 2])
        assert _probe(cur, "comb.subset_is_independent(%s, %s)", c5, "s")[0] is True


def test_re_registering_a_witness_replaces_its_assignment(conn, c5):
    """A witness is producer data; a claim over it is meant to flip when it
    changes, not to keep answering about a stale version."""
    with conn.cursor() as cur:
        _witness(cur, c5, "w", "colouring", [0, 1, 2, 3, 4], [0, 1, 0, 1, 2])
        assert _probe(cur, "comb.colouring_is_proper(%s, %s)", c5, "w")[0] is True
        _witness(cur, c5, "w", "colouring", [0, 1, 2, 3, 4], [0, 0, 0, 0, 0])
        assert _probe(cur, "comb.colouring_is_proper(%s, %s)", c5, "w")[0] is False


def test_an_unknown_witness_is_reported_not_guessed(conn, c5):
    with conn.cursor() as cur, pytest.raises(psycopg.Error, match="no witness"):
        cur.execute("SELECT comb.witness_id(%s, %s)", (c5, "absent"))


# --- witness checks ---------------------------------------------------------

def test_a_proper_colouring_is_valid(conn, c5):
    with conn.cursor() as cur:
        _witness(cur, c5, "good", "colouring", [0, 1, 2, 3, 4], [0, 1, 0, 1, 2])
        ok, ev = _probe(cur, "comb.colouring_is_proper(%s, %s)", c5, "good")
    assert ok is True
    assert ev["colours"] == 3


def test_a_monochromatic_block_refutes_the_colouring(conn, c5):
    with conn.cursor() as cur:
        _witness(cur, c5, "bad", "colouring", [0, 1, 2, 3, 4], [0, 0, 1, 0, 1])
        ok, ev = _probe(cur, "comb.colouring_is_proper(%s, %s)", c5, "bad")
    assert ok is False
    assert ev["monochromatic"][0]["elements"] == [0, 1]


def test_a_partial_colouring_is_refuted_not_quietly_accepted(conn, c5):
    """Leaving a vertex out is the cheapest way to make a bad colouring look
    proper."""
    with conn.cursor() as cur:
        _witness(cur, c5, "partial", "colouring", [0, 1, 2], [0, 1, 0])
        ok, ev = _probe(cur, "comb.colouring_is_proper(%s, %s)", c5, "partial")
    assert ok is False
    assert ev["uncoloured"] == [3, 4]


def test_a_probe_declines_a_witness_of_the_wrong_kind(conn, c5):
    with conn.cursor() as cur:
        _witness(cur, c5, "s", "subset", [0, 2])
        ok, ev = _probe(cur, "comb.colouring_is_proper(%s, %s)", c5, "s")
    assert ok is False
    assert ev["reason"] == "witness is not a colouring"


def test_an_independent_subset_is_valid(conn, c5):
    with conn.cursor() as cur:
        _witness(cur, c5, "ind", "subset", [0, 2])
        ok, ev = _probe(cur, "comb.subset_is_independent(%s, %s)", c5, "ind")
    assert ok is True
    assert ev["size"] == 2


def test_a_contained_block_refutes_independence(conn, c5):
    with conn.cursor() as cur:
        _witness(cur, c5, "notind", "subset", [0, 1])
        ok, ev = _probe(cur, "comb.subset_is_independent(%s, %s)", c5, "notind")
    assert ok is False
    assert ev["contained_blocks"][0]["elements"] == [0, 1]


# --- isomorphism ------------------------------------------------------------

@pytest.fixture()
def triangles(conn):
    with conn.cursor() as cur:
        a, b = _register(cur, "graph", 3), _register(cur, "graph", 3)
        _edges(cur, a, [(0, 1), (1, 2), (0, 2)])
        _edges(cur, b, [(0, 1), (1, 2), (0, 2)])
    return a, b


def test_a_relabelling_of_a_triangle_is_an_isomorphism(conn, triangles):
    a, b = triangles
    with conn.cursor() as cur:
        _witness(cur, a, "perm", "bijection", [0, 1, 2], [2, 0, 1], target=b)
        ok, ev = _probe(cur, "comb.bijection_is_isomorphism(%s, %s)", a, "perm")
    assert ok is True
    assert ev["blocks"] == 3


def test_a_triangle_does_not_map_onto_a_path(conn, triangles):
    a, _ = triangles
    with conn.cursor() as cur:
        path = _register(cur, "graph", 3)
        _edges(cur, path, [(0, 1), (1, 2)])
        _witness(cur, a, "wrong", "bijection", [0, 1, 2], [0, 1, 2], target=path)
        ok, ev = _probe(cur, "comb.bijection_is_isomorphism(%s, %s)", a, "wrong")
    assert ok is False
    assert ev["reason"] == "a source block has no image block in the target"


def test_a_non_injective_map_is_not_a_bijection(conn, triangles):
    a, b = triangles
    with conn.cursor() as cur:
        _witness(cur, a, "collapse", "bijection", [0, 1, 2], [0, 0, 1], target=b)
        ok, ev = _probe(cur, "comb.bijection_is_isomorphism(%s, %s)", a, "collapse")
    assert ok is False
    assert ev["reason"] == "the map is not a bijection of the ground sets"


def test_ground_sets_of_different_size_are_reported_before_anything_else(conn, triangles):
    a, _ = triangles
    with conn.cursor() as cur:
        big = _register(cur, "graph", 4)
        _witness(cur, a, "size", "bijection", [0, 1, 2], [0, 1, 2], target=big)
        ok, ev = _probe(cur, "comb.bijection_is_isomorphism(%s, %s)", a, "size")
    assert ok is False
    assert ev["reason"] == "ground sets differ in size"


def test_isomorphism_of_digraphs_respects_direction(conn):
    """The reason block comparison goes through the position-carrying
    signature: otherwise 0->1 and 1->0 would compare equal."""
    with conn.cursor() as cur:
        d1, d2 = _register(cur, "digraph", 2), _register(cur, "digraph", 2)
        cur.execute("SELECT comb.add_arc(%s, 0, 1)", (d1,))
        cur.execute("SELECT comb.add_arc(%s, 1, 0)", (d2,))
        _witness(cur, d1, "identity", "bijection", [0, 1], [0, 1], target=d2)
        _witness(cur, d1, "swap", "bijection", [0, 1], [1, 0], target=d2)
        assert _probe(cur, "comb.bijection_is_isomorphism(%s, %s)", d1, "identity")[0] is False
        assert _probe(cur, "comb.bijection_is_isomorphism(%s, %s)", d1, "swap")[0] is True


# --- the one-sided bounds: what this step is really about -------------------

def test_a_deposited_colouring_proves_a_chromatic_upper_bound(conn, c5):
    with conn.cursor() as cur:
        _witness(cur, c5, "col3", "colouring", [0, 1, 2, 3, 4], [0, 1, 0, 1, 2])
        ok, ev = _probe(cur, "comb.chromatic_at_most(%s, %s)", c5, 3)
    assert ok is True
    assert (ev["witness"], ev["colours_used"]) == ("col3", 3)


def test_a_failed_colouring_never_refutes_the_chromatic_bound(conn, c5):
    """chi(C5) = 3, so chi <= 2 really is false -- and the probe still must not
    say so, because a producer's bad guess is not a proof."""
    with conn.cursor() as cur:
        _witness(cur, c5, "col2", "colouring", [0, 1, 2, 3, 4], [0, 1, 0, 1, 0])
        assert _probe(cur, "comb.colouring_is_proper(%s, %s)", c5, "col2")[0] is False
        ok, ev = _probe(cur, "comb.chromatic_at_most(%s, %s)", c5, 2)
    assert ok is not False
    assert ok is None
    assert ev["colourings_on_record"] == 1
    assert ev["status"] == "absence of evidence, not evidence of absence"


def test_an_empty_record_leaves_the_chromatic_bound_unverified(conn, c5):
    with conn.cursor() as cur:
        ok, ev = _probe(cur, "comb.chromatic_at_most(%s, %s)", c5, 3)
    assert ok is not False
    assert ok is None
    assert ev["colourings_on_record"] == 0


def test_a_deposited_subset_proves_an_independence_lower_bound(conn, c5):
    with conn.cursor() as cur:
        _witness(cur, c5, "ind", "subset", [0, 2])
        ok, ev = _probe(cur, "comb.independence_at_least(%s, %s)", c5, 2)
    assert ok is True
    assert ev["size"] == 2


def test_independence_beyond_what_is_deposited_stays_unverified(conn, c5):
    """alpha(C5) = 2, so alpha >= 3 is false -- and again the probe declines to
    say it."""
    with conn.cursor() as cur:
        _witness(cur, c5, "ind", "subset", [0, 2])
        ok, _ = _probe(cur, "comb.independence_at_least(%s, %s)", c5, 3)
    assert ok is not False
    assert ok is None


def test_a_bad_subset_on_record_does_not_count_toward_the_bound(conn, c5):
    with conn.cursor() as cur:
        _witness(cur, c5, "notind", "subset", [0, 1, 2])
        ok, _ = _probe(cur, "comb.independence_at_least(%s, %s)", c5, 3)
    assert ok is None


# --- claims -----------------------------------------------------------------

def test_a_colouring_claim_flips_when_the_witness_is_edited(conn, c5):
    with conn.cursor() as cur:
        _witness(cur, c5, "w", "colouring", [0, 1, 2, 3, 4], [0, 1, 0, 1, 2])
        cur.execute("SELECT comb.colouring_claim(%s, %s)", (c5, "w"))
        claim = cur.fetchone()[0]
        assert _status(cur, claim) == "valid"

        _witness(cur, c5, "w", "colouring", [0, 1, 2, 3, 4], [0, 0, 0, 0, 0])
        assert _status(cur, claim) == "refuted"


def test_a_chromatic_bound_claim_reads_unverified_not_refuted(conn, c5):
    """The verdict a reader of the ledger sees for an unproven upper bound."""
    with conn.cursor() as cur:
        cur.execute("SELECT comb.chromatic_bound_claim(%s, %s)", (c5, 2))
        claim = cur.fetchone()[0]
        assert _status(cur, claim) == "unverified"


def test_a_chromatic_bound_claim_goes_valid_once_a_colouring_lands(conn, c5):
    with conn.cursor() as cur:
        cur.execute("SELECT comb.chromatic_bound_claim(%s, %s)", (c5, 3))
        claim = cur.fetchone()[0]
        assert _status(cur, claim) == "unverified"

        _witness(cur, c5, "col3", "colouring", [0, 1, 2, 3, 4], [0, 1, 0, 1, 2])
        assert _status(cur, claim) == "valid"


def test_a_primitivity_claim_flips_when_a_superset_lands(conn):
    with conn.cursor() as cur:
        subject = _register(cur, "set_system", 5)
        _block(cur, subject, [0, 1])
        _block(cur, subject, [2, 3])
        cur.execute("SELECT comb.primitive_family_claim(%s)", (subject,))
        claim = cur.fetchone()[0]
        assert _status(cur, claim) == "valid"

        _block(cur, subject, [0, 1, 2])
        assert _status(cur, claim) == "refuted"


def test_the_witness_claims_name_the_witness_in_their_statement(conn, c5):
    """So that a refutation cannot be misread as a statement about the graph."""
    with conn.cursor() as cur:
        _witness(cur, c5, "w", "colouring", [0, 1, 2, 3, 4], [0, 1, 0, 1, 2])
        cur.execute("SELECT comb.colouring_claim(%s, %s)", (c5, "w"))
        cur.execute("SELECT statement FROM cert.claim WHERE id = %s",
                    (cur.fetchone()[0],))
        statement = cur.fetchone()[0]
    assert statement.startswith("colouring 'w' properly colours")


def test_an_isomorphism_claim_is_recheckable(conn, triangles):
    a, b = triangles
    with conn.cursor() as cur:
        _witness(cur, a, "perm", "bijection", [0, 1, 2], [2, 0, 1], target=b)
        cur.execute("SELECT comb.isomorphism_claim(%s, %s)", (a, "perm"))
        claim = cur.fetchone()[0]
        assert _status(cur, claim) == "valid"

        # Remove an edge from the target and the map stops being one.
        cur.execute("DELETE FROM comb.block WHERE structure_id = comb.structure_id(%s)"
                    " AND idx = 0", (b,))
        assert _status(cur, claim) == "refuted"
