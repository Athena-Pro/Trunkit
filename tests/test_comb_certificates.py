"""Live SQL/offline equivalence and adversarial regressions for comb.v1.

Only an explicitly supplied test DSN is used. The portable kernel is tested
elsewhere without PostgreSQL; these cases ensure the SQL producer speaks the
same exact schema and never confuses pointer resolution with optimality.
"""

from __future__ import annotations

import json
import os
import uuid

import psycopg
import pytest

from calx import combinatorial as comb
from calx.bundle import verify_bundle


@pytest.fixture()
def conn():
    dsn = os.environ.get("CALX_TEST_DSN") or os.environ.get("ARITHMETIC_DB_TEST_DSN")
    if not dsn:
        pytest.skip("No explicit test DSN provided")
    with psycopg.connect(dsn, autocommit=True) as connection:
        with connection.cursor() as cur:
            cur.execute("SELECT to_regprocedure('comb.structure_digest(text)')")
            if cur.fetchone()[0] is None:
                pytest.skip("apply 120_comb_certificates.sql")
        yield connection


def _structure(cur, n, blocks=(), kind="graph"):
    subject = f"certificate-test-{uuid.uuid4()}"
    cur.execute("SELECT comb.register_structure(%s,%s,%s)", (subject, kind, n))
    for block in blocks:
        cur.execute("SELECT comb.add_block(%s,%s::integer[])", (subject, list(block)))
    return subject


def _check(cur, subject, predicate, certificate=None):
    cur.execute("SELECT ok,evidence,witness FROM comb.check_certificate(%s,%s::jsonb,%s::jsonb)",
                (subject, json.dumps(predicate), json.dumps(certificate or {})))
    return cur.fetchone()


@pytest.mark.parametrize(("predicate", "certificate", "expected"), [
    ({"name": "triangle_free"}, {}, True),
    ({"name": "uniform", "k": 2}, {}, True),
    ({"name": "uniform", "k": 3}, {}, False),
    ({"name": "colouring", "k": 3}, {"colouring": [0, 1, 0, 1, 2]}, True),
    ({"name": "colouring", "k": 2}, {"colouring": [0, 1, 0, 1, 0]}, None),
    ({"name": "independent_set", "k": 2}, {"vertices": [0, 2]}, True),
    ({"name": "independent_set", "k": 3}, {"vertices": [0, 2]}, None),
    ({"name": "clique", "k": 2}, {"vertices": [0, 1]}, True),
    ({"name": "clique", "k": 3}, {"vertices": [0, 1, 2]}, None),
    ({"name": "odd_cycle"}, {"vertices": [0, 1, 2, 3, 4]}, True),
    ({"name": "odd_cycle"}, {"vertices": [0, 1, 2]}, None),
    ({"name": "matching", "k": 2}, {"matching": [0, 2]}, True),
    ({"name": "matching", "k": 2}, {"matching": [0, 1]}, None),
    ({"name": "vertex_cover", "k": 3}, {"vertices": [0, 2, 4]}, True),
    ({"name": "hitting_set", "k": 2}, {"vertices": [0, 2]}, None),
    ({"name": "t_intersecting", "t": 1}, {}, False),
    ({"name": "codegree", "vertices": [0], "value": 2}, {}, True),
    ({"name": "codegree_at_most", "t": 2, "max": 1}, {}, True),
    ({"name": "t_design", "t": 1, "k": 2, "lambda": 2}, {}, True),
])
def test_sql_and_portable_kernel_agree(conn, predicate, certificate, expected):
    with conn.cursor() as cur:
        subject = _structure(cur, 5, [(0, 1), (1, 2), (2, 3), (3, 4), (0, 4)])
        ok, _, witness = _check(cur, subject, predicate, certificate)
        assert witness["instance_digest"] == comb.instance_digest(witness["instance"])
        assert ok is expected
        assert comb.check_combinatorial(witness)[0] is expected


def test_snapshot_claim_refutes_size_drift_even_when_property_still_holds(conn):
    with conn.cursor() as cur:
        subject = _structure(cur, 4, [(0, 1)])
        cur.execute("SELECT comb.construction_claim(%s,'triangle_free')", (subject,))
        claim = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (claim,))
        assert cur.fetchone()[0] == "valid"
        cur.execute("SELECT comb.add_edge(%s,2,3)", (subject,))
        assert _check(cur, subject, {"name": "triangle_free"})[0] is True
        cur.execute("SELECT (cert.check(%s)).status", (claim,))
        assert cur.fetchone()[0] == "refuted"


