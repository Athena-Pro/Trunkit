# Design: Deci-Core Stratified Execution Certificates and Gate-Search

**Status:** design, not yet implemented (independent of `feat-cert-lifecycle`'s
own scope — this adds a new `subject_kind`, not a change to lifecycle/signer
semantics; can land before or after `DESIGN_TOOL_ATTESTATION_TIER.md`.)
**Source:** `C:\AI-Local\deci-core\stratified.py` (local checkout, read in
full 2026-07-21), `classic_math.py` (Collatz + sieve reference) in the same
repo; calx's existing cert-family pattern (`recurrence.py`/`93`,
`congruence.py`/`101`) and the `integers`/`factorizations` schema (`01`).
**Depends on:** `cert.claim`/`cert.method`/`cert.certificate` (`40_cert.sql`),
nothing from steps 100–104.

## Problem

`stratified.py` encodes its accumulator as `A = 2^s2 × 3^s3 × Q`: opcodes 3/4
(`PACK×2`/`PACK×3`) push onto two Gödel-style prime stacks, opcodes 5/6
(`UNPACK÷2`/`UNPACK÷3`) pop them, and `analyze_residue()` reports the
decomposition at halt. calx already stores exactly this kind of data —
`factorizations(n, prime, exponent)` is a full prime-exponent vector for
every `n` in `integers` — but the two have never been connected. The
temptation is to describe this as "prime sieving as logic-gate search" in the
abstract; the point of this doc is to pin down which parts of that are a
real, buildable feature and which are just a suggestive metaphor.

## Grounding: what the stratified model actually is (not analogy)

Read directly from source, not from memory:

- Opcodes are digits `0`–`9` plus `;`: `0` COMPARE, `1`/`2` ADD/SUB 1,
  `3`/`4` PACK×2/×3, `5`/`6` UNPACK÷2/÷3 (self-skipping the next instruction
  on failure — odd `A` under `5`, non-multiple-of-3 `A` under `6`), `7`/`8`
  READ/WRITE through register `R`, `9`/`;` are loop brackets on `A`.
- `analyze_residue()` strips factors of 2 then 3 off the halting `A` and
  reports `(s2, s3, Q)`. `Q > 1` with `s2 = s3 = 0` is flagged as "escaped
  stratification" — the program left the two-register model and became raw
  arithmetic.
- The model is fixed at exactly two stack primes (2 and 3) by construction —
  there is no opcode for pushing/popping any other prime. This is not
  generalizable to "N registers" without inventing new opcodes; don't design
  as if it were.

## Three separable pieces (not one feature)

The three formalisms in the originating discussion differ enough in
tractability and dependency order that they should not be built as one PR.

### 1. `cert.stratified_run` — execution certificate (build first)

