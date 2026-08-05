# Design: `comb` — finite combinatorial structures and their property probes

**Status:** design, not yet implemented.
**Implements:** gap **T2** of `docs/reports/Trunkit_Erdos_AI_Capability_Fit.md`
(finite combinatorial-object store), the second item on that report's build
queue and the largest remaining one.
**Depends on:** `cert.claim`/`method`/`certificate` (`40_cert.sql`),
`witness_carry` (`88`), the exact-domain shield (`94`), and the bound tier
(`105`) for the extremal half.
**Touches calx:** nothing. That is the point of the document.

## Why this is not a calx layer

calx's universe is ℤ[1..N] and its factorization lattice. Every table in it
keys on an integer: `factorizations(n, prime, exponent)`, `primes(p)`,
`orbits`, `sequence_membership` — all of them are `REFERENCES integers(n)` or
derived from something that is. A point configuration in the plane has no `n`.
A Kleitman set family has no `n`. Filing them under calx would mean either
inventing a meaningless foreign key or keeping a second, unrelated universe
inside a schema whose entire contract is "these are the integers, fully
factored, up to N".

There is also a concrete mechanical reason, not just a taxonomic one.
`trunkit reset` runs

```sql
DROP TABLE IF EXISTS calx.factorizations, calx.primes, calx.integers CASCADE
```

(`src/calx/cli.py`). Any table holding a foreign key into `calx.integers` is
cascade-dropped by a routine regeneration. calx is *generated* — it is rebuilt
from scratch whenever the limit changes. Combinatorial objects are *deposited*:
a counterexample someone carried in is not derivable from a sieve and must
survive `reset`. Those two lifecycles cannot share a schema.

**Hard rule for the implementation: `comb` declares no foreign key into
`calx`.** Where a ground set happens to be {1..n}, that is a coincidence of
labelling, stored as integers in `comb`'s own tables. A view may join the two
for convenience; a constraint may not.

## Why this is not a kan or nerode layer either

Both already contain things shaped like graphs, and neither is the right home.

`kan.object`/`kan.morphism` model finitely-presented categories: morphisms
compose, identities exist, and `kan.sync_category()` reflects real Postgres
foreign keys into that structure (`20_kan.sql`). An edge of a graph does not
compose with an adjacent edge to give a third edge. Storing edges as kan
morphisms would assert that every graph is a category, which is false and
would corrupt the one table the meta-layer reflects into.

nerode's automata are labelled digraphs, but the label alphabet and the
accept-state semantics are load-bearing there. A set system is not an
automaton.

There is a free consequence worth naming: because `kan.sync_category()`
reflects *any* Postgres schema, `comb` becomes a kan category automatically the
moment it exists, with its tables as objects and its foreign keys as
morphisms — no special-casing. The meta-layer gains the new domain for free
precisely *because* the domain is kept out of it.

## Part 1 — graphs, hypergraphs and set systems are one table trio

These three are the same object viewed three ways. A set system is a family of
subsets of a ground set; a hypergraph is a set system; a graph is the
2-uniform case. Modelling them as one incidence structure means one probe
language instead of three parallel ones.

