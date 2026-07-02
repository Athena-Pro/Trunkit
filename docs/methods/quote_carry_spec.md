# quote_carry — method spec (Trunkit universal method)

Ground a citation by **span + content hash**. The anti-hallucination primitive: a model can't
fake a span the kernel re-slices back to its quote unless the document actually contains it.

## Claim shape
    document D, span [start:end]  ==  quote Q   (and Q hashes to H)

## Witness
    {"semantics":"quote/1","canon":"utf8-nfc-lf/1",
     "doc_id":"declaration","doc_sha256":"<hash of canonical doc>",
     "span":[start,end],"quote":"…","quote_sha256":"<hash of quote>"}

- `doc_id` is a human label; `doc_sha256` is the **authority** (content addressing).
- `canon` pins the canonicalizer: NFC Unicode + LF newlines, offsets in codepoints — so a span
  means the same thing on any platform/encoding.

## Verdict semantics
| Verdict | When |
|---|---|
| `valid` | doc resolves by hash, `doc[span] == quote`, and the quote re-hashes to `quote_sha256` |
| `refuted` | quote absent (fabricated/altered), present but at a different offset, span out of range, or cited doc version ≠ held version (hash mismatch) |
| `unverified` | the document isn't available to the kernel — it can't ground the citation, so it refuses to guess |

The split between `refuted` and `unverified` is the whole point: a missing document is *not*
evidence of a bad quote, so the kernel says `unverified`, not `refuted`.

## Determinism / portability
Canonical bytes + sha256 mean every model verifies the identical artifact. Offsets are codepoint
indices into the canonical string, so they're stable across encodings. The kernel is two
operations: slice and hash.

## Trunkit mapping
`curry` = `slice_and_hash(doc, span)`; method = `empirical_corpus` / `quote_carry`; witness via
`witness_carry`; `kernel_verify(store, claim)` mirrors `trunkit.kernel_verify`. `claim_export`
ships the witness as-is — it's already self-contained.

## Demo results (`quote_carry.py`)
real span → valid · wrong offset → refuted (reports true offset) · fabricated quote → refuted ·
version mismatch → refuted · doc unavailable → unverified.
