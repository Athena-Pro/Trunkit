# Trunkit — Erdős×AI Capability Fit & Missing-Capability Targets

*Prepared 2026-06-20. Companion to `Trunkit_arXiv_Expansion_Review.md`. Source of record for the AI contributions: the [teorth/erdosproblems "AI contributions" wiki](https://github.com/teorth/erdosproblems/wiki/AI-contributions-to-Erd%C5%91s-problems) (snapshot covering Nov 2025 – Mar 2026), cross-checked against [erdosproblems.com](https://www.erdosproblems.com) and primary writeups. Package inspected: `trunkit` 0.2.6.*

> **Brief.** Map the recent wave of AI-solved (and AI-assisted) Erdős problems onto Trunkit's existing layers, and treat the **gaps as the build queue**. The point of this document is not the problems Trunkit can already host — it is the problems it *can't*, and what each one tells us to build.

---

## 0. Framing: don't count problems, count *artifact types*

A solved Erdős problem is not a single kind of object. The tracker records, per problem, **what artifact the AI actually produced** — and *that* is what determines Trunkit fit, not the problem's headline. Trunkit attests artifacts (witnesses, probes, pinned proofs), so the right unit of analysis is the artifact, not the theorem.

Across the tracker, AI output falls into seven artifact types. Trunkit fit is decided entirely by which one a given solution is:

| # | Artifact type | Representative problems | Trunkit layer | Fit today |
|---|---------------|-------------------------|---------------|-----------|
| A | **Lean formalization** | ~100 in §2(b): #93, #115, #205, #397, #457, #728, #1026, #1051 … | `cert.formal_external` | **Anchor only** — SHA-pinned, stays `unverified` |
| B | **OEIS sequence gen/location** | #271, #334, #396, #860, #872 | `calx` `oeis-load/match` | **Direct** |
| C | **Finite numerical verification** | #42, #43, #650, #757, #993, #1044 | `cert.comp_sql` | **Direct if arithmetic** |
| D | **Counterexample / explicit construction** | #486, #514, #563, #665, #850 (arith.); #36, #52, #67, #106, #391, #507, #1097, unit-distance (combinatorial) | `cert.witness_carry` | **Split** — arith. fits, combinatorial doesn't |
| E | **Improved explicit bound / inequality** | #348→#848, #524, #650, #788, #513, #349 | — | **Missing** |
| F | **Asymptotic / analytic theorem** | #1196 bound itself, density/almost-all statements | `cert.formal_external` | **Anchor only** |
| G | **Provenance / credit structure** (the tracker *itself*) | the 1(a)–1(d) × 🟢🟡🔴 taxonomy | `curry` + `cert.derivation` | **Latent fit** |

The single most useful observation: **AI's productivity on Erdős problems is concentrated in exactly the artifact types Trunkit is weakest at** — Lean proofs it can pin but not check (A), and combinatorial constructions it has no schema for (D-combinatorial). That is the gap, and it is the whole point of this scan.

---

## 1. What Trunkit can host *today* (the baseline, briefly)

So the gaps below are properly scoped, here is what already works with zero new mechanism:

- **Anchor any Lean proof** as a `formal_external` claim: store the artifact, SHA256-pin it, record the toolchain. The claim is honestly `unverified` (Trunkit does not run Lean), but it is *carried, hashed, and bundle-portable*. This already covers the entire §2(b) formalization wave at the provenance level.
- **The #1196 / #164 / #1217 primitive-set family is partly in-scope right now.** #1196 (Erdős–Sárközy–Szemerédi, the GPT-5.4 "Book proof") is *multiplicative* number theory: a set is primitive iff no element divides another. The asymptotic Erdős-sum bound is a `formal_external` anchor — but the **primitivity of any given finite set, and any divisibility-chain witness, is a `comp_sql` probe over calx's factorization tables**. calx already models divisibility on ℤ[1..N]; attesting "this set is primitive" or "this chain divides" is a re-runnable probe today. Good first demo.
- **OEIS matches** #271, #334, #396, #860, #872 land directly in `oeis-load`/`oeis-match`.
- **Arithmetic counterexamples** expressible over ℤ[1..N] (the "cheap counterexample to previous formulation" cluster — #486, #514, #563, #665, #850) become `witness_carry` claims of `kind: counterexample`, replayable in-DB.

Everything else needs a new capability. Those are the targets.

---

## 2. Missing capabilities, ranked by leverage

Ranking criterion: **(volume of AI-Erdős output unlocked) × (fit with Trunkit's existing DNA) ÷ (engineering cost)**. Highest first.

### T1 — Lean verification bridge: turn `formal_external` from *pin* into *check*

**Evidence.** The largest single category in the entire tracker is Lean formalization (§2(b) alone is ~100 problems, overwhelmingly **Aristotle/Harmonic**, plus Seed Prover, AlphaProof, Numina, Aleph). Marquee fully-AI Lean solutions: #205, #397, #457, #728/#729, #1026, #1051, #897, #966, #1007, #1043, #1047.

**The gap.** `cert.formal_external` SHA-pins an external artifact and records its hash, but Trunkit **never executes the proof** — by design the claim sits at `unverified` until a human runs Lean. That means the biggest pile of AI-Erdős output is *carried* but not *verified*, which is the one thing a proof-carrying system is supposed to close.

**Build.** A `formal_lean` sub-tier with an **out-of-process Lean checker hook**: `cert.check` shells to a pinned `lake`/Lean toolchain in a sandbox, records `{toolchain_version, mathlib_rev, exit_code, checker_sha}`, and flips the claim to `valid`/`refuted` with the checker transcript stored as the witness. Trunkit stays dependency-light (the heavy toolchain lives outside the 1.5 MB core, behind the hook), preserving the README's "no 3 GB compiler in the box" promise while finally making a Lean-formalized Erdős proof *re-verifiable by a consumer*.

**Why first.** Converts the largest body of AI-Erdős work from anchored to verified, and it is the most on-mission capability Trunkit is currently missing.

### T2 — Finite combinatorial-object store + property probes (`comp_sql` beyond ℤ)

**Evidence.** AI's flashiest Erdős wins are *non-arithmetic constructions*: OpenAI's **disproof of the planar unit-distance conjecture** (a new family of point configurations via algebraic number theory); the **AlphaEvolve** construction sweep (#36, #52, #67, #106, #391, #507, #1097); geometry/incidence (#659 Sheffer–Lund); and the **Kleitman set-system** cluster (#447, #487, #497, #498, #505, #1023).

**The gap.** calx knows exactly one kind of object: ℤ[1..N] and its factorization lattice. There is **no schema for graphs, point sets, or set systems**, so none of these constructions or counterexamples can be hosted, let alone re-checked. This is bucket D's combinatorial half — precisely where AI has been most original.

**Build.** Generic finite-structure tables (`vertices/edges`, `points/coords`, `set_membership`) plus **parameterized property probes** (`is_unit_distance_graph(point_set, d)`, `is_primitive_family(sets)`, `chromatic_ge(graph, k)`). A construction or counterexample then becomes a `witness_carry` claim whose witness is the explicit object and whose verification is a `comp_sql` probe over these tables — re-runnable by a consumer with no trust in the producer. This is the natural generalization of the existing factorization probes from "integers" to "finite combinatorial objects."

**Why second.** Second-largest unlocked volume, strong DNA fit (it *is* `comp_sql`, just over a richer domain), but a real schema-design cost.

### T3 — Numeric-bound / inequality cert tier

**Evidence.** A large share of outcomes are graded, not boolean: "improved bound" (#524, #788, #349), "explicit bound derived" (#848), "full solution **stronger than literature**" (#650). AlphaEvolve's role on #650 was literally "optimal construction found numerically."

**The gap.** `cert` is three-valued boolean (`valid`/`refuted`/`unverified`); it cannot express "claimed bound = X, sharper than prior Y," nor re-check an inequality at witnesses.

**Build.** A tier that records a claimed inequality + a **re-runnable numeric evaluation** at finite witness points, with a partial order over competing bounds. This dovetails with the **Weighted-Nerode / exact-extremal-bounds** recommendation already in `Trunkit_arXiv_Expansion_Review.md` (item ①) — the witness-extraction machinery there is the same machinery a bound-cert needs. **Build T3 and arXiv-① together.**

### T4 — OEIS conjecture→attestation workflow (extend, don't invent)

**Evidence.** Live instances #271, #334, #396, #860, #872 used AI for OEIS sequence generation/location.

**The gap.** `oeis-match` exists, but there is no first-class "ingest an OEIS conjecture → attest a closed-form/recurrence match as a claim" flow.

**Build.** Thin workflow on top of existing `oeis-load`/`oeis-match`: register the conjecture, attest the match as `comp_sql` (finite agreement) or `formal_external` (proved closed form). Low cost, narrow payoff. **Already flagged as Tier 4 in the arXiv review** — the Erdős data just confirms there is live demand.

### T5 — Solution-provenance DAG: attest the *credit structure*, not just the proof

**Evidence.** The tracker's own architecture — sections **1(a) original / 1(b) independent-rediscovery / 1(c) prior-art / 1(d) human+AI**, each cell tagged 🟢 full / 🟡 partial / 🔴 incorrect, with explicit "Literature result," "found on," and "Similar proofs?" columns — *is itself a multi-contributor, three-valued attestation ledger.* See #397 and #728 (AI + Lean + prior literature + human verification, all needing attribution) for the canonical shape.

**The gap.** Trunkit has the parts (`curry` append-only provenance, `cert.derivation` proof-composition DAG, three-valued honesty) but no schema that models "an Erdős solution as a DAG of contributors, each with a trust tier."

**Build.** A `derivation`-backed schema: nodes = {prior literature, AI output, human step, Lean artifact}, edges = "used / formalized / corrected," each node carrying a contributor trust tier and a 🟢/🟡/🔴 mapped onto `valid`/`partial`/`refuted`. This is the highest-*novelty* fit — it turns Trunkit into a **credit-and-correctness ledger for AI-assisted mathematics**, exactly the bookkeeping problem the community is currently solving by hand in a wiki. Low-medium cost (mostly reuses `cert.derivation`), and it is pure Trunkit DNA: *the proof carries its provenance*.

---

## 3. Honestly out of scope (anchor-only or no inflow)

- **Asymptotic / analytic theorems (bucket F).** #1196's actual Erdős-sum bound, density statements, almost-all results — not finitely checkable in-DB. Trunkit can anchor the Lean proof (`formal_external`) and attest *finite corollaries* (primitivity of a given set, a chain witness), but **not the theorem**. Don't oversell calx here.
- **Category theory (kan).** Thomas Bloom's own assessment is that AI "hasn't done anything interesting in category theory." So `kan` gets **zero Erdős inflow** — its expansion path remains internal structural invariants, not external proof intake. Worth stating plainly so kan isn't mis-prioritized.
- **Reporting-bias caveat.** §1(a) carries a large hidden tail of 🔴 failures; AI success *rates* on Erdős problems are far lower than the solved list suggests. Any Trunkit "AI-math attestation" framing must keep the three-valued honesty (a `refuted`/`unverified` is a first-class outcome, not a gap to paper over).

---

## 4. Recommended build queue (highest leverage first)

1. **T1 — Lean verification bridge** (`formal_lean` check hook). Unlocks the single largest category (~100 Lean-formalized problems) and is the most on-mission missing piece. Keep the toolchain outside the core.
2. **T2 — Finite combinatorial-object store** (`comp_sql` over graphs/points/sets). Unlocks the AlphaEvolve constructions, unit-distance-style counterexamples, and the Kleitman set-system cluster — the work where AI is most original.
3. **T3 — Numeric-bound cert tier**, built jointly with **Weighted-Nerode (arXiv review ①)**. Captures the large "improved/explicit bound" class that boolean cert can't express.
4. **T5 — Solution-provenance DAG.** Highest novelty, low-medium cost, pure DNA fit; turns the community's hand-maintained credit wiki into an attestable ledger.
5. **T4 — OEIS conjecture-attestation workflow.** Cheapest; extends existing `oeis-match`; do it as a warm-up demo (pairs with arXiv-review Tier 4).

**First demo to ship now (no new capability):** attest the **#1196 primitive-set family** at the finite level — generate ℤ[1..N], probe primitivity and a divisibility-chain witness via `comp_sql`/`witness_carry`, and anchor the GPT-5.4/Lean proof of the asymptotic bound as `formal_external`. It exercises the baseline end-to-end and sets up T1 (verify the anchor) and T5 (record the AI+human+literature provenance) as the next two increments.

---

## Sources

- [AI contributions to Erdős problems — teorth/erdosproblems wiki](https://github.com/teorth/erdosproblems/wiki/AI-contributions-to-Erd%C5%91s-problems)
- [Erdős Problems site](https://www.erdosproblems.com) · [#1196](https://www.erdosproblems.com/1196) · [#1196 discussion](https://www.erdosproblems.com/forum/thread/1196)
- [Tao, "Primitive sets and von Mangoldt chains: Erdős Problem #1196 and beyond"](https://terrytao.wordpress.com/2026/05/03/primitive-sets-and-von-mangoldt-chains-erdos-problem-1196-and-beyond/) · [arXiv:2605.00301](https://arxiv.org/abs/2605.00301)
- [Physics World — "AI-led solutions of Erdős problems spark debate"](https://physicsworld.com/a/ai-led-solutions-of-erdos-problems-spark-debate-over-the-future-of-mathematics/)
- [Resolution of Erdős Problem #728 (Aristotle/Lean writeup), arXiv:2601.07421](https://arxiv.org/pdf/2601.07421)
- [Boris Alexeev — "Formalization of Erdős problems" (Xena blog)](https://xenaproject.wordpress.com/2025/12/05/formalization-of-erdos-problems/)
- Companion: `Trunkit_arXiv_Expansion_Review.md` (local)