A new cert family, same shape as `93_cert_recurrence.sql` /
`101_cert_congruence.sql`: pure-Python mirror (`calx/stratified.py`, thin
wrapper importing the actual interpreter logic — vendor or depend on
deci-core's `stratified.py` directly rather than re-deriving its semantics)
+ SQL registry + `probe_sql` that re-verifies.

- **Subject:** `(code, input_stream, initial_A, max_steps)`.
- **Claim:** `(halted, final_A, output, steps, s2, s3, Q)`.
- **Verify:** literally instantiate `DeciCoreStratified(code, input)`, set
  `A = initial_A`, run to halt or `max_steps`, and diff every field against
  the claim. Exact, deterministic, no floats — same "regenerate and compare"
  stance as `recurrence_verify`/`congruence_verify`. Never raises out of the
  checker: a step-budget exhaustion or opcode-stream mismatch is a refutation
  (`ok=false`), not an error, matching the vanishing-leading-coefficient /
  non-coprime-moduli precedent in 93/101.
- **Free connection to existing schema:** for any `n` already present in
  `integers`/`factorizations` (i.e. within whatever limit the sieve has
  covered), `(s2, s3, Q)` is *already derivable* with no new table — `s2` and
  `s3` are just `factorizations.exponent` at `prime IN (2, 3)`, and `Q` is
  `n` divided by those two prime powers. Expose this as a read-only view or
  function (`stratified_decomposition(n)`), not a new certificate — it's a
  restatement of columns that already exist, so there is nothing to verify
  that isn't already exact by construction.

### 2. Squarefree bit-vector SAT search (defer)

Restricting to `is_squarefree = TRUE` rows (already a flagged column on
`integers`) turns a prime factorization into a bit-vector — multiplication
by a fixed prime is a bit flip, and "search for n satisfying predicate P" is
SAT over which of the first *k* primes are set. But for any predicate
expressible over existing columns (`omega`, `big_omega`, sequence
membership, etc.) this is already a plain `WHERE` scan over a bounded,
already-enumerated table — no NP-hard solver earns its keep there. A SAT
solver dependency (`python-sat`, `z3`) is only justified once a search
predicate needs *execution* semantics from piece 1 (e.g. "does `n`, read as
a deci-core program, halt within K steps") that can't be precomputed as a
static column. Don't add the dependency before that predicate exists.

### 3. Reachability/BFS over `(s2, s3, Q)` — the actual "sieve as gate
   search" reframing (build last, depends on #1)

This is not a sieve at all once stated precisely — it's BFS over the
configuration graph of a 2-counter machine, where the fixed finite control
is whatever deci-core program supplies the PACK/UNPACK transitions. General
counter-machine (Petri net) reachability is decidable but can be
Ackermann-hard in the worst case; any *concrete* deci-core program in play
here is small and already step-bounded by convention (`max_steps` is
enforced everywhere else in calx), so this stays tractable in practice —
just don't claim the general result is easy.

Implementation shape: this is `trace_orbit`'s sibling from `05_dynamics.sql`
— same `(orbit_id, step, state, cycle_close)` shape, but `state` is an
`(s2, s3, Q)` triple instead of a single integer `n`, and the transition is
parameterized by a deci-core program string instead of a fixed `rel_type`.
Each BFS witness path is exactly a piece-1 execution trace, so a discovered
reachable state is certified by minting a `cert.stratified_run` claim for
the path that reached it — piece 3 produces inputs to piece 1, not the
reverse. A missing witness within the explored bound is `unverified`
(absence of evidence), never `refuted` (which would claim unreachability) —
same three-valued discipline as everywhere else in `cert.*`.

## What we explicitly do not build yet

- No fourth `feat-cert-lifecycle` step piggybacked onto 100–104 — this is a
  new `subject_kind`, orthogonal to lifecycle/revocation/signer-independence.
- No SAT solver dependency until a concrete execution-dependent predicate
  needs one (see piece 2).
- No claim of an N-register generalization — deci-core's stratified model is
  fixed at primes 2 and 3.
- No new opcode language or interpreter fork — piece 1 should depend on or
  vendor deci-core's `stratified.py` rather than re-implement its semantics,
  so the two stay provably in sync (same reasoning as calx already using
  `recurrence.py`/`congruence.py` as the single source of truth their SQL
  mirrors).

## Sizing / suggested order

1. `cert.stratified_run` — comparable size to `101_cert_congruence.sql`
   (~110 SQL lines) + a Python wrapper + tests in the pattern of
   `test_congruence.py`.
2. `stratified_decomposition(n)` read-only helper over existing
   `factorizations` — near zero cost, no new table.
3. BFS/orbit tracer variant, once (1) exists to certify individual witnesses.
4. SAT-over-squarefree, only if/when a predicate needing execution semantics
   (not expressible as a static column) actually shows up.

## Threat / correctness notes

- Execution-certificate verification is exact re-derivation (same
  interpreter, same inputs) — nothing here trusts a cached result.
- `max_steps` must be caller-bounded and enforced inside the checker (as it
  already is by convention in `recurrence`/`congruence`), or a re-check via
  `cert.check` could hang the ledger on a non-halting program.
- A BFS pass proves reachability, never non-reachability — don't let a
  `--exhausted-search` result get reported as `refuted`.