def test_sql_export_is_independently_checked_without_database(conn):
    with conn.cursor() as cur:
        subject = _structure(cur, 3, [(0, 1), (1, 2), (0, 2)])
        cur.execute("SELECT comb.clique_claim(%s,ARRAY[0,1,2])", (subject,))
        claim = cur.fetchone()[0]
        cur.execute("SELECT cert.check_with_witness(%s)", (claim,))
        cur.execute("SELECT cert.export_bundle(ARRAY[%s]::bigint[])", (claim,))
        bundle = cur.fetchone()[0]
    result = verify_bundle(bundle, None)[0]
    assert result.ok is True, result.notes


def test_empty_set_member_affects_primitivity_intersection_and_colouring(conn):
    with conn.cursor() as cur:
        subject = _structure(cur, 2, [(), (0,)], "set_system")
        cases = [({"name": "wellformed"}, {}, True),
                 ({"name": "primitive_family"}, {}, False),
                 ({"name": "intersecting_family"}, {}, False),
                 ({"name": "colouring", "k": 2}, {"colouring": [0, 1]}, None),
                 ({"name": "hitting_set"}, {"vertices": [0, 1]}, None)]
        for predicate, certificate, expected in cases:
            ok, _, witness = _check(cur, subject, predicate, certificate)
            assert ok is expected
            assert comb.check_combinatorial(witness)[0] is expected


def test_empty_ground_set_has_typed_empty_witnesses(conn):
    with conn.cursor() as cur:
        subject = _structure(cur, 0)
        cur.execute("SELECT comb.register_witness(%s,'empty','subset',ARRAY[]::integer[])",
                    (subject,))
        cur.execute("SELECT ok FROM comb.subset_is_independent(%s,'empty')", (subject,))
        assert cur.fetchone()[0] is True
        assert _check(cur, subject, {"name": "colouring", "k": 0}, {"colouring": []})[0] is True


def test_coincident_nonadjacent_vertices_do_not_realise_a_graph(conn):
    with conn.cursor() as cur:
        graph = _structure(cur, 3, [(0, 1), (1, 2)])
        config = f"config-{uuid.uuid4()}"
        cur.execute("SELECT comb.register_configuration(%s,1,'rational')", (config,))
        for idx, value in enumerate([0, 1, 0]):
            cur.execute("SELECT comb.add_rational_point(%s,%s,%s::numeric[])",
                        (config, idx, [value]))
        cur.execute("SELECT ok,evidence FROM comb.is_unit_distance(%s,%s)", (config, graph))
        ok, evidence = cur.fetchone()
        assert ok is False
        assert evidence["reason"] == "two points coincide"


def test_nonreal_number_field_cannot_certify_euclidean_geometry(conn):
    with conn.cursor() as cur:
        graph = _structure(cur, 2, [(0, 1)])
        config, field = f"config-{uuid.uuid4()}", f"field-{uuid.uuid4()}"
        cur.execute("SELECT comb.register_field(%s,ARRAY[1,0,1]::numeric[])", (field,))
        cur.execute("SELECT comb.register_configuration(%s,3,'algebraic',%s)", (config, field))
        for idx, coordinates in enumerate([[[0, 0]] * 3, [[0, 1], [1, 0], [1, 0]]]):
            for axis, value in enumerate(coordinates):
                cur.execute("SELECT comb.set_coordinate(%s,%s,%s,%s::numeric[])",
                            (config, idx, axis, value))
        cur.execute("SELECT ok FROM comb.sq_distance_is(%s,0,1,1)", (config,))
        assert cur.fetchone()[0] is True  # exact identity in a complex field
        cur.execute("SELECT ok FROM comb.is_unit_distance(%s,%s)", (config, graph))
        assert cur.fetchone()[0] is None  # no real Euclidean coordinates


