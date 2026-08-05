"""Tests for the comb structure store (110).

Three things are being pinned here. That the incidence trio really does hold
all four kinds -- a graph, a digraph, a hypergraph and a set family -- through
one set of tables. That ingestion is replayable, since a construction gets
deposited by a script that will be run more than once. And that
comb.wellformed refutes exactly the malformed cases and no others, because it
is the only claim this step makes and everything in 111 will assume it.

There is also one structural test with no mathematics in it at all:
comb must hold no foreign key into calx. That is the rule the whole schema
exists to obey, and a constraint added by someone being helpful would not
otherwise fail anything until a `trunkit reset` silently deleted a deposited
counterexample.

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
            cur.execute("SELECT to_regclass('comb.structure')")
            if cur.fetchone()[0] is None:
                pytest.skip("comb.structure missing — apply 110_comb_structure.sql first")
        yield c
    finally:
        c.close()


def _register(cur, kind, ground_n, subject=None, source="") -> str:
    subject = subject or f"test-{kind}-{uuid.uuid4()}"
    cur.execute(
        "SELECT (comb.register_structure(%s, %s, %s, %s)).id",
        (subject, kind, ground_n, source),
    )
    return subject


@pytest.fixture()
def graph(conn):
    """A triangle: the smallest object with an edge, a cycle and no ambiguity."""
    with conn.cursor() as cur:
        subject = _register(cur, "graph", 3)
        for u, v in ((0, 1), (1, 2), (0, 2)):
            cur.execute("SELECT comb.add_edge(%s, %s, %s)", (subject, u, v))
    return subject


def _sid(cur, subject) -> int:
    cur.execute("SELECT comb.structure_id(%s)", (subject,))
    return cur.fetchone()[0]


def _wellformed(cur, subject):
    cur.execute("SELECT ok, evidence FROM comb.wellformed(%s)", (subject,))
    return cur.fetchone()


def _faults(evidence) -> list[str]:
    return [f["fault"] for f in evidence["faults"]]


# --- the schema rule this layer exists to obey ------------------------------

def test_comb_holds_no_foreign_key_into_calx(conn):
    """calx is dropped CASCADE by `trunkit reset`; comb objects are deposited
    and must survive it. This is the rule, as a query."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ct.relname, c.conname"
            "  FROM pg_constraint c"
            "  JOIN pg_class ct ON ct.oid = c.conrelid"
            "  JOIN pg_namespace cn ON cn.oid = ct.relnamespace"
            "  JOIN pg_class ft ON ft.oid = c.confrelid"
            "  JOIN pg_namespace fn ON fn.oid = ft.relnamespace"
            " WHERE c.contype = 'f' AND cn.nspname = 'comb' AND fn.nspname = 'calx'"
        )
        assert cur.fetchall() == []


# --- registration -----------------------------------------------------------

def test_registering_populates_the_ground_set(conn):
    with conn.cursor() as cur:
        subject = _register(cur, "graph", 4)
        cur.execute("SELECT idx FROM comb.element WHERE structure_id = %s ORDER BY idx",
                    (_sid(cur, subject),))
        assert [r[0] for r in cur.fetchall()] == [0, 1, 2, 3]


def test_registering_a_structure_is_replayable(conn):
    with conn.cursor() as cur:
        subject = _register(cur, "graph", 3, source="first")
        first = _sid(cur, subject)
        _register(cur, "graph", 3, subject=subject, source="second")
        cur.execute("SELECT id, source FROM comb.structure WHERE subject_id = %s",
                    (subject,))
        again, source = cur.fetchone()
    assert again == first
    assert source == "second"


def test_growing_the_ground_set_tops_it_up(conn):
    with conn.cursor() as cur:
        subject = _register(cur, "graph", 2)
        _register(cur, "graph", 5, subject=subject)
        cur.execute("SELECT count(*) FROM comb.element WHERE structure_id = %s",
                    (_sid(cur, subject),))
        assert cur.fetchone()[0] == 5
        assert _wellformed(cur, subject)[0] is True


def test_shrinking_the_ground_set_is_refused(conn):
    """Silently deleting elements would orphan the blocks that reference them."""
    with conn.cursor() as cur:
        subject = _register(cur, "graph", 5)
        with pytest.raises(psycopg.Error, match="refusing to shrink"):
            _register(cur, "graph", 2, subject=subject)


def test_unknown_structure_is_reported_not_guessed(conn):
    with conn.cursor() as cur, pytest.raises(psycopg.Error, match="no structure"):
        cur.execute("SELECT comb.structure_id(%s)", (f"absent-{uuid.uuid4()}",))


# --- ingestion --------------------------------------------------------------

def test_replaying_an_edge_is_a_no_op(conn, graph):
    """A deposit script gets run more than once; it must not build a multigraph."""
    with conn.cursor() as cur:
        cur.execute("SELECT comb.add_edge(%s, 0, 1)", (graph,))
        replayed = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM comb.block WHERE structure_id = %s",
                    (_sid(cur, graph),))
        assert cur.fetchone()[0] == 3
    assert replayed == 0


