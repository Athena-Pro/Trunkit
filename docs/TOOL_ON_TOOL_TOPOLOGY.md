# Tool-on-tool topology: hidden Betti features in Trunkit's own constructions

*Analysis run 2026-06-01. Every number below was computed by Trunkit's own
`dfa_betti` kernel (`calx.kernel.check_dfa_betti` / `cert.kernel_dfa_betti`,
step 94) applied to Trunkit's own constructions — the topological kernel
measuring the system that contains it.*

## The lens

A graph is a 1-complex; its homology is fully determined by:

- **β₀** = connected components (undirected)
- **β₁** = E − V + β₀  (circuit rank — independent cycles)
- **χ** = V − E = β₀ − β₁  (βₙ = 0 for n ≥ 2)

Pointing this at Trunkit's *internal* graphs (call graph, schema dependencies,
the ledger DAG) turns design intentions stated in prose into measurable
invariants.

## Findings

| Construction | β₀ | β₁ | χ | The loop |
|---|---|---|---|---|
| cert_kernel call graph | 1 | **1** | 0 | shared `gcd` reuse cycle |
| ledger entanglement DAG (with derivation) | 1 | **2** | −1 | provenance + derivation cycles |
| kan reflexive closure (`kan_in_kan`) | 1 | **1** | 0 | the `kan_self` self-loop |
| cross-DB anchor (Porter ⇄ cert) | 1 | **1** | 0 | envelope ⇄ ledger_root mutual ref |

### 1. β₁ counts primitive reuse — invisible to the design model
The cert_kernel dispatch *looks* like a tree (verify → 5 kernels ⇒ β₁ = 0). The
real graph has **β₁ = 1**, because `gcd` is shared by both `crt` and
`unit_fraction`, closing `verify → crt → gcd ← unit_fraction ← verify`. Modeling
the "designed" version with private gcds drops β₁ to 0 and raises β₀ to 2. The
single Betti bit *is* the DRY-factoring. `dfa_betti` itself contributes 0 (no
shared deps) — topologically the most self-contained kernel, fitting its status
as the newest, LQLE-imported one.

### 2. The immutable ledger is intrinsically multi-cycle (the deep finding)
A hash **chain** is acyclic (β₁ = 0) — verified directly. The step-95 ledger is
not a chain but a **Merkle DAG**: each cert entangles `prev_hash` (time),
`inference_hash` (curry provenance), and `premise_hashes` (derivation). A single
`modus_ponens` derivation (claim C ⊢ from P₁, P₂) raises **β₁ from 0 to 2** —
two independent cycles, one per premise, because each premise is already
chain-linked to the conclusion.

> **β₁ of the ledger = the number of independent ways tampering is detected.**
> A pure chain (β₁ = 0) is truncatable from an end; each entanglement cycle binds
> a record into the web so it cannot be excised without breaking multiple cycles.
> The "entanglement" named in plain English in step 95 has an exact homological
> value, strictly greater than a chain's.

### 3. Every self-referential construction has the same β₁ = 1 fingerprint
`kan_in_kan` (the `kan_self` endofunctor) is a self-loop → β₁ = 1. The cross-DB
anchor (Porter envelope embeds `cert.ledger_root()`; cert records the envelope
hash) is a mutual reference → β₁ = 1. Both share the minimal-cycle signature
(V=1, E=1, β₁=1 — identical to a DFA self-loop). Self-reference across every
Trunkit layer is, homologically, always exactly one loop.

## The self-audit (step 97)

`cert.certify_ledger_betti()` makes this a re-checkable, self-applied claim:

1. `cert.ledger_graph()` extracts the live cert/curry entanglement graph
   (vertices = ledger records; edges = prev_hash / provenance / premise refs)
   as a `dfa_betti` proof object.
2. The `dfa_betti` kernel measures (β₀, β₁, χ).
3. The result is recorded as a `cert.claim` + certificate, with the **witness
   being the graph itself** — so any consumer re-runs the kernel and must get the
   same β₁ (no trust in the producer).

**Honesty:** it certifies the *measured* signature, never an aspirational
threshold. On the live ledger (V=655, single connected chain, no derivations
recorded) the audit reports **β₀=1, β₁=0, χ=1** — truthfully "pure chain, no
entanglement cycles yet." Injecting one derivation in a throwaway transaction
took it to **β₁=2** and rollback restored β₁=0, confirming the detector is real,
not hardcoded.

## Why this matters

The Betti kernel, pointed back at its host, found that Trunkit's three signature
properties each correspond to a topological cycle that was *designed as prose,
not as topology*:

- **reuse** (DRY kernels) → β₁ of the call graph
- **immutability / entanglement** (the ledger) → β₁ ≥ 2 once derivations exist,
  strictly above a chain
- **reflexivity** (`kan_in_kan`, cross-DB anchor) → β₁ = 1 self-loops everywhere

These were intentions; the topological kernel makes them *invariants the system
can measure and certify about itself*. "Is the ledger still well-entangled?"
becomes the re-checkable claim `cert.certify_ledger_betti()` — β₁ as a health
metric for tamper-resistance.

## Reproduce

```bash
make apply-trunkit                                   # loads steps incl. 94 + 97
psql $TRUNK_DSN -c "SELECT cert.ledger_graph()"      # the entanglement graph
psql $TRUNK_DSN -c "SELECT * FROM cert.certify_ledger_betti()"  # measure + certify
# consumer re-check, no DB trust:
trunkit export <claim_id> | python tools/verify_bundle.py -
```