def test_oversized_coordinate_vectors_are_rejected(conn):
    with conn.cursor() as cur:
        config = f"config-{uuid.uuid4()}"
        cur.execute("SELECT comb.register_configuration(%s,1,'rational')", (config,))
        with pytest.raises(psycopg.Error, match="wider than field degree"):
            cur.execute("SELECT comb.set_coordinate(%s,0,0,ARRAY[1,999]::numeric[])", (config,))


def test_large_primitive_integers_remain_exact_and_export_portably(conn):
    with conn.cursor() as cur:
        subject = _structure(cur, 2, kind="set_system")
        huge = 10**100 + 267
        cur.execute("SELECT comb.label_elements(%s,%s)", (subject, [str(huge), str(huge * 2)]))
        cur.execute("SELECT ok,evidence,witness FROM comb.carried_primitive_set(%s)", (subject,))
        ok, evidence, witness = cur.fetchone()
        assert ok is False
        assert evidence["violations"] == 1
        assert comb.check_combinatorial(witness)[0] is False


def test_relabel_and_disjoint_union_preserve_directions_and_provenance(conn):
    with conn.cursor() as cur:
        source = _structure(cur, 3, kind="digraph")
        cur.execute("SELECT comb.add_arc(%s,2,0)", (source,))
        renamed, union = f"relabel-{uuid.uuid4()}", f"union-{uuid.uuid4()}"
        cur.execute("SELECT comb.relabel(%s,%s,ARRAY[2,0,1])", (source, renamed))
        cur.execute("SELECT tail,head FROM comb.arc WHERE structure_id=comb.structure_id(%s)",
                    (renamed,))
        assert cur.fetchone() == (1, 2)
        cur.execute("SELECT comb.disjoint_union(%s,%s,%s)", (renamed, source, union))
        cur.execute("SELECT ground_n,provenance->>'operation' FROM comb.structure"
                    " WHERE subject_id=%s", (union,))
        assert cur.fetchone() == (6, "disjoint_union")
        cur.execute("SELECT tail,head FROM comb.arc WHERE structure_id=comb.structure_id(%s)"
                    " ORDER BY tail", (union,))
        assert cur.fetchall() == [(1, 2), (5, 3)]


def test_fano_plane_is_a_design_and_missing_pair_is_detected(conn):
    with conn.cursor() as cur:
        subject = _structure(cur, 7, [(0, 1, 2), (0, 3, 4), (0, 5, 6), (1, 3, 5),
                                     (1, 4, 6), (2, 3, 6), (2, 4, 5)], "hypergraph")
        predicate = {"name": "t_design", "t": 2, "k": 3, "lambda": 1}
        ok, _, witness = _check(cur, subject, predicate)
        assert ok is True
        assert comb.check_combinatorial(witness)[0] is True
        cur.execute("DELETE FROM comb.block WHERE structure_id=comb.structure_id(%s) AND idx=0",
                    (subject,))
        ok, _, witness = _check(cur, subject, predicate)
        assert ok is False
        assert comb.check_combinatorial(witness)[0] is False


def test_family_subset_budget_returns_unknown(conn):
    with conn.cursor() as cur:
        subject = _structure(cur, 40, kind="set_system")
        assert _check(cur, subject, {"name": "t_design", "t": 20, "k": 20, "lambda": 0})[0] is None


def _typed_bound_case(cur):
    subject = _structure(cur, 4, [(0, 1), (1, 2), (2, 3), (0, 3)])
    quantity = f"quantity-{uuid.uuid4()}"
    cur.execute("SELECT cert.register_quantity(%s,"
                " 'maximum triangle-free edge count on 4 vertices')", (quantity,))
    cur.execute("SELECT comb.register_objective(%s,'edge_count','max',"
                " '{\"name\":\"triangle_free\"}'::jsonb,'graph',4)", (quantity,))
    cur.execute("SELECT (cert.register_bound(%s,'lower',4)).id", (quantity,))
    lower = cur.fetchone()[0]
    cur.execute("SELECT comb.bind_attainment(%s,%s)", (lower, subject))
    cur.execute("SELECT (cert.register_bound(%s,'upper',4)).id", (quantity,))
    upper = cur.fetchone()[0]
    return subject, lower, upper


