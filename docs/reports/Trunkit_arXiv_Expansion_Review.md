# Trunkit — Capability Review & arXiv Expansion Scan

*Prepared 2026-06-19. Package inspected: `trunkit` 0.2.6 (sdist from PyPI). arXiv scan: most-recent publications, May–June 2026, sorted by date across cs.LO/PL/CR/FL/AI/MA/DB and math.CT/NT/CO.*

---

## 1. Current capabilities (what the base actually is)

Trunkit 0.2.6 is **proof-carrying-code (PCC) middleware on PostgreSQL** plus a **deterministic-automata engine** and an **agent context-handoff layer**. It is two Postgres instances by design (proof ledger isolated from the automata/agent workload), ~1.5 MB of schemas, dependency-light (`psycopg` only).

| Layer | What it does today | Key surface |
|-------|--------------------|-------------|
| **calx** | Dense prime factorisation of ℤ[1..N]; ω/Ω, p-adic stratification; aliquot/derivative **arithmetic dynamics**; CRT; **OEIS** load + sequence matching | `trunkit generate/validate/oeis-load/oeis-match/compose-match` |
| **curry** | Immutable, versioned constants & functions; append-only computational provenance | `10_curry.sql` |
| **kan** | Category-*structured* meta-layer reflecting Postgres FK graphs into objects/morphisms; checks **structural invariants** (triangle commutativity, product UP, naturality, epi classification) as re-runnable probes — explicitly *not* formal proof | `20–83_kan_*.sql` |
| **cert** | PCC attestation: five method tiers (`comp_sql`, `struct_kan`, `formal_external`, `empirical_corpus`, `witness_carry`); structured witness storage; proof-composition DAG (`cert.derivation`); portable JSONB bundle export; consumer re-verification without INSERT | `40–88_cert_*.sql`, `trunkit verify/check/attest/witness/export` |
| **Nerode** | DFA engine on Postgres: construction, **minimization**, **product**, regex→DFA, session DFAs, sequence cache, certified handoff envelopes; Chomsky/categorical/morphism layers | `nerode/sql/00–98`, `automata.py` |
| **Porter** | Agent context handoff: pre-pack external data (Weather/Ticker/HN sources), certify session boundaries, hand verified context to a fresh model with zero tool calls; **cybernetic DFAs** (rise/oscillate/dead-time/homeostasis) + composite pattern detection | `precache.py`, `sources.py`, `95–97` cybernetic/composite DFAs |

**Design DNA to preserve when expanding:** everything is in-SQL and re-runnable; three-valued honesty (`valid`/`refuted`/`unverified`); proofs are *carried* (witness travels with the claim) and *composed* (DAG); the ledger is append-only and hash-chained; cross-instance links are by value (embedded `ledger_root()` / envelope hash), never by FK.

The natural axes for expansion are therefore: **(a) new cert method tiers**, **(b) richer automata classes in Nerode**, **(c) Porter as a policy/enforcement boundary**, and **(d) new attestable structures in calx/kan**.

---

## 2. Best-fit arXiv papers, ranked by leverage

### Tier 1 — Strong, directly actionable fits