```sql
CREATE SCHEMA IF NOT EXISTS comb;

CREATE TABLE comb.structure (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_id   TEXT NOT NULL UNIQUE,   -- stable external name, e.g. 'erdos-107-alphaevolve'
    kind         TEXT NOT NULL CHECK (kind IN
                     ('graph','digraph','hypergraph','set_system')),
    ground_n     INTEGER NOT NULL CHECK (ground_n >= 0),
    -- Optional canonical-form digest from an EXTERNAL canonicaliser. A hint for
    -- deduplication, never evidence -- see the isomorphism note below.
    canon_digest TEXT,
    canon_tool   TEXT,
    source       TEXT NOT NULL DEFAULT '',
    provenance   JSONB NOT NULL DEFAULT '{}'::jsonb,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The ground set. `idx` is canonical WITHIN one structure and meaningless
-- across two -- no probe may ever compare idx between structures.
CREATE TABLE comb.element (
    structure_id BIGINT NOT NULL REFERENCES comb.structure(id) ON DELETE CASCADE,
    idx          INTEGER NOT NULL CHECK (idx >= 0),
    label        TEXT,
    PRIMARY KEY (structure_id, idx)
);

-- Edges / hyperedges / member sets.
CREATE TABLE comb.block (
    structure_id BIGINT NOT NULL REFERENCES comb.structure(id) ON DELETE CASCADE,
    idx          INTEGER NOT NULL CHECK (idx >= 0),
    label        TEXT,
    PRIMARY KEY (structure_id, idx)
);

CREATE TABLE comb.incidence (
    structure_id BIGINT NOT NULL,
    block_idx    INTEGER NOT NULL,
    element_idx  INTEGER NOT NULL,
    -- NULL for unordered blocks. For a digraph arc: 0 = tail, 1 = head.
    position     SMALLINT,
    PRIMARY KEY (structure_id, block_idx, element_idx),
    FOREIGN KEY (structure_id, block_idx)   REFERENCES comb.block(structure_id, idx)   ON DELETE CASCADE,
    FOREIGN KEY (structure_id, element_idx) REFERENCES comb.element(structure_id, idx) ON DELETE CASCADE
);
```

Graph probes should not have to write the 2-uniform join by hand every time, so
the surface they use is a view, not a second copy of the data:

```sql
CREATE VIEW comb.edge AS
SELECT structure_id, block_idx,
       MIN(element_idx) AS u, MAX(element_idx) AS v
  FROM comb.incidence
 GROUP BY structure_id, block_idx
HAVING COUNT(*) = 2;
```

Cost of this generality, stated plainly: a simple graph costs two `incidence`
rows per edge rather than one row in a `(u, v)` table. For the object sizes
actually in play — AlphaEvolve constructions, Kleitman families, unit-distance
configurations are all small — that is not a consideration. If some future
corpus makes it one, the fix is a materialised `comb.edge`, not a redesign.

## Part 2 — point configurations, and the coordinate problem

Geometry is separate because coordinates are separate. This is the half where
the design can quietly go wrong, so it gets the most space.

**The trap.** OpenAI's disproof of the planar unit-distance conjecture builds
its configuration through algebraic number theory. Store those coordinates as
`double precision` and "these two points are at distance exactly 1" becomes
uncheckable — the whole content of the object is an exact incidence pattern
that floating point erases. A store that loses it is not hosting the
counterexample, only a picture of it.

**The simplification that makes this tractable.** Work with *squared*
distances. A unit-distance condition is |p − q|² = 1, which is a polynomial in
the coordinates: no square root is ever taken, so the metric introduces no
irrationality of its own. Whatever field the coordinates live in, the distance
test stays inside that field. This is what makes exact geometry in SQL a
bounded problem rather than a symbolic-algebra project.

So coordinates live in an explicitly named field, and a coordinate is a
rational vector in that field's power basis:

```sql
-- ℚ(α) for a pinned minimal polynomial of α. Rational configurations use no
-- row here at all (degree 1, basis {1}).
CREATE TABLE comb.number_field (
    id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,       -- 'Q(sqrt3)', 'Q(zeta5)'
    -- Minimal polynomial of α, exact integer coefficients, ascending degree.
    min_poly NUMERIC[] NOT NULL,
    degree   INTEGER NOT NULL CHECK (degree >= 1)
);

CREATE TABLE comb.configuration (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_id   TEXT NOT NULL UNIQUE,
    dim          INTEGER NOT NULL CHECK (dim >= 1),
    coord_domain TEXT NOT NULL CHECK (coord_domain IN ('rational','algebraic')),
    field_id     BIGINT REFERENCES comb.number_field(id),
    source       TEXT NOT NULL DEFAULT '',
    provenance   JSONB NOT NULL DEFAULT '{}'::jsonb,
    CHECK ((coord_domain = 'rational') = (field_id IS NULL))
);

CREATE TABLE comb.point (
    configuration_id BIGINT NOT NULL REFERENCES comb.configuration(id) ON DELETE CASCADE,
    idx              INTEGER NOT NULL CHECK (idx >= 0),
    label            TEXT,
    PRIMARY KEY (configuration_id, idx)
);

-- One coordinate = num[]/den, num being coefficients over the power basis
-- 1, α, …, α^(d-1). NUMERIC, never float8 -- same stance as cert.bound (105)
-- and seq_vector.
CREATE TABLE comb.coordinate (
    configuration_id BIGINT NOT NULL,
    point_idx        INTEGER NOT NULL,
    axis             SMALLINT NOT NULL CHECK (axis >= 0),
    num              NUMERIC[] NOT NULL,
    den              NUMERIC NOT NULL CHECK (den > 0),
    PRIMARY KEY (configuration_id, point_idx, axis),
    FOREIGN KEY (configuration_id, point_idx)
        REFERENCES comb.point(configuration_id, idx) ON DELETE CASCADE
);
```

