# Porter policy gate — a proof-carrying LedgerAgent on Postgres

*Design note. Primary source: Md Nayem Uddin et al., "LedgerAgent: Structured State for Policy-Adherent Tool-Calling Agents," [arXiv:2606.20529](https://arxiv.org/abs/2606.20529). Hardening borrows from "Sovereign Execution Brokers" ([2606.20520](https://arxiv.org/abs/2606.20520), validity windows + live-state drift) and "ContractGuard" ([2606.18550](https://arxiv.org/abs/2606.18550), the gate is only as honest as its inputs ⇒ pin them in the witness).*

Status: starter increment. Four runnable SQL files (`B0`–`B3`) plus a Python `Warden` API. Nothing is wired into `db.py` until §6, so the files are inert until then.

---

## 1. Why this fits Porter exactly

LedgerAgent (Algorithm 1) has two deterministic components bolted onto an agent loop:

1. **A schema-anchored ledger.** Successful tool returns are projected into a compact *typed dictionary keyed by canonical paths*, re-injected each turn so the agent reads current state by lookup instead of re-scanning the transcript. `Absorb(L, m)` updates it; `Render(L)` emits it.
2. **A policy gate.** *Before* an environment-changing tool call, `GateFilter(a, L, Π)` evaluates the proposed call against domain rules expressed as **predicates over ledger fields**, returning `ALLOW` / `REVISE` / `BLOCK` with feedback naming the violated rule and the conflicting state.

Porter already has the hard half of this and none of the gate:

| LedgerAgent piece | Porter today | Gap |
|---|---|---|
| schema-anchored ledger (typed dict, canonical paths) | `sequence_cache` + `session_cache_tags` — pre-packed reads keyed by string keys, resolved into `open_session().resolved{}` | not *typed*, not an `Absorb` step over live tool-returns |
| `Render(L)` into the prompt | `open_session` already inlines `resolved{}` with zero tool calls | rename/shape into a typed render |
| policy predicates Π | — | none |
| `GateFilter` before env-changing calls | — | none — Porter packs context but never *gates an action* |
| feedback naming violated rule + state | — | none |

The synthesis: Porter is the model-to-model context carrier; the gate turns it into a **pre-commit boundary** — the next model's environment-changing call is checked against the carried, cert-verified ledger *before* it fires. And because Porter lives on the cert ledger, the gate decision is itself proof-carrying: the witness pins the ledger snapshot and the predicates, so a consumer re-derives `ALLOW`/`BLOCK` without trusting the producer. That is ContractGuard's "the gate is only as honest as its contracts" answered with Trunkit's own witness discipline.

The three gate verdicts land naturally on Trunkit's three-valued honesty:

```
ALLOW  → valid        (every governing predicate satisfied over current state)
BLOCK  → refuted      (a hard predicate is violated by the ledger)
REVISE → unverified   (a soft violation, or required state missing — fetch/clarify)
```

`REVISE-on-missing-state` is the empty-engine guard again: absent facts must not manufacture a `BLOCK`, exactly as an empty automaton must not manufacture a `refuted` (AUDIT §3).

---

## 2. Data model

### B0 — ledger state (the `Absorb`/`Render` store)

```
nerode.ledger_state (
    session_id   TEXT,
    path         TEXT,            -- canonical path, e.g. 'reservation.UX789'
    value        JSONB NOT NULL,  -- the typed value projected from a tool return
    schema_type  TEXT,            -- optional declared type tag ('reservation', 'order', …)
    source_event BIGINT,          -- nerode.session_log.id that produced it (provenance)
    observed_at  TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (session_id, path)
)
```

`ledger_absorb(session, path, value, type, source_event)` upserts (latest successful read wins — the paper's "successful known reads update typed state"). `ledger_render(session) → JSONB` returns `{ path : value }` (the compact dict re-injected at each turn). `ledger_get(session, path) → JSONB` is the single lookup the gate and the model use instead of re-reading the transcript.

### B1 — policy registry (the predicates Π)

```
nerode.policy_rule (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name          TEXT UNIQUE,
    tool          TEXT NOT NULL,        -- the environment-changing tool this gates
    predicate_sql TEXT NOT NULL,        -- ALLOW condition: boolean over $1=ledger, $2=args
    message       TEXT NOT NULL,        -- policy-grounded feedback when violated
    effect        TEXT NOT NULL DEFAULT 'block' CHECK (effect IN ('block','revise')),
    enabled       BOOLEAN NOT NULL DEFAULT TRUE
)
nerode.gated_tool (tool TEXT PRIMARY KEY)   -- registry of environment-changing tools
```

A rule's `predicate_sql` is an **allow-predicate**: a SQL boolean expression that is `TRUE` iff the action is *permitted*, written over two bound parameters — `$1` = the rendered ledger JSONB, `$2` = the call args JSONB. Whitelist semantics; violation = `NOT allow`. It is evaluated with parameters *bound* (`EXECUTE … USING v_ledger, v_args`), never string-interpolated, so attacker-controlled arg values cannot inject SQL — the same discipline as the stored `probe_sql` in `close_session`. Example (the paper's flight-cancel policy):

```sql
-- rule 'cancel_within_policy' on tool 'cancel_reservation'
(  ($2->>'reservation_id') = ($1->'reservation'->>'id')
   AND (
        ($1->'reservation'->>'hours_since_booking')::numeric <= 24
     OR ($1->'reservation'->>'airline_cancelled')::boolean
     OR ($1->'reservation'->>'insurance_covered')::boolean
   )
)
```

This is a re-runnable SQL probe over current state — structurally the same object as a kan invariant or a cert `comp_sql` probe.

---

## 3. The gate (`GateFilter`, B2)

`nerode.policy_gate(session, tool, args) → (verdict, feedback, witness)`:

1. `L := ledger_render(session)`.
2. For each enabled `policy_rule` bound to `tool`, evaluate the allow-predicate against `(L, args)` → `TRUE` (satisfied) / `FALSE` (violation) / `NULL` (required state missing).
3. Resolve the verdict:
   - any `FALSE` with `effect='block'` → **BLOCK**;
   - else any `FALSE` with `effect='revise'`, or any `NULL` → **REVISE**;
   - else (all `TRUE`) → **ALLOW**. A tool with no rules and not registered as environment-changing is ALLOW by default (reads pass; the gate only governs `gated_tool`s).
4. `feedback` concatenates each violated/indeterminate rule's `message` with the conflicting ledger values — policy-grounded, like the paper's "outside the 24-hour cancellation window."
5. `witness` pins everything needed to re-derive the verdict: the ledger snapshot, the args, the per-rule results, and `ledger_hash = md5(L)`.

The verdict maps to `GateFilter`'s `(a', g)`: `ALLOW` keeps the call; `REVISE` removes the rejected call and returns feedback; `BLOCK` refuses.

---

## 4. Proof-carrying gate decisions + SEB hardening (B3)

`certify_gate(session, tool, args) → claim_id` records the decision as a `cert.claim` + `certificate` + `witness` (mirroring `close_session`), with:

- **status** = the three-valued map (`ALLOW→valid`, `BLOCK→refuted`, `REVISE→unverified`);
- **witness** (`kind='gate_decision'`) = the pinned ledger snapshot + args + per-rule results;
- **probe_sql** = `SELECT nerode.replay_gate(<witness>)` — so a consumer re-evaluates the *same predicates against the pinned snapshot* and must reach the same verdict, with no trust in the producer (ContractGuard).

Two SEB borrows make the decision a *short-lived, revocable* capability rather than a forever-true fact:

- **Live-state drift.** `gate_drift(session, witness)` recomputes `md5(ledger_render(session))` and compares it to the witness's `ledger_hash`. An `ALLOW` is only valid against the snapshot it was made on; if the ledger has changed, the decision is stale and the action must be re-gated before it fires. This is SEB's "live-state drift" check.
- **Validity window.** The certificate's `valid_under` carries `decided_at` and the `ledger_hash`; a caller can attach a TTL/epoch and treat the ALLOW as expired past it.

---

## 5. Python surface — `nerode.warden.Warden`

Mirrors `Precacher`'s style so Porter users get one idiom:

```python
from nerode.warden import Warden