**① Scenario Constraints with Memory — Event-History Automata + Weighted Finite Automata** · [2606.11223](https://arxiv.org/abs/2606.11223) (q-fin.CP, cs.FL)
Introduces **EHAs** (regex event patterns × admissible numeric intervals) and **WFFAs** (weighted automata whose transition weights depend on observed values); takes their **synchronized product** to compute *exact upper/lower payoff bounds* and **extracts interpretable witness event histories** realizing the extrema.
→ *Maps onto Nerode almost one-to-one.* Nerode already has product DFAs, a sequence cache, and certified witnesses; this paper is the recipe for upgrading Nerode from Boolean DFAs to **quantitative/weighted automata** with **exact extremal bounds + witness extraction** — and the witness drops straight into `cert.witness` (`kind: trace`). The cybernetic monitors (`metric_rise_3`, `homeostasis_alarm`) become weighted instead of binary. **Top pick for the Nerode base.**

**② LedgerAgent — Structured State for Policy-Adherent Tool-Calling Agents** · [2606.20529](https://arxiv.org/abs/2606.20529) (cs.AI, cs.CL)
Keeps observed task state in a **separate ledger** rendered into the prompt, and **checks state-dependent policy constraints before environment-changing tool calls**, blocking violations.
→ This is essentially **Porter's envelope generalized into a live policy gate**. Porter already certifies session boundaries and carries verified context; adding a "check policy predicate against the envelope before a tool call" step makes Porter a *pre-commit gate*, not just a pre-cacher. The predicate can be a `comp_sql`/`witness_carry` cert. **Top pick for the Porter base.**

**③ Beyond Accuracy: Measuring Logical Compliance — Rule Violation Score (RVS)** · [2606.20208](https://arxiv.org/abs/2606.20208) (cs.AI, cs.DB, cs.NE)
A metric for how well outputs respect logical rules, distinguishing **hard rules** (strict) from **soft rules** (statistical), **computed via auto-generated SQL queries for Horn rules**, over any relational vocabulary.
→ Almost purpose-built for cert. It's SQL-native, three-valued in spirit, and gives Trunkit a principled **soft-rule / hard-rule cert tier** (`comp_sql` does hard rules today; RVS adds a graded soft-rule attestation). Also a ready-made way to attest the *logical consistency of a corpus* — complements `empirical_corpus`. **Top pick for a new cert tier.**

**④ Sovereign Execution Brokers (SEB)** · [2606.20520](https://arxiv.org/abs/2606.20520) (cs.CR, cs.AI)
A runtime enforcement boundary that **consumes certificates**, verifies a requested mutation matches its **certified execution contract**, checks **validity windows, policy epochs, revocation epochs, and live-state drift**, and records signed decision/outcome records.
→ Trunkit cert has tiers and witnesses but **no temporal lifecycle**: certificates don't expire, can't be revoked, and don't carry validity windows. SEB is the blueprint for adding **`valid_from`/`valid_to`, revocation epochs, and drift re-checks** to `cert.certificate` — turning a static ledger entry into a "short-lived, revocable, auditable capability." Pairs naturally with Porter (②).

### Tier 2 — Strong, slightly more research-y

**⑤ Efficient and Sound Probabilistic Verification for AI Agents** · [2606.20510](https://arxiv.org/abs/2606.20510) (cs.CR, cs.AI)
Enforces **Datalog policies with probabilistic predicates**, computing **sound upper bounds on violation probability** via distributionally robust optimization (no independence assumption needed).
→ cert is currently Boolean/three-valued. This is the path to a **probabilistic cert tier** that emits *bounded* claims ("violation probability ≤ p") with the same soundness discipline — useful when a witness comes from a fallible detector (exactly Porter's external sources).

**⑥ ContractGuard — verifying the contract layer of causal gating** · [2606.18550](https://arxiv.org/abs/2606.18550) (cs.CR)
Inserts a verifier between a tool registry and the gate that layers **signed provenance + typed contract attestation + runtime effect verification**; reduces injection success to zero without over-rejecting honest contracts (evaluated on Claude Opus 4.8 / Sonnet 4.6 / Haiku 4.5 among others).
→ This is `cert.witness` + `cert.derivation` + curry's signed provenance applied to *tool contracts*. Directly reinforces the Porter-as-gate direction (②/④): the thing Porter hands over should be a **signed, typed, effect-verified contract**, not raw context.

**⑦ Neuro-Symbolic Injection of LTLf Constraints (LTLf → DFA)** · [2606.08312](https://arxiv.org/abs/2606.08312) (cs.AI, cs.FL)
Compiles **Linear Temporal Logic over finite traces (LTLf) into DFAs** and uses DFA progression as a differentiable, re-runnable satisfaction signal.
→ Nerode builds DFAs from regex today; an **LTLf front-end** is the right specification language for the cybernetic monitors (`U{3,}`, `(UD){3,}`, dead-time) — those are temporal properties currently hand-encoded as regex. Gives Porter/Nerode a declarative way to author session-safety properties that compile to the existing DFA machinery.

### Tier 3 — Good theoretical extensions to the engine

**⑧ Minimality of Random Moore Automata under Prefix-Dependent Congruences** · [2606.20454](https://arxiv.org/abs/2606.20454) (cs.FL) — Moore automata with **state outputs** and a literal **Nerode-style congruence**. Extends Nerode's DFA model to Moore machines (outputs on states), which is what weighted/labelled monitoring (①) wants underneath. The name is not a coincidence; this is the engine's namesake territory.

**⑨ Learning Alternating Real-Time Automata (AL\*RTA)** · [2606.19822](https://arxiv.org/abs/2606.19822) (cs.FL) — **Active automata learning** (membership/equivalence queries). Nerode currently *constructs* DFAs; this enables **inferring** a session DFA from observed Porter traces rather than hand-authoring it — a "learn the protocol from logs" capability.

**⑩ Tractable Gap-Constraint Languages for Complex Event Recognition** · [2606.18878](https://arxiv.org/abs/2606.18878) (cs.DS, cs.DB, cs.FL) — Near-optimal (under SETH) **subsequence matching with gap constraints** for CER, with **enumeration of all satisfying embeddings**. Directly upgrades Porter's *composite pattern detection* over event streams, and it's DB-shaped so it fits the in-SQL philosophy.

### Tier 4 — calx / kan (number-theory & category base)

calx's expansion path is "new attestable sequence families and dynamics," and the OEIS-conjecture papers are the most actionable because calx already ships `oeis-load`/`oeis-match`:

- **Proofs of several OEIS conjectures on determinants and permanents** · [2606.09913](https://arxiv.org/abs/2606.09913) — turns OEIS *conjectures* into proved closed forms. A natural new calx workflow: ingest an OEIS conjecture, attest a closed-form match as a `comp_sql`/`formal_external` claim.
- **Proof of Conjecture 19 … Elementary Symmetric Partitions** · [2606.09321](https://arxiv.org/abs/2606.09321) — maps on partitions ↔ OEIS sequences; same "attest an OEIS conjecture" pattern.
- **Dual Affine Spiral Orbits on ℤ²** · [2606.15506](https://arxiv.org/abs/2606.15506) — a linear recurrence driven by the Gaussian integer (1+i) that *generates an OEIS sequence (A396151)*; squarely calx's "arithmetic dynamics + OEIS match" wheelhouse, and a clean demo orbit.
- **Pairwise meets of antichains in ℤ^d** · [2606.08772](https://arxiv.org/abs/2606.08772) — meets = coordinatewise min on prime-exponent vectors; via factorisation gives **gcd lower bounds for sets supported on ≤ d primes**. This *is* calx's p-adic stratification lattice (ω/Ω, prime-exponent vectors) — a theorem calx could host and attest over its generated integers.

For kan/cert as a relational meta-layer, two SQL-native constraint languages are worth tracking as design references rather than drop-ins: **DeQL** ([2606.19751](https://arxiv.org/abs/2606.19751), SQL + `DECIDE`/constraints/objective) and **A-COMPASS** ([2606.20492](https://arxiv.org/abs/2606.20492), a compliance-assertion language with proved determinism/compositionality) — both echo Trunkit's "state it declaratively, check it in-DB" stance.

---

## 3. Recommended expansion shortlist (highest leverage first)

1. **Weighted Nerode** (①, supported by ⑧) — lift DFAs to weighted/event-history automata with exact extremal bounds + witnesses. Biggest capability jump, lands directly in existing product/witness machinery.
2. **Porter policy gate** (②, hardened by ④/⑥) — add pre-commit policy checks against the envelope; give certificates **validity windows + revocation**. Turns Porter from cacher into enforcer.
3. **Logical-compliance cert tier** (③, optionally probabilistic via ⑤) — a Horn-rule/soft-rule attestation computed in SQL; natural sixth method tier with the same three-valued honesty.
4. **LTLf front-end for monitors** (⑦) — declarative temporal specs that compile to today's DFA/cybernetic machinery.
5. **OEIS conjecture-attestation workflow in calx** (Tier 4) — ingest an OEIS conjecture, attest a closed-form/recurrence match as a first-class claim; ⑥/A396151 makes a tidy demo.

**Caveats.** (a) Several Tier-1/2 papers are very recent (June 2026) preprints — read the methods before committing schema. (b) The agent-security papers (④⑤⑥) frame Trunkit-shaped ideas in an *enforcement* context; adopting them means Trunkit takes on a security-boundary role, which raises the bar on the ledger's integrity guarantees (already a stated design goal in `SECURITY.md`). (c) calx's pure-math fits are about *content to attest*, not new mechanism — lower engineering cost, narrower payoff.