def test_direction_survives_so_two_arcs_are_two_arcs(conn):
    """(0,1) and (1,0) share an element set and differ only in position."""
    with conn.cursor() as cur:
        subject = _register(cur, "digraph", 2)
        cur.execute("SELECT comb.add_arc(%s, 0, 1)", (subject,))
        cur.execute("SELECT comb.add_arc(%s, 1, 0)", (subject,))
        cur.execute("SELECT tail, head FROM comb.arc WHERE structure_id = %s"
                    " ORDER BY tail", (_sid(cur, subject),))
        assert cur.fetchall() == [(0, 1), (1, 0)]
        assert _wellformed(cur, subject)[0] is True


def test_a_block_is_a_set_not_a_multiset(conn):
    with conn.cursor() as cur:
        subject = _register(cur, "set_system", 3)
        with pytest.raises(psycopg.Error, match="repeats an element"):
            cur.execute("SELECT comb.add_block(%s, ARRAY[0,1,1])", (subject,))


def test_self_loops_are_refused_rather_than_silently_dropped(conn, graph):
    """The incidence key cannot represent one; a caller who asks must be told."""
    with conn.cursor() as cur, pytest.raises(psycopg.Error, match="self-loop"):
        cur.execute("SELECT comb.add_edge(%s, 1, 1)", (graph,))


def test_an_element_outside_the_ground_set_is_refused(conn, graph):
    with conn.cursor() as cur, pytest.raises(psycopg.Error, match="not in the ground set"):
        cur.execute("SELECT comb.add_block(%s, ARRAY[0,9])", (graph,))


def test_an_empty_block_is_refused(conn):
    with conn.cursor() as cur:
        subject = _register(cur, "set_system", 3)
        with pytest.raises(psycopg.Error, match="at least one element"):
            cur.execute("SELECT comb.add_block(%s, ARRAY[]::INTEGER[])", (subject,))


def test_positions_must_pair_with_elements(conn):
    with conn.cursor() as cur:
        subject = _register(cur, "digraph", 3)
        with pytest.raises(psycopg.Error, match="differ in length"):
            cur.execute(
                "SELECT comb.add_block(%s, ARRAY[0,1], ARRAY[0]::SMALLINT[])",
                (subject,),
            )


def test_deleting_a_structure_takes_its_contents_with_it(conn, graph):
    with conn.cursor() as cur:
        sid = _sid(cur, graph)
        cur.execute("DELETE FROM comb.structure WHERE id = %s", (sid,))
        for table in ("element", "block", "incidence"):
            cur.execute(f"SELECT count(*) FROM comb.{table} WHERE structure_id = %s",
                        (sid,))
            assert cur.fetchone()[0] == 0, table


# --- the views --------------------------------------------------------------

def test_edge_view_normalises_endpoints(conn, graph):
    with conn.cursor() as cur:
        cur.execute("SELECT u, v FROM comb.edge WHERE structure_id = %s ORDER BY u, v",
                    (_sid(cur, graph),))
        assert cur.fetchall() == [(0, 1), (0, 2), (1, 2)]


def test_edge_view_omits_blocks_that_are_not_2_uniform(conn):
    """A hyperedge is absent from comb.edge rather than misreported as an edge."""
    with conn.cursor() as cur:
        subject = _register(cur, "hypergraph", 4)
        cur.execute("SELECT comb.add_block(%s, ARRAY[0,1,2])", (subject,))
        cur.execute("SELECT comb.add_block(%s, ARRAY[2,3])", (subject,))
        cur.execute("SELECT u, v FROM comb.edge WHERE structure_id = %s",
                    (_sid(cur, subject),))
        assert cur.fetchall() == [(2, 3)]


# --- well-formedness --------------------------------------------------------

def test_a_triangle_is_well_formed(conn, graph):
    with conn.cursor() as cur:
        ok, ev = _wellformed(cur, graph)
    assert ok is True
    assert (ev["kind"], ev["ground_n"], ev["blocks"]) == ("graph", 3, 3)


def test_the_empty_graph_on_n_vertices_is_well_formed(conn):
    """Otherwise register-then-populate could not pass its own check midway."""
    with conn.cursor() as cur:
        assert _wellformed(cur, _register(cur, "graph", 5))[0] is True


def test_a_mixed_arity_family_is_well_formed(conn):
    with conn.cursor() as cur:
        subject = _register(cur, "set_system", 5)
        for block in ("ARRAY[0,1,2]", "ARRAY[3]", "ARRAY[0,1,2,3,4]"):
            cur.execute(f"SELECT comb.add_block(%s, {block})", (subject,))
        ok, ev = _wellformed(cur, subject)
    assert ok is True
    assert ev["blocks"] == 3


