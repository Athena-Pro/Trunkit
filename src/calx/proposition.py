"""Typed connection between a claim and the proof object its kernel checks.

Claim digests protect bytes. This contract additionally identifies the checked
proposition; free-text statements remain descriptions, never executable logic.
Legacy witnesses can explicitly claim proof-object validity, rather than silently
being promoted to evidence of an arbitrary mathematical statement.
"""

from __future__ import annotations

from typing import Any

from .ledger import canonical_json, hash_text


def _same(left: Any, right: Any) -> bool:
    """JSON equality with distinct Boolean/integer/float types."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same(a, b) for a, b in zip(left, right, strict=True)
        )
    return left == right


def witness_subject_ref(body: dict[str, Any]) -> dict[str, Any]:
    """Construct the exact typed proposition checked by a supported witness."""
    schema = body.get("schema")
    if schema == "comb.v1":
        return {
            "witness_schema": schema,
            "predicate": body.get("predicate"),
            "instance_digest": body.get("instance_digest"),
        }
    if schema == "dfa_count.v1":
        from .automata_count import claim_subject_ref

        return claim_subject_ref(body)
    if schema == "dfa_extremal.v1":
        from .automata_count import extremal_claim_subject_ref

        return extremal_claim_subject_ref(body)
    return {
        "witness_schema": schema,
        "predicate": {"name": "witness_validity"},
        "instance_digest": hash_text("trunkit-witness-v1\x1f" + canonical_json(body)),
    }


def claim_witness_agreement(
    claim: dict[str, Any],
    body: Any,
) -> tuple[bool | None, dict[str, Any]]:
    """Unknown/mismatched proof attribution cannot refute the mathematical claim."""
    if not isinstance(body, dict):
        return None, {"error": "witness body must be an object"}
    reference = claim.get("subject_ref")
    if not isinstance(reference, dict):
        return None, {"error": "claim lacks a typed witness proposition"}
    try:
        expected = witness_subject_ref(body)
    except (ValueError, TypeError, KeyError, IndexError, RecursionError) as exc:
        return None, {"error": f"invalid typed witness proposition: {exc}"}
    try:
        mismatch = any(not _same(reference.get(key), value) for key, value in expected.items())
    except RecursionError:
        return None, {"error": "typed witness proposition exceeds nesting budget"}
    if mismatch:
        return None, {
            "error": "witness does not establish the claim's typed proposition",
            "checked_proposition": expected,
        }
    if (
        not isinstance(expected["witness_schema"], str)
        or not isinstance(expected["instance_digest"], str)
        or not isinstance(expected["predicate"], dict)
    ):
        return None, {"error": "incomplete typed witness proposition"}
    return True, {"checked_proposition": expected}
