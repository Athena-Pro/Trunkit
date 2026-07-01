#!/usr/bin/env python3
"""
quote_carry - a universal Trunkit method: ground a citation by SPAN + CONTENT HASH.

The anti-hallucination primitive. A model claims "source D says X"; the witness pins it to a
content-addressed document and an exact span, and the kernel RE-SLICES and RE-HASHES to decide.
You cannot fake a span that the kernel re-slices back to your quote unless the document really
contains it.

Why universal (not pocket):
  * Tiny deterministic kernel - slice a string, hash it. Same verdict on any model/host.
  * Content addressing - the document is identified by sha256 of its CANONICAL bytes, so two
    models verify the identical artifact (doc_id is a label; doc_sha256 is the authority).
  * Portable offsets - canonicalize first (NFC + LF newlines), index in codepoints, so spans
    mean the same thing regardless of platform/encoding. Canonicalizer id is pinned.
  * Three honest verdicts - valid / refuted / unverified.

Verdicts:
  valid       doc resolves, hash matches, doc[span] == quote and re-hashes to quote_sha256
  refuted     quote not in the document (fabricated/altered), wrong offset, span out of range,
              or the cited doc version differs from the one the kernel holds (hash mismatch)
  unverified  the document is simply not available to the kernel - it cannot ground the
              citation, so it refuses to guess (the quote may still be real)

Witness:
  {"semantics":"quote/1","canon":"utf8-nfc-lf/1",
   "doc_id":"...", "doc_sha256":"...", "span":[start,end],
   "quote":"...", "quote_sha256":"..."}

Trunkit mapping:
  curry  fn   slice_and_hash(doc, span) -> (text, hash)   (pure)
  claim       "doc D span [s:e] == Q"     method = empirical_corpus / quote_carry
  witness     the JSON above              method = witness_carry
  kernel      kernel_verify(store, claim) re-slices + re-hashes  (mirrors trunkit.kernel_verify)
"""
import hashlib, unicodedata

CANON = "utf8-nfc-lf/1"

def canon(text):
    t = unicodedata.normalize("NFC", text)
    return t.replace("\r\n", "\n").replace("\r", "\n")

def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

class Store:
    """Content-addressed document store the kernel can consult."""
    def __init__(self):
        self.by_hash = {}; self.by_id = {}
    def add(self, doc_id, text):
        c = canon(text); h = sha(c)
        self.by_hash[h] = c; self.by_id[doc_id] = h
        return h

def make_claim(store, doc_id, start, end):
    """Honest producer: build a witness for an actual span of a stored doc."""
    h = store.by_id[doc_id]; c = store.by_hash[h]
    q = c[start:end]
    return {"semantics": "quote/1", "canon": CANON, "doc_id": doc_id,
            "doc_sha256": h, "span": [start, end], "quote": q, "quote_sha256": sha(q)}

def kernel_verify(store, claim):
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
        return ("refuted", "span [%d:%d] out of range (len %d)" % (s, e, len(c)))
    sliced = c[s:e]
    if sliced == claim["quote"] and sha(sliced) == claim["quote_sha256"]:
        return ("valid", "span matches quote (%d chars) and content hash" % (e - s))
    idx = c.find(claim["quote"]) if claim["quote"] else -1
    if idx >= 0:
        return ("refuted", "quote present but at [%d:%d], not claimed [%d:%d]" % (idx, idx + len(claim["quote"]), s, e))
    return ("refuted", "quote not found in document (fabricated or altered)")


if __name__ == "__main__":
    def show(tag, v):
        print("  %-40s %-11s %s" % (tag, v[0], v[1]))

    store = Store()
    DOC = ("We hold these truths to be self-evident, that all men are created equal, "
           "that they are endowed by their Creator with certain unalienable Rights.")
    store.add("declaration", DOC)
    store.add("memo", "The launch is scheduled for the third quarter of next year.")

    print("=" * 78)
    print("quote_carry - verdict battery\n")

    good = make_claim(store, "declaration", DOC.index("all men"), DOC.index("all men") + len("all men are created equal"))
    show("exact span of real quote", kernel_verify(store, good))

    shifted = dict(good); shifted["span"] = [good["span"][0] + 4, good["span"][1] + 4]
    show("right quote, wrong offset", kernel_verify(store, shifted))

    fab = make_claim(store, "declaration", 0, 5)
    fab["quote"] = "all men are created unequal"; fab["quote_sha256"] = sha(fab["quote"])
    show("fabricated/altered quote", kernel_verify(store, fab))

    tampered = dict(good); tampered["doc_sha256"] = sha("a different version of the document")
    show("cited version != held version", kernel_verify(store, tampered))

    missing = make_claim(store, "memo", 0, 10)
    missing["doc_id"] = "secret_report"; missing["doc_sha256"] = sha("unseen document body")
    show("document not available to kernel", kernel_verify(store, missing))