`comb.sq_distance(config, i, j)` returns an exact field element. Over ℚ that is
plain `NUMERIC` arithmetic. Over ℚ(α) it is multiplication of two power-basis
vectors followed by reduction mod `min_poly` — the one genuinely new arithmetic
kernel this design needs. It gets the treatment every other kernel in the repo
gets: a Python mirror (`src/calx/numberfield.py`, in the trunkit package
alongside `bound.py` and `congruence.py`) plus an SQL-equivalence test
comparing both over a shared population, exactly as `test_bound.py` does for
the bound order.

**The float rule is already built.** Step 94 tags claims with a numeric domain
and structurally downgrades a `float_heuristic` claim's `valid` to
`unverified`. A rational configuration registers `rational`, an algebraic one
`algebraic`, and a configuration carried in with float coordinates registers
`float_heuristic` — which cannot record a valid certificate no matter what its
probe returns. No new shield, no new rule, no new discipline to remember.

## Part 3 — what is checkable, and what only looks checkable

This is the section that decides whether the layer is honest. Combinatorial
claims split cleanly by quantifier, and the split is not cosmetic — it is the
difference between a probe that terminates and one that is a research project.

| Claim shape | Example | Witness | Probe | Best reachable verdict |
|---|---|---|---|---|
| The object *has* property P, P checkable per-element or per-pair | triangle-free; 3-uniform; primitive family; this drawing is unit-distance | none needed | polynomial scan | **valid** |
| ∃ a substructure with P | this 5-colouring is proper; this 40-set is independent; this bijection is an isomorphism | the substructure | polynomial check | **valid** |
| ∄ any substructure with P | χ ≥ k; no independent set of size m; this graph is *not* unit-distance | — | exhaustive | **unverified**, unless the search space was provably covered and the budget recorded |
| The extremal value is exactly m | AlphaEvolve #650 | construction + theorem | split in two | goes to the **bound tier**, see Part 4 |

Two consequences that must be written into the code, not just the doc:

**Refuting a witness is not refuting the statement.** A supplied 5-colouring
with a monochromatic edge refutes *that colouring*. It says nothing about χ.
The probe for "colouring W properly colours G" may return `ok = false`; the
probe for "χ(G) ≤ 5" may not conclude anything from it. These are two claims
and must be two rows, or the ledger will report a producer's bad guess as a
mathematical falsehood.

**A bounded search that finds nothing is `unverified`, never `refuted`.** Same
rule the stratified-cert design states for BFS reachability, and the same rule
`cert.bound_optimal` already follows for "nothing tighter is on record". A
search that *did* provably cover its space is a different thing and may reach
valid — but only if the covering argument is recorded as data (the enumerated
space, the budget, the exhaustion flag), not asserted in a comment.

**Isomorphism is not decided here.** Graph isomorphism has no known
polynomial algorithm and no in-SQL implementation is going to change that. So:
`comb` checks a *supplied* bijection (polynomial, a witness like any other),
and `canon_digest` is filled by an external canonicaliser with the tool
recorded in `canon_tool`. Equal digests are a deduplication hint. They are
never sufficient for a valid verdict on their own, because that would be
trusting the canonicaliser silently — and if that trust is wanted, the honest
route already exists: the canonicaliser becomes a tool-attested fact under the
attestation tier (`104`).

## Part 4 — how a construction becomes a certificate, and meets the bound tier

A construction alone is not a claim. The wiring is the existing one: the object
lives in `comb`, and a `witness_carry` claim (`88`) carries it, with a
`comp_sql` probe over the tables above re-checking the property. A consumer
re-runs the probe against the deposited object and trusts no one.

