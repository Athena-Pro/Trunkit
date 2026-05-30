"""calx.ledger — consumer-side verification of the cert hash chain.

A dependency-free mirror of src/calx/sql/95_cert_ledger.sql. Given an exported
bundle (cert.export_bundle, trunk_bundle_version >= 2), it recomputes each
certificate's content hash from the payload and checks the entanglement links —
so a consumer can confirm, with no database, that the records they received were
not altered and are bound to their provenance, predecessor, and proof premises.

The canonical-JSON spec MUST match cert.canonical_json exactly:
  * object keys ordered by (utf-8 byte length, utf-8 bytes)
  * arrays keep order; compact, no spaces
  * strings via JSON escaping (ensure_ascii=False)
  * integers/booleans/null are byte-stable across SQL and Python
  * non-integer numbers are canonicalization-sensitive (documented gap)
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

SEP = "\x1f"  # chr(31), the unit separator used in the SQL preimages

__all__ = [
    "canonical_json",
    "hash_text",
    "certificate_row_hash",
    "witness_row_hash",
    "inference_row_hash",
    "verify_chain",
]


def canonical_json(o: Any) -> str:
    """Deterministic serialization matching cert.canonical_json."""
    if o is True:
        return "true"
    if o is False:
        return "false"
    if o is None:
        return "null"
    if isinstance(o, str):
        return json.dumps(o, ensure_ascii=False)
    if isinstance(o, int):  # bool already handled above
        return str(o)
    if isinstance(o, float):
        # canonicalization-sensitive; integral floats normalised, else best effort
        return str(int(o)) if o.is_integer() else repr(o)
    if isinstance(o, list):
        return "[" + ",".join(canonical_json(x) for x in o) + "]"
    if isinstance(o, dict):
        def _key_order(kv: tuple[str, Any]) -> tuple[int, bytes]:
            b = kv[0].encode("utf-8")
            return (len(b), b)
        items = sorted(o.items(), key=_key_order)
        return "{" + ",".join(
            json.dumps(k, ensure_ascii=False) + ":" + canonical_json(v) for k, v in items
        ) + "}"
    raise TypeError(f"non-JSON value in canonical_json: {type(o).__name__}")


def hash_text(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def inference_row_hash(
    inference_id: str, model_name: str, model_version: Any, metadata: Any
) -> str:
    pre = SEP.join([
        "trunkit-inf-v1",
        inference_id or "",
        model_name or "",
        "" if model_version is None else str(model_version),
        canonical_json(metadata if metadata is not None else {}),
    ])
    return hash_text(pre)


def certificate_row_hash(
    claim_id: Any, seq: Any, status: str, evidence: Any, valid_under: Any,
    inf_hash: str | None, prev: str | None, premises: list[str] | None,
) -> str:
    pre = SEP.join([
        "trunkit-cert-v1",
        str(claim_id),
        str(seq),
        status,
        canonical_json(evidence if evidence is not None else {}),
        canonical_json(valid_under if valid_under is not None else {}),
        inf_hash or "",
        prev or "",
        ",".join(premises) if premises else "",
    ])
    return hash_text(pre)


def witness_row_hash(cert_id: Any, cert_hash: str | None, kind: str, body: Any) -> str:
    pre = SEP.join([
        "trunkit-witness-v1",
        str(cert_id),
        cert_hash or "",
        kind or "",
        canonical_json(body if body is not None else {}),
    ])
    return hash_text(pre)


def verify_chain(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Recompute and check every certificate hash + entanglement link in a bundle.

    Per claim returns: content_ok (row_hash recomputes), premises_ok (each
    premise hash matches a bundled premise's row_hash, when present), and the
    fields needed to chase a failure. Linkage to the global ledger_root can only
    be fully checked when the bundle is a contiguous suffix of the ledger; here
    we verify content + premise entanglement, which catch row edits and premise
    swaps directly.
    """
    # row_hash of every bundled cert, keyed by claim_id (for premise checks)
    by_claim: dict[Any, str] = {}
    for entry in bundle.get("claims", []):
        cert = entry.get("certificate") or {}
        cid = (entry.get("claim") or {}).get("id")
        if cert.get("row_hash") is not None:
            by_claim[cid] = cert["row_hash"]

    results: list[dict[str, Any]] = []
    for entry in bundle.get("claims", []):
        claim = entry.get("claim") or {}
        cert = entry.get("certificate") or {}
        cid = claim.get("id")
        stored = cert.get("row_hash")

        recomputed = certificate_row_hash(
            cid, cert.get("seq"), cert.get("status"),
            cert.get("evidence"), cert.get("valid_under"),
            cert.get("inference_hash"), cert.get("prev_hash"),
            cert.get("premise_hashes"),
        )
        content_ok = stored is not None and recomputed == stored

        premises = cert.get("premise_hashes") or []
        bundled_premise_hashes = set(by_claim.values())
        premises_ok = all(ph in bundled_premise_hashes for ph in premises) if premises else True

        # witness binding (if a witness travels with the cert)
        witness_ok: bool | None = None
        wrap = entry.get("witness")
        if wrap and wrap.get("row_hash") is not None and stored is not None:
            witness_ok = witness_row_hash(
                cert.get("cert_id"), stored, wrap.get("kind"), wrap.get("body")
            ) == wrap["row_hash"]

        results.append({
            "claim_id": cid,
            "statement": claim.get("statement"),
            "content_ok": content_ok,
            "premises_ok": premises_ok,
            "witness_ok": witness_ok,
            "recomputed_row_hash": recomputed,
            "stored_row_hash": stored,
        })
    return results
