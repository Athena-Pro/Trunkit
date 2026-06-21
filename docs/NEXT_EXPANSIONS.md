# Trunkit — next-expansion assessment (post-0.3.0)

*Prepared 2026-06-21, against live `trunkit` 0.3.0. Evaluates four proposed expansions against Trunkit's design DNA and existing primitives, then recommends a sequence. Same lens as the arXiv/Erdős reviews: what's a true gap, what reuses what we have, and what would break minimality.*

> **STATUS — all four shipped (2026-06-21, merged to `main`):** #4 recurrence certs (`93_cert_recurrence.sql`), #3 exact-domain shield (`94_cert_exactness.sql`), #2 sequence morphisms (`95_cert_morphism.sql`), #1a holographic/Merkle commitments (`96_cert_holographic.sql`). The end-to-end `test_stack_coherence` ties them together; 26 layer tests green. **#1b** (true ZK/STARK) remains deliberately **external-only / future** — a heavy crypto prover stays outside the package behind a `formal_external`-style tier.

## Design DNA these must respect
psycopg-only core; everything **in-SQL and re-runnable**; **three-valued honesty** (`valid`/`refuted`/`unverified`); **verify-not-solve** (cert records that a claim was *accepted by a method*, it doesn't discover proofs — the Lean bridge *checks*, it doesn't prove); proofs are **carried + composed** (`cert.derivation` DAG); the ledger is **append-only + hash-chained**; heavy engines live **outside** the package behind an external-checker tier (`formal_external` pattern).

## Verdict at a glance

| # | Proposal | Fit | Cost | Reuses | Status |
|---|----------|-----|------|--------|:--:|
| 4 | C-finite / P-finite recurrence certificates | ★★★ keystone | low | calx numeric exactness, `seq_vector`, `comp_sql` | ✅ shipped (`93`) |
| 3 | Exact-domain type shields | ★★★ | low | `A0_interval` (already shipped), `numeric[]`, `cert.check` | ✅ shipped (`94`) |
| 2 | Functorial / morphism hooks | ★★ strong, research-y | med | `kan` functors, Nerode `70_morphism`, `struct_kan` | ✅ shipped (`95`) |
| 1 | Proof compression / succinct | ★★ split | (a) low-med / (b) high | ledger hash-chain, `derivation` DAG | ✅ (a) shipped (`96`); ⏳ (b) external-only |

These reinforce each other: a **recurrence certificate (#4)** is *exact* (#3), *compact* (#1a), and the object a *morphism hook* (#2) maps between. The cohesive next arc is **"exact, compact, structural sequence certificates."**

---

## #4 — C-finite / P-finite recurrence certificates · KEYSTONE, do first
A holonomic recurrence (coefficients + initial terms) **is** a tiny certificate that regenerates a sequence. Verifying "sequence S satisfies recurrence R" is a cheap **exact** in-SQL evaluation over `numeric[]`: regenerate k terms from R, compare to the stored terms. No dependency, pure SQL.

- **Why keystone:** it's the *exact identity verifier* sitting behind the OEIS cosine candidate generator — cosine *proposes* a shape, the recurrence certificate *proves* it (closing the "cosine is only a heuristic" gap honestly). It also delivers most of #1's "carry a tiny certificate instead of the whole trace" for the sequence domain (a 4-term recurrence replaces a 10,000-term log), and it is inherently exact (#3).
- **Shape:** a recurrence cert stores `{order, coeffs (C-finite: constants; P-finite: polynomials in n), init terms}`; the probe regenerates and checks equality — `valid` to the verified length, `unverified` beyond it (three-valued, never claims more than it checked). It also lets two sequences be attested as sharing a recurrence (feeds #2).
- **Stance:** *verify* a supplied recurrence (cheap, in-core); do **not** auto-*discover* it — Gosper/Zeilberger-style derivation is real symbolic math and belongs in an optional external tool, not the psycopg core (same verify-not-solve line as the Lean bridge).

## #3 — Exact-domain type shields · cheap, high-integrity, do second
Formalises the float-quarantine we already practise ad hoc (cosine is `float8` → only ever a *candidate*; exact terms decide). Add a **domain tag** to claims/probes — `exact_int | rational | algebraic | interval | float_heuristic` — and one guard in `cert.check`: a `float_heuristic` probe can **never** yield `valid`, only `unverified`/candidate. That is "no irrational leakage into a valid verdict," enforced once at the cert boundary.

- **Reuses:** `A0_interval.sql` (interval algebra) already shipped in the Nerode A-phase — promote it as the exact-interval primitive; `numeric[]` is already exact in calx/oeis. cert only needs the tag column + the guard.
- **Payoff:** makes the trig/continuum ↔ integer/p-adic bridge safe by construction (the exact place the user flagged), and it's almost free. Recurrence certs (#4) register as `exact_int`, so #3 and #4 dovetail.

## #2 — Functorial / morphism hooks · the principled successor to cosine, do third
The right answer to "cosine captures only linear similarity." Trunkit **already** has the pieces: `kan` functors / natural transformations / faithfulness checks, and Nerode **DFA morphisms** (`70_morphism.sql`). A functorial hook = attest a *verified morphism* between two sequence-generating automata (or recurrence operators) that preserves structure → a `struct_kan` certificate. "A and B are related by a hidden algebraic twist" becomes a **checked morphism**, not a cosine guess.

- **Reuses:** `kan` + `70_morphism` + `cert struct_kan`. Complements OEIS cosine exactly as #4 does arithmetically: cosine = candidate, morphism = structural proof.
- **Honesty:** full sheaf-theoretic / Langlands functoriality is out of reach; the tractable minimal slice is **automaton / recurrence-operator morphism attestation**. Scope it that way; don't market it as Langlands.
- Cost is medium (it's the most research-y), which is why it follows #3/#4 rather than leads.

## #1 — Proof compression / succinct verification · split it
Two very different halves:

- **(a) Condensed / holographic serializer — in-core, minimal, worth doing.** Content-address and **dedup witnesses by hash**, share sub-derivations through the existing `cert.derivation` DAG, gzip bundles, and **Merkle-root the trace** so a consumer verifies the small root and fetches only the leaves it needs. Reuses the ledger's existing hash-chain (`row_hash`) and the derivation DAG; dependency-light (stdlib `zlib`/`hashlib`). This is the realistic "holographic verification" for Trunkit.
- **(b) True ZK / STARK / algebraic-IOP — keep external.** A real succinct-argument prover is a heavy cryptographic dependency — squarely against the "no 3 GB toolchain" promise. If a domain ever needs it, wire it through a **`formal_external`-style tier**: an external prover emits the succinct proof, Trunkit anchors it and verifies via an external verifier command — exactly the Lean-bridge pattern (check, don't bundle the prover).
- **Crucially:** #4 already delivers the headline payoff ("tiny certificate, not the whole execution trace") for sequences. Do #4 first; reach for ZK only where a recurrence/closed-form certificate genuinely can't express the property.

---

## Recommended sequence
**#4 → #3 → #2 → #1(a)**, with **#1(b) external-only**. Each step is minimal, in-SQL, and reuses shipped primitives; together they turn the OEIS/Diophantine story from "cosine heuristic + exact prefix" into **exact, compact, structurally-verified sequence certificates** — without adding a single heavy dependency to the core.

*Guardrails to hold throughout:* verify-not-solve; heavy engines external; floats never reach a `valid` verdict; every probe re-runnable in-DB; ledger stays append-only.
