# Design: Tool-Attestation Axiom Tier for Formal Claims

**Status:** design, not yet implemented (target: next branch after `feat-cert-lifecycle`)
**Source:** EG-VAR, arXiv:2607.12650 (Ren, 2026-07-14; read in full 2026-07-15),
adapted to Trunkit's ledger; also arXiv:2605.16407 (tiered assumption partition).
**Depends on:** steps 100 (lifecycle/signer), 102 (derivation closure),
103 (signer independence), `tools/AxiomAudit.lean` (T1 bridge).

## Problem

Trunkit's formal tier proves *pure mathematics*: `AxiomAudit.lean` accepts a
Lean witness iff it is sorry-free and its transitive axiom closure is a subset
of Mathlib's three trusted axioms (`propext`, `Classical.choice`,
`Quot.sound`). Empirical facts (an OEIS lookup, a checker run, a table cell)
cannot enter a formal derivation at all — they live in separate `comp_sql` /
computational claims, connected to formal claims only loosely via
`cert.derivation` rows a prover asserts by hand.

EG-VAR shows the missing mechanism: let empirical facts enter Lean proofs as
**declared, name-schema'd axioms**, each mechanically bound to an attested
tool execution, and audit the axiom closure into **buckets** instead of
against a single whitelist.

## EG-VAR mechanics we adopt (and where they land in Trunkit)

EG-VAR's replay audit buckets `#print axioms` output into:
- **B1** fixed prelude, **B2** Lean core axioms — Trunkit's existing
  `ALLOWED` set in AxiomAudit covers B2; a Trunkit prelude would be B1.
- **B3** runtime-emitted observation leaves (`obsN_*` naming schema), each
  backed by an entry in an evidence log emitted only by the trusted runtime.
- **B4** per-source lifts (`lift_<sourcehash>_*` regex), audited once per
  source, encoding the curator's interpretive commitment.
- Plus: no `sorryAx` anywhere, and at least one trust-artifact witness.

The single load-bearing idea: **axiom names are foreign keys**. A regex over
the axiom closure mechanically partitions trust, and each B3/B4 axiom must
resolve to an external artifact (evidence-log entry / audited lift catalog).

Trunkit already has better versions of both resolution targets:
- EG-VAR's evidence log ≈ **the cert ledger itself** (append-only,
  hash-entangled, signed since step 100, revocable, exportable).
- EG-VAR's "attestation backs axiom" ≈ **`cert.derivation` premises** — and
  since step 102, revoking an attestation propagates transitively into every
  claim whose proof rests on it; since step 103, the attesting signers can be
  required to be independent.

## Design

### 1. Axiom naming schema (the contract)

A formal witness may depend on axioms named:

    trunkit_att_<claim_id>_<sha256_16>

where `<claim_id>` is a ledger claim id and `<sha256_16>` is the first 16 hex
chars of the SHA-256 of the axiom's *statement text* (canonical, UTF-8).
Everything else outside the trusted core set stays disallowed. The hash
suffix prevents a prover from re-using an attestation axiom name for a
different proposition than the ledger attests.

### 2. AxiomAudit.lean: binary → bucketed

Current exit contract: `0 iff (¬uses_sorry) ∧ (axioms ⊆ ALLOWED)`.
New contract (additive; default behaviour unchanged):

- JSON output gains `buckets`: `{"core": [...], "attested": [...],
  "disallowed": [...]}` where `attested` = axioms matching the schema above.
- New flag `--allow-attested`: exit 0 iff sorry-free ∧ disallowed = ∅.
  Without the flag, behaviour is exactly today's (attested axioms are
  disallowed) — T0 mathematical claims cannot silently acquire empirical
  dependencies.

### 3. Witness body carries the tier

`leanbridge` / `cert_formal` record in the witness body:

    "axiom_tier": "kernel_pure" | "tool_attested",
    "attestation_axioms": [{"name": ..., "claim_id": N, "stmt_sha256": ...}]

`kernel_pure` requires `attestation_axioms = []`. The witness travels in
bundles unchanged (the body is already opaque JSONB to the carrier).

### 4. SQL step 104: `cert.attestation` binding + auto-derivation

- `cert.attestation(axiom_name TEXT UNIQUE, claim_id BIGINT REFERENCES
  cert.claim, stmt_sha256 TEXT, registered_at, ...)` — the FK target for
  axiom names. Registering requires the claim's latest certificate to stand
  effectively valid.
- On witness attach with `axiom_tier = 'tool_attested'`: insert (or verify)
  a `cert.derivation` row `conclusion = formal claim, premises = the
  attestation claim_ids, rule = 'tool_attestation'`. **This is the whole
  trick** — from here, existing machinery does everything:
  - `cert.verify` (deep since 102) fails the formal claim if any attestation
    is revoked/expired/unchecked, transitively.
  - `cert.derivation_independent` (103) can require the attesting tools to
    be distinct signers, none of them the prover.
  - `cert.export_bundle` already carries the derivation; a consumer replays
    AxiomAudit on the witness AND re-checks the premises.
- Consumer-side check (`kernel_verify` extension): for each entry in
  `attestation_axioms`, confirm (a) an axiom of that name is in the audit's
  `attested` bucket, (b) `cert.attestation` maps it to `claim_id` with
  matching `stmt_sha256`, (c) the derivation row exists. Any mismatch →
  `unverified` (never `refuted` — absence of binding is absence of evidence).

### 5. Verdict semantics (three-valued honesty preserved)

No new verdict values and no grade lattice: a `tool_attested` formal claim is
`valid` only while its attestation premises stand. We do NOT adopt EG-VAR's
four-grade lattice; Trunkit's equivalent of "downcast on weakened premises"
is the derivation deep-check degrading the verdict, which is stronger (it is
re-checked live, not stamped at mint time). The `axiom_tier` field plays the
role of EG-VAR's grade *label*: queryable, bundle-visible, never silently
upgradable (AxiomAudit will not classify a schema-matching axiom as core).

### 6. What we explicitly do not adopt

- **WProp/Prop phase separation, L1/L2 lift catalogs**: Trunkit's formal
  claims are already stated in the target vocabulary (Mathlib); we have no
  storage-vs-world split to bridge. If/when Trunkit attests external tables,
  revisit lift catalogs (EG-VAR App. D) as `cert.attestation` rows whose
  claims are `comp_sql` probes over imported data.
- **MD5 source hashes** (EG-VAR B4 regex): SHA-256 throughout, consistent
  with `cert.artifact.sha256`.

## Sizing

Steps 2–3 are small (AxiomAudit ~40 LOC; leanbridge plumbing). Step 4 is one
schema file + tests in the pattern of 101/103. The consumer-side kernel_verify
extension is the careful part (it crosses the local/94–95 overlay). Suggested
order: 104 table + auto-derivation first (useful standalone as "attested
premises" bookkeeping), then AxiomAudit buckets, then the bridge plumbing,
then consumer verify.

## Threat notes (from EG-VAR's boundaries, translated)

- A semantically wrong but registered attestation still certifies a wrong
  formal claim (EG-VAR limitation (ii)). Mitigation is 103's independence
  check on the attesting signers plus revocation — trust is withdrawable.
- Name-collision games are blocked by the stmt-hash suffix (EG-VAR's Lemma
  P.3 analogue) — an axiom name can't be re-bound to a new proposition
  without changing its name.
- The prover's Lean environment can declare arbitrary axioms; soundness
  rests on the *audit*, not the build — same stance as today's T1 bridge.