w = Warden("sess-julia")
w.absorb("reservation.UX789", reservation_json, schema_type="reservation")  # Absorb
ctx = w.render()                                                            # Render → prompt
decision = w.gate("cancel_reservation", {"reservation_id": "UX789"})        # GateFilter
# decision.verdict ∈ {ALLOW, REVISE, BLOCK}; decision.feedback; decision.claim_id
if decision.verdict != "ALLOW":
    # do not call the tool; surface decision.feedback to the model
    ...
```

`absorb` calls `ledger_absorb`, `render` calls `ledger_render`, `gate` calls `certify_gate` and returns the verdict + feedback + claim id. A Porter envelope can carry the ledger forward so Model B opens an already-populated, cert-verified ledger and gates its first action with zero tool calls — the Porter handoff and the LedgerAgent gate composed.

---

## 6. Build order, tests, wiring

1. **B0 ledger** → absorb two reads, assert `ledger_render` shape and `ledger_get`.
2. **B1 registry** → register the flight-cancel rule + gated tool.
3. **B2 gate** → reproduce Figure 1: ledger says booking is 48h old ⇒ `cancel_reservation` → **BLOCK** with the 24-hour message; within 24h or insured ⇒ **ALLOW**; missing reservation ⇒ **REVISE**.
4. **B3 cert** → `certify_gate` records the decision; `replay_gate` re-verifies it from the witness; `gate_drift` flips to TRUE after a new `absorb`.

**Wiring (last).** Append to `SCHEMA_FILES` in `src/nerode/db.py`, after the Phase-4 block, in dependency order:

```python
    "B0_ledger.sql",            # Phase 5a — schema-anchored ledger (Absorb/Render)
    "B1_policy.sql",            # Phase 5b — policy predicate registry (Π)
    "B2_gate.sql",              # Phase 5c — policy_gate (GateFilter: ALLOW/REVISE/BLOCK)
    "B3_gate_cert.sql",         # Phase 5d — proof-carrying decisions + replay + drift
```

**Caveats.** (a) Starter SQL, written to repo conventions, not yet run against a live Postgres. (b) Predicates are bound-parameter SQL (`$1` ledger, `$2` args) — never interpolate arg values. (c) The gate governs only registered `gated_tool`s; everything else passes (reads must not be gated). (d) `replay_gate` re-checks against the *pinned* snapshot (deterministic, the cert sense); `gate_drift` checks the *live* ledger (the SEB sense) — they answer different questions and both are needed.