def _external_bound(cur, bound):
    """A test fixture stands in for a formal checker and its closure audit."""
    cur.execute("SELECT comb.register_bound_claim(%s)", (bound,))
    claim = cur.fetchone()[0]
    declaration = f"test.bound_{claim}"
    manifest = json.dumps({declaration: ["a" * 64, "b" * 64]})
    cur.execute("SELECT cert.closure_manifest_digest(%s::jsonb)", (manifest,))
    digest = cur.fetchone()[0]
    cur.execute("SELECT cert.bind_statement_closure(%s,%s,'test fixture bound',"
                " %s,%s::jsonb,'test')",
                (claim, declaration, digest, manifest))
    cur.execute("SELECT cert.observe_statement_closure(%s,%s,%s::jsonb,"
                " 'test fixture bound',%s,'test')", (claim, digest, manifest, digest))
    cur.execute("INSERT INTO cert.certificate(claim_id,seq,status,evidence)"
                " SELECT %s,COALESCE(max(seq),0)+1,'valid',"
                " '{\"external_checker\":\"test fixture\"}'::jsonb"
                " FROM cert.certificate WHERE claim_id=%s", (claim, claim))
    return claim


def test_optimum_requires_matching_typed_opposite_proposition(conn):
    with conn.cursor() as cur:
        subject, lower, upper = _typed_bound_case(cur)
        cur.execute("SELECT comb.construction_claim(%s,'triangle_free')", (subject,))
        unrelated = cur.fetchone()[0]
        cur.execute("SELECT cert.check_with_witness(%s)", (unrelated,))
        cur.execute("UPDATE cert.bound SET claim_id=%s WHERE id=%s", (unrelated, upper))
        with pytest.raises(psycopg.Error, match="dedicated typed formal"):
            cur.execute("SELECT comb.bind_bound_claim(%s,%s)", (upper, unrelated))
        cur.execute("SELECT ok FROM comb.exact_optimal(%s)", (lower,))
        assert cur.fetchone()[0] is None
        upper_proof = _external_bound(cur, upper)
        cur.execute("SELECT ok FROM comb.exact_optimal(%s)", (lower,))
        assert cur.fetchone()[0] is True
        cur.execute("SELECT comb.attest_attainment(%s)", (lower,))
        exact = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (exact,))
        assert cur.fetchone()[0] == "valid"
        cur.execute("SELECT premise_ids FROM cert.derivation WHERE conclusion_id=%s", (exact,))
        assert upper_proof in cur.fetchone()[0]
        cur.execute("UPDATE cert.bound SET value=3 WHERE id=%s", (lower,))
        cur.execute("SELECT (cert.check(%s)).status", (exact,))
        assert cur.fetchone()[0] == "refuted"


def test_attainment_does_not_assume_the_measured_objective_or_feasibility(conn):
    with conn.cursor() as cur:
        subject, lower, _ = _typed_bound_case(cur)
        cur.execute("UPDATE cert.bound SET value=5 WHERE id=%s", (lower,))
        cur.execute("SELECT ok FROM comb.bound_attainment(%s)", (lower,))
        assert cur.fetchone()[0] is False
        cur.execute("UPDATE cert.bound SET value=4 WHERE id=%s", (lower,))
        cur.execute("SELECT comb.add_edge(%s,0,2)", (subject,))
        cur.execute("SELECT ok FROM comb.bound_attainment(%s)", (lower,))
        assert cur.fetchone()[0] is False


@pytest.mark.parametrize(("predicate", "certificate"), [
    ({"name": "colouring", "k": 2}, {"colouring": [-4, 99]}),
    ({"name": "colouring", "k": 2}, {"colouring": [0, None]}),
    ({"name": "uniform", "k": "2"}, {}),
    ({"name": "uniform", "k": 2.0}, {}),
    ({"name": "uniform", "k": 2, "unsupported": True}, {}),
    ({"name": "clique", "k": 2}, {"vertices": [0, 0]}),
])
def test_unsupported_or_malformed_candidate_is_unknown(conn, predicate, certificate):
    with conn.cursor() as cur:
        subject = _structure(cur, 2, [(0, 1)])
        ok, _, witness = _check(cur, subject, predicate, certificate)
        assert ok is None
        assert comb.check_combinatorial(witness)[0] is None