The payoff is where this meets step 105. An extremal result is a conjunction:
*there is an object of size m* (existential, witness-checkable, exactly what
`comb` hosts) and *nothing larger exists* (universal, a theorem, which stays a
formal or attested claim). The bound tier already models precisely that split —
and `cert.bound.attained_by` is currently free-form JSONB.

So an AlphaEvolve-style result lands as three rows that already know how to
talk to each other:

1. a `comb.structure` holding the construction;
2. a `cert.bound` row for the achievable side, with
   `attained_by = {"structure": "<subject_id>"}` — turning an unstructured note
   into a pointer at a re-checkable object;
3. a `cert.bound` row for the matching upper bound, whose truth is a
   `formal_external` or attested claim.

`cert.bound_optimal` then reaches `valid` only when the construction's own
claim stands *and* nothing on record dominates the bound — which is the correct
reading of "optimal construction found numerically", and is currently
impossible to state because there is nowhere to put the construction. T2 is
what makes T3's `attained_by` mean something.

## What we explicitly do not build

- **No SAT/ILP/CP solver in the core.** Same line the arXiv review draws for ZK
  provers: an external solver may *produce* a witness; `comb` *checks* it. The
  psycopg-only promise holds.
- **No canonical-labelling implementation.** nauty and friends stay outside,
  digest-only, per Part 3.
- **No floating-point coordinates reaching a valid verdict.** Reuse `94`; do
  not add a second shield.
- **No infinite or asymptotic families.** `comb` stores finite objects. "The
  planar unit-distance conjecture is false" is not a `comb` claim; "this
  configuration of 47 points realises this graph with all edges at distance
  exactly 1" is, and it is what the disproof actually deposits.
- **No new probe language.** Property probes are ordinary SQL functions
  returning `(ok, evidence)` or `(ok, evidence, witness)`, dispatched by the
  existing `cert.check` / `cert.check_with_witness`.
- **No foreign key into calx.** Repeated because it is the constraint most
  likely to be violated by someone being helpful.

## Sizing and suggested order

1. **`110_comb_structure.sql`** — schema, the incidence trio, the `comb.edge`
   view, register functions. Scale of `01_schema.sql`. Tests in the pattern of
   `test_schema.py`.
2. **`111_comb_probes.sql`** — the elementwise and existential probes:
   uniformity, degree sequence, triangle-freeness, primitive family, proper
   colouring, independent set, supplied isomorphism. Each returns
   `(ok, evidence)`; each names what it examined in its evidence. This is the
   layer that makes the Kleitman cluster (#447, #487, #497, #498, #505, #1023)
   hostable.
3. **`112_comb_geometry.sql`** + `src/calx/numberfield.py` — configurations,
   exact squared distance over ℚ and ℚ(α), the unit-distance probe, and the
   SQL↔Python equivalence test. Biggest single piece; the unit-distance work
   is the reason it exists.
4. **`113_comb_witness.sql`** — `witness_carry` wiring and the
   `attained_by → comb.structure` bridge to the bound tier (Part 4).
5. **Search-budget records** for the universal side — last, and only when a
   real claim needs one. Do not build the exhaustion bookkeeping speculatively.

Pieces 1–2 are independently useful and unlock the set-system half without any
of the geometry. If the effort has to stop somewhere, stop after 2.

## Threat and correctness notes

- **Every probe must be bounded.** `cert.check` runs probe SQL inline; an
  exhaustive search with no ceiling hangs the ledger for every caller. Same
  requirement `max_steps` carries in the stratified-cert design and the
  recurrence tier.
- **Indices are structure-local.** `comb.element.idx` means nothing outside its
  own `structure_id`; a probe that joins on `idx` alone across two structures
  is a bug that will silently produce plausible answers.
- **A witness is producer data.** A supplied colouring, bijection or subset is
  untrusted input. Checking it is the point; treating its failure as a
  statement about the underlying mathematics is the error Part 3 exists to
  prevent.
- **Deposited objects must survive regeneration.** The no-FK-into-calx rule is
  what enforces this. A `comb` object should be droppable only by an explicit
  `comb` operation, never as collateral from `trunkit reset`.
