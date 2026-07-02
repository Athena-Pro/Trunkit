"""quote_carry — ground a citation by SPAN + CONTENT HASH.

The anti-hallucination primitive. A model claims "source D says X"; the
witness pins it to a content-addressed document and an exact span, and the
kernel RE-SLICES and RE-HASHES to decide. You cannot fake a span that the
kernel re-slices back to your quote unless the document really contains it.

Portability: the document is canonicalised first (NFC + LF newlines,
canonicaliser id pinned as "utf8-nfc-lf/1") and spans are codepoint indices,
so offsets mean the same thing on every platform.

Verdicts:
  valid       doc resolves, hash matches, doc[span] == quote, quote re-hashes
  refuted     quote absent or at a different offset, span out of range, or the
              cited doc version differs from the held one (hash mismatch)
  unverified  the document is not available to the kernel — it refuses to
              guess (the quote may still be real)

Kernel-dispatch witness (calx.kernel schema "quote_carry"; witness kind
"quote_span"):
  {"schema": "quote_carry", "canon": "utf8-nfc-lf/1",
   "doc_id": "...", "doc_sha256": "...", "span": [start, end],
   "quote": "...", "quote_sha256": "...",
   "doc_text": "..."?}    # carried document body; without it the kernel
                          # has nothing to slice -> unverified
Spec: docs/methods/quote_carry_spec.md.
"""

from __future__ import annotations

import hashlib
import unicodedata
from typing import Any

CANON = "utf8-nfc-lf/1"


def canon(text: str) -> str:
    t = unicodedata.normalize("NFC", text)
    return t.replace("\r\n", "\n").replace("\r", "\n")


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class Store:
    """Content-addressed document store the kernel can consult."""

    def __init__(self):
        self.by_hash: dict[str, str] = {}
        self.by_id: dict[str, str] = {}

    def add(self, doc_id: str, text: str) -> str:
        c = canon(text)
        h = sha(c)
        self.by_hash[h] = c
        self.by_id[doc_id] = h
        return h


def make_claim(store: Store, doc_id: str, start: int, end: int) -> dict[str, Any]:
    """Honest producer: build a witness for an actual span of a stored doc."""
    h = store.by_id[doc_id]
    c = store.by_hash[h]
    q = c[start:end]
    return {"semantics": "quote/1", "canon": CANON, "doc_id": doc_id,
            "doc_sha256": h, "span": [start, end], "quote": q, "quote_sha256": sha(q)}


def kernel_verify(store: Store, claim: dict[str, Any]) -> tuple[str, str]:
    h = claim["doc_sha256"]
    c = store.by_hash.get(h)
    if c is None:
        hid = store.by_id.get(claim["doc_id"])
        if hid is None:
            return ("unverified", "document not available to kernel; cannot ground citation")
        if hid != h:
            return ("refuted", "doc hash mismatch: cited version differs from the held document")
        c = store.by_hash[hid]
    s, e = claim["span"]
    if not (0 <= s <= e <= len(c)):
        return ("refuted", f"span [{s}:{e}] out of range (len {len(c)})")
    sliced = c[s:e]
    if sliced == claim["quote"] and sha(sliced) == claim["quote_sha256"]:
        return ("valid", f"span matches quote ({e - s} chars) and content hash")
    idx = c.find(claim["quote"]) if claim["quote"] else -1
    if idx >= 0:
        return ("refuted",
                f"quote present but at [{idx}:{idx + len(claim['quote'])}], "
                f"not claimed [{s}:{e}]")
    return ("refuted", "quote not found in document (fabricated or altered)")


# ── calx.kernel adapter (schema "quote_carry") ──────────────────────────────

def check_quote_carry(w: dict[str, Any]) -> tuple[bool | None, dict[str, Any]]:
    """Self-contained form: the witness may carry the document body itself."""
    for field in ("doc_sha256", "span", "quote", "quote_sha256"):
        if field not in w:
            return None, {"error": f"witness missing '{field}'"}
    store = Store()
    doc_text = w.get("doc_text")
    if doc_text is not None:
        held = sha(canon(doc_text))
        if held != w["doc_sha256"]:
            return False, {"status": "refuted",
                           "detail": "carried doc_text does not hash to doc_sha256",
                           "held_sha256": held}
        store.add(w.get("doc_id", "carried"), doc_text)
    status, detail = kernel_verify(store, w)
    ok = True if status == "valid" else False if status == "refuted" else None
    return ok, {"status": status, "detail": detail, "canon": w.get("canon", CANON)}
