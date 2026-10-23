"""Public bundle exports retain every derivation without duplicating claims."""

from __future__ import annotations

import json
import uuid

import psycopg
import pytest

from calx.bundle import validate_bundle, verify_bundle
from calx.proposition import witness_subject_ref


@pytest.fixture
def export_conn(calx_dsn):
    # The explicit test DSN guard is supplied by conftest; never resolve a
    # default DSN. CI bootstraps the unified schema before these tests.
    with psycopg.connect(calx_dsn, connect_timeout=3) as conn:
        assert conn.execute(
            "SELECT to_regprocedure('cert.export_bundle(bigint[])')",
        ).fetchone()[0], "apply the unified schema to the dedicated test database"
        try:
            yield conn
        finally:
            conn.rollback()


def _claim(conn, *, history=False):
    body = {"schema": "factorization", "n": 12, "factors": [[2, 2], [3, 1]]}
    claim = conn.execute(
        "INSERT INTO cert.claim "
        "(subject_kind, subject_ref, statement, claim_kind, method) "
        "VALUES ('export_derivations_test', %s::jsonb, %s, 'computational', 'comp_sql') "
        "RETURNING id",
        (json.dumps(witness_subject_ref(body)), f"export derivations {uuid.uuid4()}"),
    ).fetchone()[0]
    if history:
        conn.execute(
            "INSERT INTO cert.certificate (claim_id, seq, status) VALUES (%s, 1, 'unverified')",
            (claim,),
        )
    certificate = conn.execute(
        "INSERT INTO cert.certificate (claim_id, seq, status) VALUES (%s, %s, 'valid') "
        "RETURNING id", (claim, 2 if history else 1),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO cert.witness (certificate_id, kind, body) VALUES (%s, 'term', %s::jsonb)",
        (certificate, json.dumps(body)),
    )
    return claim


def _derive(conn, conclusion, premises, rule):
    return conn.execute(
        "INSERT INTO cert.derivation (conclusion_id, premise_ids, rule) VALUES (%s, %s, %s) "
        "RETURNING id", (conclusion, premises, rule),
    ).fetchone()[0]


def _export(conn, claims):
    return conn.execute("SELECT cert.export_bundle(%s::bigint[])", (claims,)).fetchone()[0]


def test_multiple_derivations_export_one_entry_and_all_rule_metadata(export_conn):
    conn = export_conn
    left, right, top = _claim(conn), _claim(conn), _claim(conn, history=True)
    first = _derive(conn, top, [left, left], "first_rule")
    second = _derive(conn, top, [right, left], "second_rule")
    empty = _derive(conn, top, [], "empty_rule")
    bundle = _export(conn, [top, left, right, top])
    validate_bundle(bundle)
    assert sorted(entry["claim"]["id"] for entry in bundle["claims"]) == sorted([left, right, top])
    entry = next(entry for entry in bundle["claims"] if entry["claim"]["id"] == top)
    derivation = entry["derivation"]
    assert derivation["premise_ids"] == sorted([left, right])
    assert derivation["rule"] == "all_derivations" and derivation["combination"] == "all"
    assert [(rule["id"], rule["rule"], rule["premise_ids"]) for rule in derivation["rules"]] == [
        (first, "first_rule", [left, left]),
        (second, "second_rule", [right, left]),
        (empty, "empty_rule", []),
    ]
    assert all(rule["asserted_at"] for rule in derivation["rules"])
    assert [item["status"] for item in entry["certificate_history"]] == ["unverified", "valid"]
    assert all(result.ok is True for result in verify_bundle(bundle))


def test_revoked_premise_in_either_derivation_degrades_conclusion(export_conn):
    conn = export_conn
    left, right, top = _claim(conn), _claim(conn), _claim(conn)
    _derive(conn, top, [left], "first_rule")
    _derive(conn, top, [right], "second_rule")
    conn.execute("SELECT cert.revoke_claim(%s, 'test revocation')", (right,))
    bundle = _export(conn, [left, right, top])
    results = {result.claim_id: result for result in verify_bundle(bundle)}
    assert results[left].ok is True
    assert results[right].ok is None
    assert results[top].ok is None
    right_entry = next(entry for entry in bundle["claims"] if entry["claim"]["id"] == right)
    assert right_entry["revocation"]["reason"] == "test revocation"
    assert conn.execute(
        "SELECT ok FROM cert.derivation_valid_deep(%s)", (top,),
    ).fetchone()[0] is False


def test_omitted_premises_are_retained_without_implicit_expansion(export_conn):
    conn = export_conn
    left, right, top = _claim(conn), _claim(conn), _claim(conn)
    _derive(conn, top, [left], "first_rule")
    _derive(conn, top, [right], "second_rule")
    bundle = _export(conn, [top])
    assert len(bundle["claims"]) == 1
    assert bundle["claims"][0]["derivation"]["premise_ids"] == sorted([left, right])
    result, = verify_bundle(bundle)
    assert result.ok is None
    assert sum("not in bundle" in note for note in result.notes) == 2


def test_single_empty_derivation_preserves_rule_and_artifact(export_conn):
    conn, top = export_conn, _claim(export_conn)
    _derive(conn, top, [], "original_rule")
    conn.execute(
        "INSERT INTO cert.artifact (claim_id, kind, path, sha256, checker_cmd) "
        "VALUES (%s, 'lean', 'missing-test-artifact.lean', 'test-hash', 'test-checker')", (top,),
    )
    bundle = _export(conn, [top])
    entry, = bundle["claims"]
    assert entry["derivation"]["premise_ids"] == []
    assert entry["derivation"]["rule"] == "original_rule"
    assert entry["artifact"]["path"] == "missing-test-artifact.lean"
    assert entry["artifact"]["checker_cmd"] == "test-checker"
