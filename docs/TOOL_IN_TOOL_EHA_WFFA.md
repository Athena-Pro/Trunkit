# Tool-in-tool: tension analysis of the EHA/WFFA/extremal layer

*Analysis run 2026-06-19, after landing Phase-4 quantitative automata (A0–A5).
Method follows `TOOL_ON_TOOL_TOPOLOGY.md`: point Trunkit's own kernels at
Trunkit's own new constructions. The Betti numbers below were computed by the
real `calx.kernel.check_dfa_betti` (step 94); the three-valued verdicts use the
same `valid`/`refuted`/`unverified` discipline as `cert`.*

## What was pointed at what

The new layer records three kinds of claim about a scenario-restricted product:
the **EHA** compilation, the **WFFA product** (`[[W']] = [[W]] ∩ L(H)`), and the
two **extremal bounds** (`extremal_best`, `extremal_worst`) carried by
`payoff_trace` witnesses. `extremal_consistency` cross-checks the two bounds.
The question: do these claims introduce any tension — a contradiction the ledger
could be made to assert — and does the new layer keep Trunkit's
contradiction-soundness invariant (every `refuted` is genuine; empty ≠ refuted)?

## Findings

| # | Probe | Result | Verdict |
|---|---|---|---|
| 1 | `dfa_betti` on the new claim graph | β₀=1, **β₁=1**, χ=0 (designed view β₁=0) | reflexive fingerprint, expected |
| 2 | contradiction predicate `worst ≤ best` | valid / refuted / **unverified** on empty | sound, empty-guard respected |
| 3 | soundness boundary of `refuted` | exact only on piecewise-linear payoffs | **latent tension — patched** |
| 4 | `payoff_trace` replay | reproduces the bound (witness_carry style) | sound under determinism |
| 5 | cross-scenario monotonicity | **not checked** | open gap |

### 1. The consistency check is exactly one homological loop
The claim graph `{eha, wffa → product → best, worst → consistency}` has **β₁ = 1**;
dropping the cross-check node drops it to β₁ = 0. So the `worst ≤ best` self-check
*is* a single circuit binding the two quantitative claims — the same β₁ = 1
signature the topology doc found for `kan_in_kan` and the Porter⇄cert anchor.
Self-reference in the new layer is, homologically, one loop, like everywhere else
in Trunkit. (Recording the bounds as `cert.derivation` premises of the product
claim would, per the ledger finding in the topology doc, raise the *ledger* β₁
and bind them harder against tampering — an available strengthening.)

### 2. The contradiction predicate respects the empty-engine guard
`worst > best` is a genuine contradiction. The predicate returns `valid` on the
verified example (best 207, worst 5), `refuted` on a manufactured inversion, and
— critically — `unverified` (not `refuted`) when the product is empty/infeasible
(`best`/`worst` are NULL). This matches the step-79 guard: an unpopulated engine
must not manufacture a refutation.

### 3. The tension the analysis actually caught — and the fix
The extremal optimizer (A4) is **exact only for piecewise-linear payoffs**
(`const`/`bind`/`guard`/`otimes`/`oplus`). The first draft of
`extremal_consistency` emitted `refuted` for *any* `worst > best`. On a
payoff outside that class (e.g. a hypothetical `d²` severity), the optimizer
could be unsound and the apparent `worst > best` would be **manufactured** — a
false contradiction written to the ledger, breaking contradiction-soundness.

**Patched:** added `payoff_is_pwl` / `product_is_pwl`, and `extremal_consistency`
now downgrades `refuted → unverified` whenever the product contains a
non-piecewise-linear payoff. `refuted` is emitted only where the optimizer is
provably exact — so a refutation always denotes a real tension, never a tooling
artifact. This is the AUDIT §3 invariant applied to the new layer.

### 4. Witnesses carry, under a determinism assumption
`payoff_trace` replay (`replay_payoff_trace` → `wffa_path_value`) re-derives the
bound with no producer trust, exactly like `witness_carry`. Caveat: `wffa_path_value`
follows the first enabled transition, so replay reproduces the bound only on a
**deterministic** product (which `wffa_product` of a deterministic WFFA and a
deterministic EHA is). A best-case witness over a nondeterministic product should
assert determinism (or use a max-run replay) before being marked `valid`.

### 5. Open gap — cross-scenario monotonicity is not yet checked
If `L(H₁) ⊆ L(H₂)` (scenario H₁ strictly tighter), soundness requires
`best_{H₁} ≤ best_{H₂}` and `worst_{H₁} ≥ worst_{H₂}`. Two *independently* `valid`
extremal claims about nested scenarios could violate this with **no detector
firing** — a latent tension surface. Recommended follow-up: a monotonicity probe
when two products share a base WFFA under nested EHAs, recorded as its own
three-valued claim (the same shape as `extremal_consistency`).

## Net

The new layer adds no tension with existing calx/kan/cert claims (disjoint
subject: `nerode_automaton` / quantitative payoffs; the only shared resource is
the append-only ledger, which it extends as a chain). Within the layer, the one
contradiction predicate is sound and empty-guarded after the patch; one
cross-claim invariant (monotonicity) remains unguarded and is the recommended
next probe. The tool-in-tool pass did its job: it caught a real soundness hole in
`extremal_consistency` before it could write a false `refuted` to the ledger.

## Reproduce

```bash
python /tmp/tool_in_tool.py        # dfa_betti on the new claim graph + predicate cases
# once A0–A5 are wired into SCHEMA_FILES and applied to the nerode DB:
psql $NERODE_DSN -c "SELECT * FROM nerode.extremal_consistency(<product_id>)"
```
