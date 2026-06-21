# Quantitative Automata for Nerode — EHAs + WFFAs with Extremal Analysis

*Design note. Source: Vitaly Nürnberg, "Scenario Constraints with Memory: A Finite-State Approach to Quantitative Financial Analysis," [arXiv:2606.11223](https://arxiv.org/abs/2606.11223) (May 2026), building on Droste & Nürnberg, "Weighted automata and regular expressions for financial systems," [arXiv:2604.17370](https://arxiv.org/abs/2604.17370).*

Status: starter increment. Two runnable SQL files (`A0_interval.sql`, `A1_eha.sql`) land the foundation; `A2`–`A5` are specified here with signatures and algorithms. Nothing is wired into `db.py` yet, so the new files are inert until `SCHEMA_FILES` is updated (see §7).

---

## 1. Why this paper, and what it buys Nerode

Nerode today is a **Boolean** DFA engine: a word is accepted or not, equivalence is decided by symmetric-difference emptiness, and witnesses are distinguishing strings or bisimulations (`04_product.sql`). The cybernetic layer (`95_cybernetic_automata.sql`, `97_composite_dfa.sql`) fires DFAs over control/metric streams but can only say *a pattern occurred* — it cannot say *how bad* or *how good* an admissible trajectory can get.

The paper supplies exactly the missing quantitative half, in two finite-state pieces that map cleanly onto machinery Nerode already has:

| Paper construct | What it is | Nerode analogue today | Gap to close |
|---|---|---|---|
| **EHA** (event history automaton) | Deterministic **Moore machine** whose state output is an *admissible interval* `ρ: Q → I(D)` | DFA (`states`, `transitions`); `product()` already builds the synchronous product of pattern DFAs | states have no interval output; no pattern→EHA compile |
| **Pattern scenario constraint system** | `(E, J)` rules (regex pattern → interval) + a **resolver** (priority / intersection) | `from_regex()` builds the per-pattern DFA; `product()` tracks active patterns | no resolver step assigning the output interval |
| **WFFA** (weighted finite finance automaton) | Automaton over the **max-plus semiring** with *data-dependent* transition weights (local payoff expressions `[[e]]: D → S`) | none — transitions are unweighted | need a weight/payoff-expression column + evaluator |
| **Synchronized product** `[[W']] = [[W]] ∩ L(H)` (Thm 2) | restricts a WFFA to the EHA's admissible language by folding interval guards into transition weights | `product()` (Boolean intersection) | weighted variant carrying interval guards |
| **Extremal analysis** (Thm 3) | exact **best/worst-case payoff** in polynomial time over the product, + **witness history** | `equivalent()` already does BFS with a predecessor chain to reconstruct a witness string | max-plus longest/shortest-path DP instead of reachability; same backpointer trick |

The strategic win is the last row meeting `cert`: an extremal bound is a **claim**, and the witness history is a **`trace` witness** that a consumer re-verifies by replaying the path through the product automaton — no trust in the producer. That is the whole Trunkit thesis (proof travels with the result), now extended from "this DFA is minimal" to "this monitored system's worst-case severity over all admissible trajectories is ≤ B, and here is the trajectory that achieves it."

---

## 2. Scope of this increment

Land the foundation and prove the EHA half end-to-end on top of existing infrastructure:

- **A0 — interval algebra.** A first-class representation of `I(D)` (∅, `[a,b]`, `[a,∞)`, `D`) with intersection, membership, and guard rendering. Pure, `IMMUTABLE`, independently testable.
- **A1 — EHA layer.** A side table for state outputs (non-invasive: no change to `nerode.states`), plus `compile_eha()` that compiles a pattern-constraint system into an EHA by **reusing the existing `from_regex()` and `product()`** and then applying a resolver to set each product state's interval. Reuse is the point — the paper's Theorem 1(a) construction *is* "synchronous product of the pattern DFAs," which `product()` already does.

`A2`–`A5` (WFFA, weighted product, extremal DP, cert bridge) are fully specified in §5–§6 so the increment is a coherent slice of a known whole, not a dead end.

---

## 3. Data model

### 3.1 Interval (A0)

Represent `I(D)` over `D = ℝ≥0` as a composite, with rationals stored as `NUMERIC` to honor the paper's "rational endpoints encoded in binary" assumption (Thm 3) and keep extremal arithmetic exact:

```
nerode.interval := (
    lo      NUMERIC,     -- lower endpoint (NULL ⇒ −∞ side unused; D uses lo=0)
    hi      NUMERIC,     -- upper endpoint (NULL ⇒ +∞, i.e. [a,∞))
    is_empty BOOLEAN     -- TRUE ⇒ ∅ (lo/hi ignored)
)
```

Canonical forms: `∅` = `(_,_,TRUE)`; `D` = `(0, NULL, FALSE)`; `[a,b]` = `(a, b, FALSE)`; `[a,∞)` = `(a, NULL, FALSE)`. Functions: `iv_empty()`, `iv_full()`, `iv_closed(a,b)`, `iv_lower(a)`, `iv_meet(x,y)` (intersection — the intersection resolver of Example 4), `iv_contains(iv, d)` (membership), `iv_is_empty(iv)`.

### 3.2 EHA (A1)

An EHA is stored as an ordinary `nerode.automata` row (so it inherits `states`/`transitions`/`export_json`/`complete_dfa`) plus a per-state interval output kept in a side table:

```
nerode.eha_output (
    automaton_id  BIGINT  REFERENCES nerode.automata(id) ON DELETE CASCADE,
    state_id      INTEGER,
    interval      nerode.interval NOT NULL,
    PRIMARY KEY (automaton_id, state_id),
    FOREIGN KEY (automaton_id, state_id) REFERENCES nerode.states(automaton_id, state_id)
)
```

The `automata.type` CHECK currently allows `DFA/NFA/NFA_E/PDA`. A1 extends it idempotently to add `'EHA'` (and `'WFFA'` for A2) so model intent is queryable; the transition structure is still a complete DFA underneath. `is_accepting` is unused for EHAs (every state "accepts" with an interval), so A1 leaves it FALSE and relies on `eha_output`.

### 3.3 WFFA (A2 — specified)

Weighted transitions and the max-plus payoff live beside the Boolean transition relation rather than replacing it, so DFA tooling keeps working:

```
nerode.wffa_weight (
    transition_id BIGINT REFERENCES nerode.transitions(id) ON DELETE CASCADE PRIMARY KEY,
    payoff        JSONB NOT NULL      -- serialized local payoff expression, see §5
)
nerode.wffa_terminal (
    automaton_id BIGINT, state_id INTEGER,
    role TEXT CHECK (role IN ('initial','final')),
    weight NUMERIC NOT NULL DEFAULT 0,   -- wt_I / wt_F  (−∞ encoded as NULL)
    PRIMARY KEY (automaton_id, state_id, role)
)
```

---

## 4. EHA compilation (A1, the runnable half)

`nerode.compile_eha(p_system JSONB) → BIGINT` takes a constraint system and returns a new EHA automaton id. The input mirrors Definition 1:

```json
{
  "alphabet": "nerode_events",
  "default": {"lo": 99, "hi": 101},
  "resolver": "priority",            // or "intersection"
  "constraints": [                    // listed in priority order
    {"pattern": "Σ*·u·u·Σ*", "interval": {"lo": 180, "hi": 220}},
    {"pattern": "Σ*·d·d·Σ*", "interval": {"lo": 40,  "hi": 90}}
  ]
}
```

Algorithm (Theorem 1(a), implemented by reuse):

1. For each constraint *i*, `from_regex()` → deterministic pattern DFA `D_i` (final states = "pattern matched so far").
2. Fold the `D_i` with the existing `product()` under `'intersection'` semantics for the **state space** — we only need the reachable product of state-tuples, which `product()` already enumerates via BFS. (For the first cut we iterate `product()` pairwise; a dedicated *n*-ary product that records the per-component accepting bits is the obvious follow-up and avoids re-deriving "which patterns are active" from labels.)
3. For each product state, compute the **active set** `Act(u) = { i : component i is accepting }` from the component accepting bits, apply the resolver (`priority` → first active interval, else default; `intersection` → `iv_meet` of all active, else `D`), and write the result to `eha_output`.
4. Log to `construction_log` (`operation='compile_eha'`), stash the source system in `automata.provenance`.

Because step 2 leans on `product()`, the worst-case `O(N^m)` blow-up noted in the paper's Remark 1 is inherited honestly — fine for the small constraint counts the cybernetic use-case needs, and the paper's own scalability study (≤10 constraints) lives in the same regime.

`nerode.eha_interval(p_automaton_id, p_history TEXT) → nerode.interval` runs the unique path for an event history and returns `ρ(last(u))` — the EHA analogue of a membership query, used in tests and by the WFFA guard step.

---

## 5. WFFA payoff expressions (A2 — specified)

Local payoff expressions `e ∈ E_F` (Definition 3) over the linear finance semiring `F_lin = (S_max,+, ℝ≥0, b_lin)` with `b_lin(d,s) = d·s`. Serialize as a small JSONB AST evaluated by `nerode.payoff_eval(payoff JSONB, d NUMERIC) → NUMERIC` (returns NULL for the semiring zero −∞):

| AST node | Meaning | `[[e]](d)` |
|---|---|---|
| `{"const": s}` | semiring constant | `s` |
| `{"bind": s}` | `⟨⟨s⟩⟩`, data-binding | `d · s` |
| `{"oplus": [e1,e2]}` | `⊕` = max | `max([[e1]](d), [[e2]](d))` |
| `{"otimes":[e1,e2]}` | `⊗` = `+` | `[[e1]](d) + [[e2]](d)` (NULL if either NULL) |
| `{"guard": {"iv": I, "then": e}}` | interval guard | `[[e]](d)` if `d ∈ I` else NULL(−∞) |

This covers the case-study weights directly: the autocall payoff `100 + max(0.1·(d−100), 0)` is `otimes(const 100, oplus(otimes(bind 0.1, const −10), const 0))`, and `min(d,100)` uses the guard encoding from the paper's Example 2.9 reference. **Interval-completeness** (needed for Thm 2) is exactly the `guard` node: every `nerode.interval` renders to a guard, which `A0.iv_guard(iv)` produces.

`nerode.wffa_product(p_eha BIGINT, p_wffa BIGINT) → BIGINT` is the weighted twin of `product()`: same BFS state-tuple enumeration, but each product transition's payoff becomes `otimes(original_payoff, guard(ρ(target EHA state)))` — folding the admissible interval into the weight, which is the entire content of Theorem 2's proof.

---

## 6. Extremal analysis + cert integration (A4/A5 — specified)

### 6.1 Best/worst-case DP (Thm 3)

The scenario-restricted product over `S_max,+` is a weighted DAG once unrolled to the contract horizon (the paper synchronizes with a horizon WFFA `T_n`; Nerode's session DFAs already bound trace length). Best-case = **longest path**, worst-case (deterministic WFFA) = **shortest path**, both under `+`-accumulation. Two implementation routes, both in-DB:

- **Recursive-CTE DP** over `(state, step)` with `MAX`/`MIN` of `incoming_value + edge_weight(d*)`, where `d*` is the data value optimizing that transition's payoff within its guard interval (for `b_lin`, the optimum is an interval endpoint — exact, no sampling).
- A **backpointer column** records the maximizing predecessor + chosen `(symbol, d*)`, reconstructed exactly like `equivalent()`'s `v_pred` chain to emit the witness history.

This reuses two patterns already in the codebase: the predecessor-chain witness reconstruction (`04_product.sql`) and the spectral/attractor DP in `40_eigenform.sql`.

`nerode.extremal(p_product BIGINT, p_sense TEXT /* 'best'|'worst' */) → (value NUMERIC, witness JSONB)` where `witness = { kind: 'trace', history: [(a₁,d₁),…], value, sense }`.

### 6.2 Mint a carried certificate

Route the result straight through the existing `nerode.certify()` (`10_cert.sql`), which already writes `cert.claim` + `cert.certificate` + `cert.witness`:

```
PERFORM nerode.certify(
  p_automaton_id => p_product,
  p_operation    => 'extremal_' || p_sense,        -- new method string
  p_evidence     => jsonb_build_object('value', v_value, 'sense', p_sense),
  p_witness_kind => 'payoff_trace',                -- new witness kind
  p_witness_body => v_witness                       -- the (a,d) history + value
);
```

Consumer re-verification (`cert.verify`) replays the `payoff_trace`: walk the history through the product automaton, accumulate `payoff_eval` under `+`, check each `dᵢ ∈ ρ(stateᵢ)`, and confirm the total equals the claimed bound. This is a pure replay — safe for untrusted callers, no INSERT — matching `witness_carry` semantics. The only cert-side addition is teaching `cert.verify` the `payoff_trace` kind (one replay function); no schema change.

A worked, Trunkit-native example (more apt than the finance case study): the cybernetic `homeostasis_alarm` monitor weighted by *excursion magnitude*. Question answered: "over all admissible control traces of length ≤ n in which the metric stays within the EHA-declared band, what is the worst-case cumulative excursion, and which trace realizes it?" — a quantitative, witnessed strengthening of today's Boolean `pg_notify` alarm.

---

## 7. Build order, wiring, and tests

Phased, each phase independently testable:

1. **A0 interval** → unit tests: `iv_meet` truth table (∅/D/closed/lower combinations), `iv_contains` boundaries, guard round-trip. *(file delivered)*
2. **A1 EHA** → reproduce paper Example 5/6: compile the two-constraint priority system, assert `eha_interval('macro_shock·earnings_miss·…')` matches the paper's `[[S]](uᵢ)` sequence `[98,102] → [90,104] → [40,80]`. *(file delivered)*
3. **A2 WFFA + payoff_eval** → unit-test the AST against the paper's `[[e]]` examples (`d−100`, `max(d−100,0)`, autocall, `min(d,100)`).
4. **A3 wffa_product** → assert payoffs preserved on admissible words, −∞ on violating words (paper's `(F ∩ L)` definition).
5. **A4 extremal + A5 cert** → reproduce Table 2 (e.g. `S₁`, horizon 8 → best 126.0 / worst 56.0) and assert the minted `payoff_trace` re-verifies via a standalone replay.

**Wiring (do last, after review).** The loader uses an explicit tuple in `src/nerode/db.py`; until these filenames are appended, the files are dormant. Add, in dependency order, after `97_composite_dfa.sql`:

```python
    "A0_interval.sql",
    "A1_eha.sql",
    # "A2_wffa.sql", "A3_wffa_product.sql", "A4_extremal.sql", "A5_extremal_cert.sql",
```

(The cert side — teaching `cert.verify` the `payoff_trace` replay — is a separate one-function change in `src/calx/sql/`, mirroring the `witness_carry` verifier in `88_cert_witness_carry.sql`.)

**Caveats.** (a) The starter SQL is written against the repo's schema conventions but has **not** been executed against a live Postgres in this session — run phase 1–2 tests before trusting it. (b) `compile_eha` leans on pairwise `product()`; for >2 constraints an *n*-ary product that records per-component accepting bits is the right follow-up (avoids inferring active-sets from concatenated labels). (c) Keeping `NUMERIC` endpoints (not float) is deliberate: Thm 3's polynomial-time guarantee is stated for binary-encoded rationals, and exact endpoints keep the extremal DP sound.