def test_an_unknown_structure_is_not_well_formed(conn):
    with conn.cursor() as cur:
        ok, ev = _wellformed(cur, f"absent-{uuid.uuid4()}")
    assert ok is False
    assert ev["reason"] == "no such structure"


def test_a_hyperedge_in_a_graph_is_refuted(conn):
    with conn.cursor() as cur:
        subject = _register(cur, "graph", 4)
        cur.execute("SELECT comb.add_block(%s, ARRAY[0,1,2])", (subject,))
        ok, ev = _wellformed(cur, subject)
    assert ok is False
    assert "block arity is not 2" in _faults(ev)


def test_an_unordered_block_in_a_digraph_is_refuted(conn):
    with conn.cursor() as cur:
        subject = _register(cur, "digraph", 3)
        cur.execute("SELECT comb.add_block(%s, ARRAY[0,1])", (subject,))
        ok, ev = _wellformed(cur, subject)
    assert ok is False
    assert "digraph block does not carry positions {0,1}" in _faults(ev)


def test_a_positioned_block_in_a_graph_is_refuted(conn):
    with conn.cursor() as cur:
        subject = _register(cur, "graph", 3)
        cur.execute("SELECT comb.add_block(%s, ARRAY[0,1], ARRAY[0,1]::SMALLINT[])",
                    (subject,))
        ok, ev = _wellformed(cur, subject)
    assert ok is False
    assert "graph block carries positions" in _faults(ev)


def test_a_declared_ground_n_that_drifts_is_refuted(conn):
    """ground_n is the declaration and comb.element is the fact."""
    with conn.cursor() as cur:
        subject = _register(cur, "graph", 4)
        cur.execute("DELETE FROM comb.element WHERE structure_id = %s AND idx = 3",
                    (_sid(cur, subject),))
        ok, ev = _wellformed(cur, subject)
    assert ok is False
    assert "ground_n disagrees with the elements on record" in _faults(ev)


def test_parallel_edges_refute_a_simple_graph(conn):
    """add_block dedupes, so this can only arrive by direct insert — which is
    exactly the tampering the probe is here to catch."""
    with conn.cursor() as cur:
        subject = _register(cur, "graph", 3)
        cur.execute("SELECT comb.add_edge(%s, 0, 1)", (subject,))
        sid = _sid(cur, subject)
        cur.execute("INSERT INTO comb.block (structure_id, idx) VALUES (%s, 1)", (sid,))
        cur.execute(
            "INSERT INTO comb.incidence (structure_id, block_idx, element_idx)"
            " VALUES (%s, 1, 0), (%s, 1, 1)", (sid, sid))
        ok, ev = _wellformed(cur, subject)
    assert ok is False
    assert "parallel blocks in a simple structure" in _faults(ev)


def test_duplicate_sets_in_a_family_are_surfaced_but_not_refuted(conn):
    """A family may legitimately be reported with a repeat; a simple graph may
    not. The probe reports the first and refutes only the second."""
    with conn.cursor() as cur:
        subject = _register(cur, "set_system", 3)
        cur.execute("SELECT comb.add_block(%s, ARRAY[0,1])", (subject,))
        sid = _sid(cur, subject)
        cur.execute("INSERT INTO comb.block (structure_id, idx) VALUES (%s, 1)", (sid,))
        cur.execute(
            "INSERT INTO comb.incidence (structure_id, block_idx, element_idx)"
            " VALUES (%s, 1, 0), (%s, 1, 1)", (sid, sid))
        ok, ev = _wellformed(cur, subject)
    assert ok is True
    assert ev["duplicate_blocks"][0]["block_idxs"] == [0, 1]


# --- the claim --------------------------------------------------------------

def test_wellformed_claim_is_recheckable(conn, graph):
    with conn.cursor() as cur:
        cur.execute("SELECT comb.wellformed_claim(%s)", (graph,))
        claim = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (claim,))
        assert cur.fetchone()[0] == "valid"

        # Editing the object after it was accepted must stop it reading as
        # accepted — the reason this is a claim and not a one-off report.
        cur.execute("SELECT comb.add_block(%s, ARRAY[0,1,2])", (graph,))
        cur.execute("SELECT (cert.check(%s)).status", (claim,))
        assert cur.fetchone()[0] == "refuted"


def test_canonical_digest_is_recorded_with_its_tool(conn, graph):
    """A digest is a dedup hint from an external canonicaliser, so the tool
    travels with it and neither decides a verdict."""
    with conn.cursor() as cur:
        cur.execute("SELECT (comb.set_canonical(%s, %s, %s)).canon_digest",
                    (graph, "deadbeef", "nauty 2.8.9"))
        assert cur.fetchone()[0] == "deadbeef"
        cur.execute("SELECT canon_tool FROM comb.structure WHERE subject_id = %s",
                    (graph,))
        assert cur.fetchone()[0] == "nauty 2.8.9"
