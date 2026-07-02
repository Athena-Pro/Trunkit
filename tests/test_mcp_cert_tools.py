"""Certificate-family verifier tests: the pure calx mirrors and the
trunkit-mcp consumer tools built on them (recurrence 93, morphism 95,
holographic 96, arith/crypto 97).

Three layers:
  1. pure-module tests — no DB, no mcp package;
  2. MCP tool tests    — skipped when the mcp extra is not installed;
  3. SQL equivalence   — the Python merkle/commitment must be byte-identical
     to cert.merkle_root / cert.claim_commitment (needs a test DSN).
"""

from __future__ import annotations

import hashlib
import json
import os

import psycopg
import pytest

from calx import holographic, morphism
from calx.arith import interp_from_json, phi_from_json, residual

# ── holographic (96 mirror) ─────────────────────────────────────────────────


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def test_merkle_empty_commits_to_empty_hash():
    assert holographic.merkle_root([]) == _h("")
    assert holographic.merkle_root(None) == _h("")


def test_merkle_single_leaf_is_its_hash():
    assert holographic.merkle_root(["a"]) == _h("a")


def test_merkle_pair_and_odd_tail_duplication():
    a, b, c = _h("a"), _h("b"), _h("c")
    assert holographic.merkle_root(["a", "b"]) == _h(a + b)
    # odd tail: c pairs with itself
    assert holographic.merkle_root(["a", "b", "c"]) == _h(_h(a + b) + _h(c + c))


def test_merkle_tamper_flips_root():
    leaves = ["claim:1", "stmt:s", "method:m", "status:valid", "evid:{}"]
    root = holographic.merkle_root(leaves)
    assert holographic.verify_root(leaves, root)
    tampered = leaves[:3] + ["status:refuted"] + leaves[4:]
    assert not holographic.verify_root(tampered, root)


def test_claim_leaves_coalesce_semantics():
    assert holographic.claim_leaves(7, None, None, None, None) == [
        "claim:7", "stmt:", "method:", "status:unchecked", "evid:",
    ]


# ── morphism (95 mirror) ─────────────────────────────────────────────────────


def test_morphism_scale():
    ok, ev = morphism.matches("scale", {"c": 2}, [1, 1, 2, 3, 5], [2, 2, 4, 6, 10])
    assert ok and ev["verified_terms"] == 5 and ev["exact"]


def test_morphism_affine_and_rational_params():
    ok, ev = morphism.matches("affine", {"a": "1/2", "b": 1}, [2, 4, 6], [2, 3, 4])
    assert ok, ev


def test_morphism_index_shift_common_prefix_only():
    ok, ev = morphism.matches("index_shift", {"s": 2}, [1, 1, 2, 3, 5], [2, 3])
    assert ok and ev["verified_terms"] == 2


def test_morphism_mismatch_is_located():
    ok, ev = morphism.matches("scale", {"c": 2}, [1, 2, 3], [2, 4, 7])
    assert not ok
    assert ev["reason"] == "morphism mismatch" and ev["at"] == 3


def test_morphism_empty_overlap_refutes():
    ok, ev = morphism.matches("index_shift", {"s": 5}, [1, 2, 3], [9])
    assert not ok and ev["reason"] == "empty overlap"


def test_morphism_bad_kind_and_bad_shift():
    ok, ev = morphism.matches("rotate", {}, [1], [1])
    assert not ok and ev["reason"] == "apply failed"
    ok, ev = morphism.matches("index_shift", {"s": -1}, [1], [1])
    assert not ok and ev["reason"] == "apply failed"


# ── arith JSON codec (97) ────────────────────────────────────────────────────


def _const(n: int) -> dict:
    return {"op": "Const", "args": [n]}


def test_arith_divides_valid_and_refuted():
    good = {"op": "Divides", "args": [_const(7), _const(28), _const(4)]}
    assert residual(phi_from_json(good)) == 0
    bad = {"op": "Divides", "args": [_const(7), _const(28), _const(5)]}
    assert residual(phi_from_json(bad)) != 0


def test_arith_forall_over_interp():
    # forall x in [len(C)]: C_1(x) = x  for C = identity row [1,2,3]
    phi = phi_from_json({
        "op": "Forall",
        "args": ["C", {"op": "Eq", "args": [
            {"op": "Lookup", "args": ["C", 1, {"op": "Var", "args": []}]},
            {"op": "Var", "args": []},
        ]}],
    })
    s = interp_from_json({"C": [[1, 2, 3]]})
    assert residual(phi, s) == 0


