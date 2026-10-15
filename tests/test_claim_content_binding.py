"""Claim changes cannot leave a carried certificate bound to a different meaning."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

import psycopg
import pytest
from psycopg.types.json import Jsonb

from calx.ledger import (
    CLAIM_DIGEST_KIND,
    certificate_row_hash,
    claim_binding_ok,
    claim_digest,
    verify_chain,
)


def _claim():
    return {
        "id": 1, "subject_kind": "test", "subject_ref": {"n": 4},
        "statement": "2 + 2 = 4", "claim_kind": "computational", "method": "comp_sql",
        "probe_sql": "SELECT 2 + 2 = 4, '{}'::jsonb", "domain": "exact_int",
    }


def _certificate(claim):
    cert = {"seq": 1, "status": "valid", "evidence": {}, "valid_under": {
        "claim_digest_kind": CLAIM_DIGEST_KIND, "claim_digest": claim_digest(claim),
    }}
    cert["row_hash"] = certificate_row_hash(
        claim["id"], 1, "valid", {}, cert["valid_under"], None, None, None,
    )
    return cert


@pytest.mark.parametrize("field,value", [
    ("statement", "2 + 2 = 5"), ("subject_ref", {"n": 5}),
    ("subject_kind", "other"), ("method", "formal_external"),
    ("claim_kind", "formal"), ("probe_sql", "SELECT TRUE"),
    ("domain", "float_heuristic"), ("id", 2),
])
def test_semantic_mutation_breaks_claim_and_chain_binding(field, value):
    claim = _claim()
    cert = _certificate(claim)
    assert claim_binding_ok(claim, cert) is True
    claim[field] = value
    assert claim_binding_ok(claim, cert) is False
    result = verify_chain({"claims": [{"claim": claim, "certificate": cert}]})[0]
    assert result["content_ok"] is False
    assert result["claim_bound"] is False


def test_legacy_claim_binding_is_explicitly_unknown():
    assert claim_binding_ok(_claim(), {"valid_under": {}}) is None


def test_claim_binding_rejects_unknown_or_removed_digest_kind():
    claim = _claim()
    cert = _certificate(claim)
    cert["valid_under"]["claim_digest_kind"] = "unknown"
    assert claim_binding_ok(claim, cert) is False
    del cert["valid_under"]["claim_digest_kind"]
    assert claim_binding_ok(claim, cert) is False


def test_claim_digest_normalizes_number_scale_and_ignores_display_metadata():
    claim = _claim()
    other = deepcopy(claim)
    other["subject_ref"] = {"n": Decimal("4.000")}
    other["created_at"] = "display-only"
    assert claim_digest(claim) == claim_digest(other)


@pytest.mark.parametrize("subject_ref", [
    {"n": 4}, {"é": "数学", "longer": [True, None, "line\nquote\""]},
    {"ratio": 1.25, "whole": 4.0, "small": 1e-7, "large": 1e25},
])
def test_sql_python_claim_digest_parity(calx_dsn, subject_ref):
    claim = _claim()
    claim["subject_ref"] = subject_ref
    claim["created_at"] = "display-only"
    with psycopg.connect(calx_dsn) as conn:
        actual = conn.execute("SELECT cert.claim_digest(%s)", (Jsonb(claim),)).fetchone()[0]
    assert actual == claim_digest(claim)


def test_certificate_pins_immutable_claim_version(calx_dsn):
    with psycopg.connect(calx_dsn) as conn:
        claim = conn.execute(
            "INSERT INTO cert.claim AS c"
            " (subject_kind,subject_ref,statement,claim_kind,method,probe_sql)"
            " VALUES ('binding_test','{}',gen_random_uuid()::text,'computational','comp_sql',"
            " 'SELECT TRUE, ''{}''::jsonb') RETURNING to_jsonb(c)"
        ).fetchone()[0]
        cert = conn.execute(
            "INSERT INTO cert.certificate AS c (claim_id,seq,status) VALUES (%s,1,'valid')"
            " RETURNING to_jsonb(c)", (claim["id"],),
        ).fetchone()[0]
        assert claim_binding_ok(claim, cert) is True
        snapshot = conn.execute(
            "SELECT content FROM cert.claim_version WHERE claim_id=%s", (claim["id"],),
        ).fetchone()[0]
        assert claim_digest(snapshot) == cert["valid_under"]["claim_digest"]
        conn.execute("UPDATE cert.claim SET statement=gen_random_uuid()::text WHERE id=%s",
                     (claim["id"],))
        changed = conn.execute("SELECT to_jsonb(c) FROM cert.claim c WHERE id=%s",
                               (claim["id"],)).fetchone()[0]
        assert claim_binding_ok(changed, cert) is False
        refreshed = conn.execute(
            "INSERT INTO cert.certificate AS c (claim_id,seq,status) VALUES (%s,2,'valid')"
            " RETURNING to_jsonb(c)", (claim["id"],),
        ).fetchone()[0]
        assert claim_binding_ok(changed, refreshed) is True
        assert conn.execute("SELECT count(*) FROM cert.claim_version WHERE claim_id=%s",
                            (claim["id"],)).fetchone()[0] == 2
        with pytest.raises(psycopg.Error, match="append-only"), conn.transaction():
            conn.execute("DELETE FROM cert.claim_version WHERE claim_id=%s", (claim["id"],))
        with pytest.raises(psycopg.Error, match="append-only"), conn.transaction():
            conn.execute("TRUNCATE cert.claim_version")
        conn.rollback()
