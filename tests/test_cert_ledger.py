"""Tests for the consumer-side ledger verifier (calx.ledger).

DB-free: these pin the canonical-JSON spec and the hash/entanglement logic that
mirror src/calx/sql/95_cert_ledger.sql. The in-DB cert.verify_chain() is the
authoritative check; this is what a bundle consumer runs offline.
"""
from __future__ import annotations

from calx.ledger import (
    canonical_json,
    certificate_row_hash,
    verify_chain,
    witness_row_hash,
)

# ---- canonical JSON (must match cert.canonical_json) ----------------------

def test_canonical_sorts_keys_by_length_then_bytes():
    # length-first ordering: 'a' (1 byte) before 'bb' (2 bytes)
    assert canonical_json({"bb": 1, "a": 2}) == '{"a":2,"bb":1}'
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_canonical_scalars_and_nesting():
    assert canonical_json({"x": [1, 2], "y": True, "z": None}) == '{"x":[1,2],"y":true,"z":null}'
    assert canonical_json(True) == "true"
    assert canonical_json(None) == "null"
    assert canonical_json(28) == "28"


def test_canonical_string_escaping():
    assert canonical_json("a\"b") == '"a\\"b"'


# ---- certificate hashing -------------------------------------------------

def _cert(cid, seq, status, evidence, valid_under, inf_hash, prev, premises):
    rh = certificate_row_hash(cid, seq, status, evidence, valid_under, inf_hash, prev, premises)
    return {
        "claim": {"id": cid, "statement": f"claim {cid}"},
        "certificate": {
            "cert_id": cid, "seq": seq, "status": status, "evidence": evidence,
            "valid_under": valid_under, "inference_hash": inf_hash, "prev_hash": prev,
            "premise_hashes": premises, "row_hash": rh,
        },
    }


def test_certificate_hash_is_deterministic_and_sensitive():
    base = ("28 perfect", 1, "valid", {"got": 28}, {"kan_objects": 19}, "i0", None, [])
    h = certificate_row_hash(*base)
    assert h == certificate_row_hash(*base)                       # deterministic
    assert h != certificate_row_hash(*(base[:2] + ("refuted",) + base[3:]))  # status matters
    assert h != certificate_row_hash(*(base[:7] + (["x"],)))      # premises matter


def test_verify_chain_accepts_intact_and_entangled_bundle():
    premise = _cert(1, 1, "valid", {"got": 28}, {}, "iA", None, [])
    p_hash = premise["certificate"]["row_hash"]
    conclusion = _cert(2, 1, "valid", {"derived": True}, {}, "iB",
                       p_hash, [p_hash])               # entangled with premise 1
    bundle = {"trunk_bundle_version": 2, "ledger_root": p_hash, "claims": [premise, conclusion]}

    res = {r["claim_id"]: r for r in verify_chain(bundle)}
    assert res[1]["content_ok"] is True
    assert res[2]["content_ok"] is True
    assert res[2]["premises_ok"] is True               # premise hash is present in the bundle


def test_verify_chain_catches_tampered_content():
    c = _cert(1, 1, "valid", {"got": 28}, {}, "iA", None, [])
    c["certificate"]["evidence"] = {"got": 999}        # tamper after hashing
    res = verify_chain({"claims": [c]})[0]
    assert res["content_ok"] is False


def test_verify_chain_catches_premise_swap():
    c = _cert(2, 1, "valid", {}, {}, "iB", None, ["0" * 64])  # premise hash not in bundle
    res = verify_chain({"claims": [c]})[0]
    assert res["premises_ok"] is False


def test_verify_chain_checks_witness_binding():
    c = _cert(1, 1, "valid", {"got": 28}, {}, "iA", None, [])
    rh = c["certificate"]["row_hash"]
    body = {"kind": "term", "reconstruction": "2^2 * 7 = 28"}
    c["witness"] = {"kind": "term", "body": body,
                    "row_hash": witness_row_hash(1, rh, "term", body)}
    res = verify_chain({"claims": [c]})[0]
    assert res["witness_ok"] is True

    c["witness"]["body"]["reconstruction"] = "tampered"
    res = verify_chain({"claims": [c]})[0]
    assert res["witness_ok"] is False