@pytest.mark.parametrize("field", ["kind", "ground_n", "elements", "blocks"])
@pytest.mark.parametrize("missing", [True, False])
def test_isomorphism_rejects_missing_or_null_target_shape_fields(conn, field, missing):
    with conn.cursor() as cur:
        subject = _structure(cur, 0)
        target = {"kind": "graph", "ground_n": 0, "elements": [], "blocks": []}
        if missing:
            target.pop(field)
        else:
            target[field] = None
        cur.execute("SELECT comb.instance_wellformed(%s::jsonb),comb.instance_digest(%s::jsonb)",
                    (json.dumps(target), json.dumps(target)))
        valid_shape, target_digest = cur.fetchone()
        assert valid_shape is False
        ok, _, witness = _check(cur, subject,
                                {"name": "isomorphism", "target_digest": target_digest},
                                {"target": target, "mapping": []})
        assert ok is None
        assert comb.check_combinatorial(witness)[0] is None


def test_relabel_and_union_preserve_repeated_and_empty_family_blocks(conn):
    with conn.cursor() as cur:
        subject = _structure(cur, 1, [(), (0,)], "set_system")
        cur.execute("INSERT INTO comb.block(structure_id,idx)"
                    " VALUES(comb.structure_id(%s),2)", (subject,))
        cur.execute("INSERT INTO comb.incidence(structure_id,block_idx,element_idx)"
                    " VALUES(comb.structure_id(%s),2,0)", (subject,))
        renamed, union = f"relabel-{uuid.uuid4()}", f"union-{uuid.uuid4()}"
        cur.execute("SELECT comb.relabel(%s,%s,ARRAY[0])", (subject, renamed))
        cur.execute("SELECT comb.disjoint_union(%s,%s,%s)", (subject, renamed, union))
        cur.execute("SELECT count(*) FROM comb.block_sets(%s)", (renamed,))
        assert cur.fetchone()[0] == 3
        cur.execute("SELECT count(*),count(*) FILTER(WHERE size=0) FROM comb.block_sets(%s)",
                    (union,))
        assert cur.fetchone() == (6, 2)


def test_realisation_sql_witness_can_be_checked_offline(conn):
    with conn.cursor() as cur:
        graph = _structure(cur, 2, [(0, 1)])
        config = f"config-{uuid.uuid4()}"
        cur.execute("SELECT comb.register_configuration(%s,1,'rational')", (config,))
        for idx, value in enumerate([0, 1]):
            cur.execute("SELECT comb.add_rational_point(%s,%s,%s::numeric[])",
                        (config, idx, [value]))
        cur.execute("SELECT ok,evidence,witness FROM comb.carried_realisation(%s,%s)",
                    (config, graph))
        ok, _, witness = cur.fetchone()
        assert ok is True
        assert comb.instance_digest(witness["instance"]) == witness["instance_digest"]
        assert comb.check_combinatorial(witness)[0] is True


def test_exact_optimum_loses_opposite_proof_on_revocation_or_mutation(conn):
    with conn.cursor() as cur:
        _, lower, upper = _typed_bound_case(cur)
        proof = _external_bound(cur, upper)
        cur.execute("SELECT comb.attest_attainment(%s)", (lower,))
        exact = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (exact,))
        assert cur.fetchone()[0] == "valid"
        cur.execute("SELECT cert.revoke_claim(%s,'test external proof revoked')", (proof,))
        cur.execute("SELECT (cert.check(%s)).status", (exact,))
        assert cur.fetchone()[0] == "unverified"
        cur.execute("UPDATE cert.bound SET value=5 WHERE id=%s", (upper,))
        cur.execute("SELECT ok FROM comb.exact_optimal(%s)", (lower,))
        assert cur.fetchone()[0] is None


def test_formal_bound_certificate_without_statement_closure_is_unknown(conn):
    with conn.cursor() as cur:
        _, lower, upper = _typed_bound_case(cur)
        cur.execute("SELECT comb.register_bound_claim(%s)", (upper,))
        proof = cur.fetchone()[0]
        cur.execute("INSERT INTO cert.certificate(claim_id,seq,status) VALUES(%s,1,'valid')",
                    (proof,))
        cur.execute("SELECT ok FROM comb.exact_optimal(%s)", (lower,))
        assert cur.fetchone()[0] is None
