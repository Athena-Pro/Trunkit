"""DB-backed tests for the cert vision layer (91_cert_image.sql).

Registers descriptor vectors directly (no Pillow needed), builds comp_sql match
claims via cert.image_match_claim, runs cert.check, and asserts the three-valued
outcome. Skips cleanly when the calx DB is unreachable.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from calx import imagefeatures as imf


def _calx_dsn():
    dsn = os.environ.get("CALX_TEST_DSN") or os.environ.get("ARITHMETIC_DB_TEST_DSN")
    if not dsn:
        pytest.skip("No test DSN provided. Refusing to write to default/production ledger.")
    return dsn


@pytest.fixture()
def conn():
    dsn = _calx_dsn()
    try:
        c = psycopg.connect(dsn, connect_timeout=3)
    except psycopg.Error as exc:
        pytest.skip(f"calx DB not reachable: {exc}")
    with c:
        yield c


def _register(cur, vector, kind=imf.VECTOR_KIND, label=None):
    cur.execute(
        "SELECT (cert.register_image(%s,%s,%s,%s,%s,%s,%s::jsonb)).id",
        (f"sha-{uuid.uuid4()}", kind, vector, 16, 16, label, "{}"),
    )
    return cur.fetchone()[0]


def _check_status(cur, claim_id):
    cur.execute("SELECT (cert.check(%s)).status", (claim_id,))
    return cur.fetchone()[0]


def test_sql_cosine_matches_python(conn):
    a = [1.0, 2.0, 3.0, -1.0]
    b = [2.0, 1.0, 0.0, 4.0]
    with conn.cursor() as cur:
        cur.execute("SELECT cert.image_cosine(%s, %s)", (a, b))
        sql_cos = cur.fetchone()[0]
    assert sql_cos == pytest.approx(imf.cosine(a, b), rel=1e-9)


def test_identical_descriptor_matches(conn):
    v = imf.finalize_descriptor([float((i * 37) % 256) for i in range(256)])
    with conn.cursor() as cur:
        ref = _register(cur, v, label="reference")
        cand = _register(cur, v, label="regenerated")
        claim = None
        cur.execute("SELECT cert.image_match_claim(%s,%s,%s,%s)", (cand, ref, 0.95, "self"))
        claim = cur.fetchone()[0]
        assert _check_status(cur, claim) == "valid"


def test_different_descriptor_refuted(conn):
    left = imf.descriptor_from_matrix(
        [[(255.0 if x < 16 else 0.0) for x in range(32)] for _ in range(32)])
    top = imf.descriptor_from_matrix(
        [[(255.0 if y < 16 else 0.0) for _ in range(32)] for y in range(32)])
    with conn.cursor() as cur:
        ref = _register(cur, left, label="left")
        cand = _register(cur, top, label="top")
        cur.execute("SELECT cert.image_match_claim(%s,%s,%s,%s)", (cand, ref, 0.95, "diff"))
        claim = cur.fetchone()[0]
        assert _check_status(cur, claim) == "refuted"


def test_descriptor_kind_mismatch_unverified(conn):
    v = imf.finalize_descriptor([float(i) for i in range(256)])
    with conn.cursor() as cur:
        ref = _register(cur, v, kind="gray16c", label="ref")
        cand = _register(cur, v[:64], kind="gray8c", label="other-scheme")
        cur.execute("SELECT cert.image_match_claim(%s,%s,%s,%s)", (cand, ref, 0.95, "kindmix"))
        claim = cur.fetchone()[0]
        # probe requires equal vector_kind → no row → ok IS NULL → unverified
        assert _check_status(cur, claim) == "unverified"
