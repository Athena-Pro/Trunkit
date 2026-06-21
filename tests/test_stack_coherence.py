"""End-to-end coherence of the recurrence/exactness/morphism/holographic stack.

Demonstrates the four layers composing on one example (Fibonacci & 2·Fibonacci):
  #4 recurrence  — certify each sequence exactly
  #2 morphism    — prove Fib×2 = scale(2, Fib) exactly
  #3 exactness   — exact claims stay valid; a float_heuristic claim is shielded
  #1 holographic — commit a claim to a 32-byte Merkle root
All share the one cert engine (claim/certificate/check), so they interlock.
"""

from __future__ import annotations

import json
import os
import uuid

import psycopg
import pytest

FIB = [1, 1, 2, 3, 5, 8, 13, 21, 34, 55]
FIB2 = [2 * x for x in FIB]
CFIN = [[1], [-1], [-1]]  # a_n = a_{n-1} + a_{n-2}


def _calx_dsn():
    dsn = os.environ.get("CALX_TEST_DSN") or os.environ.get("ARITHMETIC_DB_TEST_DSN")
    if not dsn:
        pytest.skip("No test DSN provided. Refusing to write to default/production ledger.")
    return dsn


@pytest.fixture()
def conn():
    try:
        c = psycopg.connect(_calx_dsn(), connect_timeout=3)
    except psycopg.Error as exc:
        pytest.skip(f"calx DB not reachable: {exc}")
    with c:
        yield c


def _status(cur, claim_id):
    cur.execute("SELECT (cert.check(%s)).status", (claim_id,))
    return cur.fetchone()[0]


def test_full_stack_coherence(conn):
    tag = uuid.uuid4().hex[:6]
    fib_id, fibx2_id = f"fib_{tag}", f"fibx2_{tag}"
    with conn.cursor() as cur:
        # --- #4 recurrence: certify both sequences exactly ---
        cur.execute("SELECT (cert.register_recurrence(%s,'c_finite',%s::jsonb,%s::numeric[],%s::numeric[])).id",
                    (fib_id, json.dumps(CFIN), [1, 1], FIB))
        rfib = cur.fetchone()[0]
        cur.execute("SELECT (cert.register_recurrence(%s,'c_finite',%s::jsonb,%s::numeric[],%s::numeric[])).id",
                    (fibx2_id, json.dumps(CFIN), [2, 2], FIB2))
        rfibx2 = cur.fetchone()[0]
        cur.execute("SELECT cert.recurrence_claim(%s)", (rfib,)); c_fib = cur.fetchone()[0]
        cur.execute("SELECT cert.recurrence_claim(%s)", (rfibx2,)); c_fibx2 = cur.fetchone()[0]
        # tag them exact_int (#3) and confirm they attest valid
        cur.execute("SELECT cert.set_domain(%s,'exact_int')", (c_fib,))
        cur.execute("SELECT cert.set_domain(%s,'exact_int')", (c_fibx2,))
        assert _status(cur, c_fib) == "valid"
        assert _status(cur, c_fibx2) == "valid"

        # --- #2 morphism: prove the exact relation cosine only hinted at ---
        cur.execute("SELECT (cert.register_morphism(%s,%s,'scale',%s::jsonb,%s::numeric[],%s::numeric[])).id",
                    (fib_id, fibx2_id, json.dumps({"c": 2}), FIB, FIB2))
        mid = cur.fetchone()[0]
        cur.execute("SELECT cert.morphism_claim(%s)", (mid,)); c_morph = cur.fetchone()[0]
        assert _status(cur, c_morph) == "valid"

        # --- #3 shield: a float_heuristic 'cosine candidate' claim cannot be valid ---
        cur.execute(
            "INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql) "
            "VALUES ('cosine','{}'::jsonb,%s,'computational','comp_sql',%s) RETURNING id",
            (f"cosine candidate {tag}", "SELECT true AS ok, '{}'::jsonb AS evidence"),
        )
        c_cos = cur.fetchone()[0]
        cur.execute("SELECT cert.set_domain(%s,'float_heuristic')", (c_cos,))
        assert _status(cur, c_cos) == "unverified"     # shielded

        # --- #1 holographic: commit the morphism claim to a 32-byte root ---
        cur.execute("SELECT cert.claim_commitment(%s)", (c_morph,))
        root = cur.fetchone()[0]
        assert root and len(root) == 64
        cur.execute("SELECT cert.verify_commitment(%s,%s)", (c_morph, root))
        assert cur.fetchone()[0] is True
