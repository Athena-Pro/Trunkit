"""Holographic (Merkle) commitments — consumer-side re-verification.

Pure-Python mirror of the SQL in ``96_cert_holographic.sql``, so the two agree
and a consumer can re-check a 32-byte commitment with nothing but a Python
interpreter (the same charter as calx.kernel / calx.recurrence: no database,
no third-party dependency).

Semantics, byte-for-byte with the SQL:
  * ``leaf_hash(s)``    — sha256 hex of the UTF-8 encoding (None -> '').
  * ``merkle_root``     — binary tree over the ordered leaf hashes; pairs are
    combined by hashing the *concatenated hex strings*; an odd tail node is
    duplicated; an empty leaf list commits to ``leaf_hash('')``.
  * ``claim_leaves``    — the canonical five-leaf encoding of a claim's
    verifiable content used by ``cert.claim_commitment``:
    ``claim:<id>``, ``stmt:<statement>``, ``method:<method>``,
    ``status:<status or 'unchecked'>``, ``evid:<evidence text or ''>``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

__all__ = ["leaf_hash", "merkle_root", "claim_leaves", "claim_commitment", "verify_root"]


def leaf_hash(s: str | None) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def merkle_root(leaves: Sequence[str] | None) -> str:
    if not leaves:
        return leaf_hash("")
    level = [leaf_hash(x) for x in leaves]
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level), 2):
            right = level[i + 1] if i + 1 < len(level) else level[i]  # duplicate odd tail
            nxt.append(leaf_hash(level[i] + right))
        level = nxt
    return level[0]


def claim_leaves(
    claim_id: int,
    statement: str | None,
    method: str | None,
    status: str | None,
    evidence_text: str | None,
) -> list[str]:
    """The canonical leaf list of cert.claim_commitment (COALESCE semantics)."""
    return [
        f"claim:{claim_id}",
        f"stmt:{statement or ''}",
        f"method:{method or ''}",
        f"status:{status or 'unchecked'}",
        f"evid:{evidence_text or ''}",
    ]


def claim_commitment(
    claim_id: int,
    statement: str | None,
    method: str | None,
    status: str | None,
    evidence_text: str | None,
) -> str:
    return merkle_root(claim_leaves(claim_id, statement, method, status, evidence_text))


def verify_root(leaves: Sequence[str], root: str) -> bool:
    """True iff the recomputed commitment equals the carried root (hex, case-insensitive)."""
    return merkle_root(leaves).lower() == (root or "").strip().lower()