def test_arith_codec_rejects_unknown_and_malformed():
    with pytest.raises(ValueError):
        phi_from_json({"op": "Exec", "args": []})
    with pytest.raises(ValueError):
        phi_from_json({"op": "Const"})       # missing required arg -> bad arity
    with pytest.raises(ValueError):
        phi_from_json(["not", "a", "node"])
    with pytest.raises(ValueError):
        interp_from_json({"C": [[1, 2], [3]]})   # ragged
    with pytest.raises(ValueError):
        interp_from_json({"C": [[1.5]]})         # non-integer


# ── MCP tool layer ───────────────────────────────────────────────────────────

mcp_mod = pytest.importorskip("mcp", reason="mcp extra not installed")
from trunkit_mcp import server  # noqa: E402


def test_tool_recurrence_verify_fibonacci():
    out = server.recurrence_verify("[[1],[-1],[-1]]", "[1,1]", "[1,1,2,3,5,8,13]")
    assert out["verdict"] == "valid" and out["verified_terms"] == 7
    out = server.recurrence_verify("[[1],[-1],[-1]]", "[1,1]", "[1,1,2,3,5,8,14]")
    assert out["verdict"] == "refuted" and out["at_index"] == 6


def test_tool_recurrence_verify_honest_on_garbage():
    assert server.recurrence_verify("not json", "[1]", "[1]")["verdict"] == "unverified"
    assert server.recurrence_verify("[[1],[-1]]", "[1]", "[]")["verdict"] == "unverified"


def test_tool_morphism_verify():
    out = server.morphism_verify("scale", '{"c": 2}', "[1,1,2,3]", "[2,2,4,6]")
    assert out["verdict"] == "valid"
    out = server.morphism_verify("scale", '{"c": 2}', "[1,1,2,3]", "[2,2,4,7]")
    assert out["verdict"] == "refuted"


def test_tool_commitment_verify_roundtrip():
    leaves = ["claim:1", "stmt:x", "method:comp_sql", "status:valid", "evid:{}"]
    root = holographic.merkle_root(leaves)
    out = server.commitment_verify(root, leaves_json=json.dumps(leaves))
    assert out["verdict"] == "valid" and out["recomputed_root"] == root
    out = server.commitment_verify(root[:-1] + ("0" if root[-1] != "0" else "1"),
                                   leaves_json=json.dumps(leaves))
    assert out["verdict"] == "refuted"
    out = server.commitment_verify(root)     # neither leaves nor claim
    assert out["verdict"] == "unverified"


def test_tool_arith_verify():
    good = json.dumps({"op": "Divides", "args": [_const(7), _const(28), _const(4)]})
    out = server.arith_verify(good)
    assert out["verdict"] == "valid" and out["residual"] == "0"
    bad = json.dumps({"op": "Divides", "args": [_const(7), _const(28), _const(5)]})
    assert server.arith_verify(bad)["verdict"] == "refuted"
    assert server.arith_verify('{"op":"Exec","args":[]}')["verdict"] == "unverified"


# ── SQL equivalence (byte-identity with 96) ──────────────────────────────────


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
    try:
        yield c
    finally:
        c.rollback()
        c.close()


def test_python_merkle_matches_sql(conn):
    vectors = [[], ["a"], ["a", "b"], ["a", "b", "c"],
               ["claim:1", "stmt:σ(28)=56", "method:comp_sql", "status:valid", "evid:{}"]]
    with conn.cursor() as cur:
        for leaves in vectors:
            cur.execute("SELECT cert.merkle_root(%s::text[])", (leaves,))
            assert cur.fetchone()[0] == holographic.merkle_root(leaves), leaves


def test_python_claim_commitment_matches_sql(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT s.claim_id, s.statement, s.method, s.status, s.evidence::text, "
            "       cert.claim_commitment(s.claim_id) "
            "FROM cert.standing s ORDER BY s.claim_id LIMIT 5"
        )
        rows = cur.fetchall()
    assert rows, "standing view is empty"
    for claim_id, stmt, method, status, evid_text, sql_root in rows:
        py_root = holographic.claim_commitment(claim_id, stmt, method, status, evid_text)
        assert py_root == sql_root, f"claim {claim_id}"
