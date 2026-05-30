# Cross-Lab Shared Abstractions → a candidate common Trunkit schema

Third pass: comparing **hypergroup_research_program**, **interlace**, and the
**TEL core** (telc + construction sheaves + telVOS) to find the abstractions they
*share*, and whether they're worth a new Trunkit schema. Drafted 2026-05-29, local.

The one-line of each:
- **hypergroup** — three-type classification; duality-depth tower; Haar residual.
- **interlace** — operator graphs / closure / diameter; order lattices; self-reference.
- **TEL core** — construction sheaves; cubical coherence; construction-dependence hierarchy.

---

## 1. Five shared abstractions

| # | Abstraction | hypergroup | interlace | TEL core |
|---|---|---|---|---|
| **A. Graded site** (poset + presheaf + gluing) | hyperposet + CD-depth presheaf; sheaf-glue at meets | dominance / divisor / Scott lattices; bootlace dependency DAG | construction poset; `construction_sheaf`; cubical coherence |
| **B. Iterated tower + stabilization depth** (three-valued) | duality tower `Dⁿ`; depth 0/1/**∞** | operator closure / BFS levels; Y-combinator fixpoint; busy-beaver tower | HIR fold→normal-form; coherence levels; `kan_self` endofunctor iteration |
| **C. Frontier residual** (zero on classified stratum, positive/undefined on frontier) | Haar residual ‖Aμ‖₂ (=0 Type I/II, >0 III); positivity defect | graph diameter; unreachable-state count; closure defect | construction-dependence defect; Feigenbaum convergence residual (under-converged = positive!) |
| **D. Classification trichotomy** (third class = honestly *open*, not failed) | I / II / **III (no Haar, depth ∞)** | proved / disproved / **open-or-uncomputable** | invariant / construction-dependent / **undefined** |
| **E. Base-change stability** (which invariants survive a completion/functor) | survives ℝ→ℂ→ℍ→𝕆→𝕊? p-adic completion of `c(n,m;k)` | bit-width change; mod-n across n; HxTT shape independence | ℂ vs ℚ_p (ghost attractor); construction-dependence levels |

**The synthesis (one sentence):** all three are instances of *a stratified site
carrying an iterated endofunctor whose stabilization depth is a three-valued
invariant, with a frontier residual measuring distance to the classified stratum,
and a base-change functor asking which of these survive.*

Crucially, **(D) is exactly the verification trichotomy we hardened this week**
(`cert.standing` LEFT JOIN; `kan_engines` empty≠refuted; equip-for-verification).
The labs' mathematics and Trunkit's verdict logic are *the same shape*.

---

## 2. What's already covered vs. missing in Trunkit

| Abstraction | Covered by | Gap |
|---|---|---|
| A. Graded site | `kan` (categories, sheaves) | the labs' *specific posets* aren't first-class data — each is ad hoc |
| B. Tower + depth | `kan_self` (endofunctor iter); `curry` close/eigenform | no general "iterate-to-stabilization-depth" ledger with the ∞/undefined case |
| C. Residual | `cert` evidence jsonb | residuals aren't modeled as structure (zero-on-stratum-A, positive-on-B) |
| D. Trichotomy | **`cert` (valid/refuted/unverified)** — done this week | — (this is the win to generalize *from*) |
| E. Base change | `calx` (p-adic), `kan` (functors) | no "survives-functor?" comparison primitive |

So A, B, C, E recur in **all three labs** but live nowhere uniformly. That is the
case for a thin new schema sitting between `kan` (structure) and `cert` (verdict).

---

## 3. Proposed schema: `strat` (stratification engine) — DDL sketch

```sql
-- A poset/site: hyperposet | lattice | construction | dag | domain
CREATE TABLE strat.site   (id serial PK, name text UNIQUE, kind text, meta jsonb);
CREATE TABLE strat.node   (site_id int, node_id text, label text, payload jsonb,
                           PRIMARY KEY (site_id, node_id));
CREATE TABLE strat.cover  (site_id int, lo text, hi text);   -- order/Hasse edges

-- An iterated endofunctor over a site and its stabilization depth (three-valued).
CREATE TABLE strat.tower  (id serial PK, site_id int, endofunctor text,
                           max_depth int,
                           stab_depth int,         -- NULL  ==  ∞ / never stabilises  (==> cert unverified)
                           orbit jsonb,            -- the Dⁿ trace
                           meta jsonb);

-- A frontier residual: 0 on the classified stratum, >0 on the frontier, NULL undefined.
CREATE TABLE strat.residual (id serial PK, site_id int, object text,
                             metric text, value double precision,  -- NULL == undefined
                             classified_zero boolean,              -- is 0 the "nice" case?
                             meta jsonb);

-- A base-change functor between sites + which residuals it preserves.
CREATE TABLE strat.basechange (id serial PK, src int, dst int, functor text,
                               preserves jsonb);  -- {metric: survives?}
```

**Integration (no new verdict logic — reuse what we hardened):**
- Every `strat.tower` and `strat.residual` is the *subject* of a `cert.claim`.
  The verdict follows the **same three-valued rule**: `stab_depth IS NULL` or
  `residual.value IS NULL` → **`unverified`** (not refuted); `value ≈ 0 on a
  classified-zero metric` → **valid**; genuine positive defect where zero was
  required → **refuted**. This is literally the `cert.law_view_holds` /
  `kan_engines_all_true` pattern generalized.
- `strat.site` registers as a `kan` category; `strat.basechange` as a `kan` functor.
- Numeric payloads (p-adic structure constants, mod-n tables) live in `calx`.
- Endofunctor orbits + their purity memoize in `curry`.

---

## 4. Each lab as an instance (concrete rows)

| | site | tower (stab_depth) | residual (classified_zero) |
|---|---|---|---|
| **hypergroup** | hyperposet | duality `D` (0 / 1 / **NULL** for III) | Haar ‖Aμ‖₂ (zero=Type I/II) |
| **interlace** | dominance lattice / operator reachability | operator closure / BFS (closure depth; **NULL** if non-terminating) | diameter / unreachable count (zero=closed) |
| **TEL core** | construction poset | HIR fold→NF; coherence (`kan_self`) | construction-dependence defect (zero=invariant, e.g. Feigenbaum within a class) |

The Feigenbaum correction from earlier today is a `strat.residual` story:
"δ_sine = 4.541" was a **non-zero residual read as a real difference** — but the
residual was just *under-convergence* (the tower hadn't stabilized). Distinguishing
"residual > 0 because genuinely different" from "residual > 0 because not yet
converged" is exactly what `strat.tower.stab_depth` + `strat.residual` together
encode. The schema would have *caught* that error.

---

## 5. Recommendation

**Worth it — but as a thin layer, not a heavy engine.** A, B, C, E genuinely recur
in all three labs and in TEL, and modeling them once removes the per-lab ad-hockery.
But:
- Start as **views + a handful of helper functions** over `kan`/`cert`/`calx`, not
  a big new pillar. `strat.tower` is a generalization of `kan_self`; `strat.residual`
  is structured `cert` evidence. Prove the abstraction earns its keep on **two**
  labs before schema-fying.
- The **verdict layer must not be reinvented** — `strat` produces subjects, `cert`
  judges them, under the three-valued rule already hardened this week.
- Highest-value first instance: **duality-depth (hypergroup) + operator-closure
  (interlace)** share `strat.tower` almost exactly — implement that one tower
  primitive, attest both, and see if the residual/site tables pull their weight.

**The deep finding of this pass:** the three labs aren't three problems — they're
three *coordinate charts* on one object: a stratified site with a depth tower and a
frontier residual, judged by a three-valued verdict. Trunkit already grew the
verdict layer (this week) and the functor layer (`kan`); `strat` would be the
missing **stratification layer** that makes "classified vs frontier" first-class —
and TEL is the language in which that object's *constructions* are written.

---

## 6. Validation (2026-05-29) — `strat` built and proven on two labs

The thin layer was implemented as `src/calx/sql/92_strat.sql` (idempotent) and
exercised on exactly the two labs predicted to share `strat.tower`:

| instance | `strat.tower` | residual | cert verdict |
|---|---|---|---|
| interlace `{add1, 2-way Morton}` 8-bit | `stab_depth = 61` (self-recomputed BFS) | `unreachable_states = 0` (classified) | **242 valid** |
| hypergroup duality (Jacobi, FlatType5) | `stab_depth = 0` (flagged stub) | FlatType5 Haar `= 0.2427` (frontier) | **243 valid · 244 unverified · 245 valid** |

**Verdict on the abstraction:**
- **`tower` earned its keep** — one table + one detector (`strat.tower_depth`,
  `saturate`/`return` modes) expresses both interlace closure-depth and hypergroup
  duality-depth, three-valued.
- **`residual` earned its keep** — holds both the zero (classified) and positive
  (frontier) cases; `classified_zero` distinguishes them.
- **`site` is thin glue** — useful as the join anchor, lightest of the three.
- The verdict was **not** reinvented: all four claims went through `cert` under the
  three-valued rule (valid / unverified), as designed.

### Finding surfaced on first contact: the `_dualize` identity stub

Running the *real* hypergroup code (numpy) to build the duality towers returned
`duality_depth = 0` with **identical orbits at every level** for both Jacobi
(Type I) and FlatType5 (Type III) — because `core/duality.py::_dualize` is an
**identity stub** (it relabels the input rather than dualizing). Consequences:

- The program's headline invariant (**duality depth**) is **not actually computed**;
  the README's *Type I depth = 1* and *Type III depth = ∞* are **aspirational**.
- `strat` recorded this honestly: claim **243 valid** documents the stub; claim
  **244 unverified** marks the Type III frontier as *undeterminable while
  dualization is unimplemented* (the checker doesn't really exist) — not `refuted`.

This is the **third instance this session of the claims-outrun-implementation
pattern**, after the Trunkit `cert.standing` INNER-JOIN bug and the TEL Feigenbaum
construction-dependence error. In all three, the honest-null discipline (treat
"unknown / unbuilt" as `unverified`, never `refuted`) is what keeps the ledger
truthful — and `strat` inherited it for free by routing every subject through `cert`.

**Next, if pursued:** implement a real `_dualize` (character-table / Pontryagin)
in the hypergroup repo; then `strat.tower` recomputes genuine depths (Jacobi → 1,
FlatType5 → NULL) and claims 243/244 flip accordingly — the ledger tracks the fix.
