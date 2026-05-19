# Trunkit — Skill Guide

> Connection: `TRUNK_DSN=postgresql://trunk:trunk@localhost:5434/trunk`
> Default `search_path`: `calx, curry, kan, public` (cert is fully schema-qualified)

---

## CLI

```
trunkit <command> [--dsn DSN]

Consumer commands (read-only — safe for LLM use):
  verify <claim_id>                      side-effect-free re-verification
  standing [--method M] [--status S]     latest attestation per claim
  export <id> [<id> ...]                 portable JSONB proof bundle to stdout

Prover commands (dry-run without --write):
  check <claim_id> [--write]             attest a claim, append certificate
  attest [--write]                       run formal-tier artifact attestation
  witness <claim_id> --kind K --body J [--write]   attach proof witness

calx data commands:
  init                                   apply schema/views/procedures
  generate --limit N [--backend ...]     populate integer tables
  validate [--limit N]                   check derived columns
  reset                                  drop calx tables
  oeis-load / oeis-match / compose-match
```

Examples:
```bash
trunkit verify 7                         # consumer: re-verify claim 7
trunkit standing --status refuted        # consumer: show failing claims
trunkit export 1 2 3 > bundle.json       # consumer: export proof bundle

trunkit check 7                          # prover dry-run: preview verdict
trunkit check 7 --write                  # prover: record certificate
trunkit witness 7 --kind trace \
  --body '{"steps":["σ(28)=56","56-28=28"]}' \
  --write                                # prover: attach witness
```

---

## Bootstrap

```bash
# Apply all schemas (idempotent)
for f in sql/*.sql; do psql $TRUNK_DSN -f "$f"; done

# Populate integers, sync categories, run reflexive closure
python tools/kan_in_kan.py

# Or from Python:
from calx.db import apply_unified
apply_unified()
```

`apply_unified()` runs every SQL file in order, then:
```sql
SELECT kan.sync_category('calx');
SELECT kan.sync_category('curry');
SELECT kan.sync_category('kan');
SELECT kan.populate_curry_calx_functor();
```

---

## calx — integer arithmetic

calx is the bedrock. It holds prime factorisations, divisor data, and dynamical
sequences for the integers [1..N], generated without modular arithmetic using
layered arithmetic progressions.

### Table sizing reference

Rows and estimated on-disk size at common N values (PostgreSQL 16, default fillfactor):

| N | `integers` | `factorizations` | `primes` | `calx` total |
|---|-----------|-----------------|---------|-------------|
| 10,000 | 10 K rows · ~1 MB | ~35 K rows · ~3 MB | 1,229 rows · <1 MB | **~5 MB** |
| 100,000 | 100 K rows · ~9 MB | ~380 K rows · ~30 MB | 9,592 rows · ~1 MB | **~40 MB** |
| 1,000,000 | 1 M rows · ~90 MB | ~4 M rows · ~320 MB | 78,498 rows · ~7 MB | **~420 MB** |

`factorizations` dominates — it grows as N · ln(ln(N)) due to the average number
of prime factors per integer. `primes` is tiny at any practical N (N / ln(N)).
The default bootstrap (`kan_in_kan.py`) generates N = 10,000; raise it via the
`CALX_N` environment variable.

### Core tables and views

```sql
SELECT * FROM calx.integers LIMIT 5;
-- id, is_prime, omega (distinct prime count), Omega (total prime count), ...

SELECT * FROM calx.primes LIMIT 10;
-- p, prime_index

SELECT prime, exponent FROM calx.factorizations WHERE n = 360;
-- 2,3  3,2  5,1

SELECT * FROM calx.divisor_summary WHERE n = 28;
-- n, sigma (sum of divisors), tau (count), classification (perfect/abundant/deficient)
```

### Dynamics

```sql
-- Aliquot step: s(n) = σ(n) − n
SELECT calx.aliquot_step(28);   -- → 28  (perfect number; fixed point)
SELECT calx.aliquot_step(12);   -- → 16

-- Arithmetic derivative
SELECT calx.arithmetic_derivative(12);  -- → 16  (12 = 2²·3, derivative = 2·6 + 4·1)

-- Radical: product of distinct prime factors
SELECT calx.radical(360);  -- → 30  (2·3·5)

-- Follow an orbit
SELECT step, value FROM calx.orbits WHERE n = 220 ORDER BY step;
```

### CRT

```sql
-- Extended GCD
SELECT * FROM calx.extended_gcd(35, 15);
-- gcd=5, s=1, t=-2

-- Modular inverse
SELECT calx.mod_inverse(3, 7);  -- → 5  (3·5 ≡ 1 mod 7)

-- Chinese Remainder Theorem lift
SELECT calx.crt_lift(ARRAY[2,3], ARRAY[3,5]);  -- → 8  (≡2 mod 3, ≡3 mod 5)
```

### OEIS matching

```sql
-- Match a prefix against known sequences
SELECT * FROM calx.oeis_match(ARRAY[1,1,2,3,5,8,13]);
-- sequence_id, name, offset, match_length

-- Composition match (shape of a sequence's structure)
SELECT * FROM calx.composition_match(12);
```

---

## curry — versioned provenance

curry is the provenance layer. Everything computed gets a version-locked record.
No row is ever updated; drift is detected by comparing versions.

### Registering constants and functions

```sql
-- Register an immutable constant
INSERT INTO curry.constants (name, version, value_json, description)
VALUES ('PI_approx', 1, '3.14159'::jsonb, 'Rational approximation to π')
ON CONFLICT (name, version) DO NOTHING;

-- Register a pure function
INSERT INTO curry.functions (name, version, arity, is_pure, description)
VALUES ('aliquot_step', 1, 1, TRUE, 'σ(n) − n')
ON CONFLICT (name, version) DO NOTHING;

-- Look up current version
SELECT * FROM curry.constants WHERE name = 'PI_approx' ORDER BY version DESC LIMIT 1;
```

### Recording inference provenance

Every `cert.check()` call automatically appends a `curry.inferences` row.
You can also write provenance directly:

```sql
INSERT INTO curry.inferences (
    inference_id, model_name, model_version,
    input_tokens, output_tokens,
    execution_timestamp, temperature_used, max_tokens_used, metadata
) VALUES (
    gen_random_uuid()::text,
    'my-checker', 1,
    convert_to('{"claim_id":7}'::text, 'UTF8'),
    convert_to('valid',                'UTF8'),
    now(), 0.0, 0,
    '{"method":"comp_sql"}'::jsonb
);
```

### Querying provenance

```sql
-- All inferences for a checker model
SELECT inference_id, execution_timestamp, metadata
  FROM curry.inferences
 WHERE model_name = 'cert-checker-model'
 ORDER BY execution_timestamp DESC;

-- Trace a certificate back to its curry provenance
SELECT ce.status, ce.checked_at, inf.model_name, inf.metadata
  FROM cert.certificate ce
  JOIN curry.inferences inf ON inf.inference_id = ce.checker_inference_id
 WHERE ce.claim_id = 7
 ORDER BY ce.seq DESC LIMIT 1;
```

---

## kan — category theory

kan has two modes:

- **Reflection mode** — `kan.sync_category('schema')` reads Postgres FK edges
  and auto-populates categories, objects, and morphisms.
- **Explicit mode** — insert categorical structures by hand for abstract diagrams
  not encoded as FK graphs.

### Layer 0 — base categories

```sql
-- Reflect a Postgres schema into kan
SELECT kan.sync_category('calx');

-- Inspect what was synced
SELECT * FROM kan.presentation;

-- Manually register a diagram category
INSERT INTO kan.category (name, db_schema, description)
VALUES ('TL', NULL, 'Temperley-Lieb category')
ON CONFLICT (name) DO NOTHING;

INSERT INTO kan.object (category, name) VALUES ('TL', '2'), ('TL', '4')
ON CONFLICT DO NOTHING;
```

### Layer 0b — elements and composition

```sql
-- Insert a generator element
SELECT kan.upsert_element('TL', 'e12', '2', '4', '{"diagram":"cap-cup"}'::jsonb);

-- Set the identity at object '2'
SELECT kan.set_identity('TL', '2', 'id_2');

-- Record a computed composition e12 ∘ e21 = e12_e21
SELECT kan.record_composition('TL', 'e12', 'e21', 'e12_e21');

-- Look up a memoised composition
SELECT kan.lookup_composition('TL', 'e12', 'e21');
```

### Layer 1 — monoidal structure

```sql
-- Register a tensor product
SELECT kan.register_monoidal('TL', 'tensor', '0', TRUE, 'Horizontal juxtaposition');

-- Record a ⊗ b = c
SELECT kan.record_tensor('TL', 'tensor', 'e12', 'e34', 'e12_e34');

-- Record an involution (dagger)
SELECT kan.record_involution('TL', 'e12', 'e21');

SELECT * FROM kan.self_dual_elements;
```

### Layer 2 — natural transformations

```sql
-- Register an NT η: F ⇒ G
SELECT kan.register_nt('eta', 'F_calx', 'G_calx', 'Unit of adjunction');

-- Set components
SELECT kan.set_nt_component('eta', '7', 'eta_at_7');

-- Check naturality (empty result = natural)
SELECT * FROM kan.check_naturality('eta');

-- Check natural isomorphism
SELECT kan.is_natural_iso('eta');

-- Vertical composition η · θ
SELECT kan.nt_vertical_compose('eta', 'eps', 'id_composed');
```

### Layer 3 — Kan extensions

```sql
-- Register a left Kan extension request
SELECT kan.register_extension('Lan_i_F', 'F_calx', 'i_inclusion', 'left', 'Lan of F along i');

-- After computing object/morphism maps in Python:
SELECT kan.set_extension_object_map('Lan_i_F', 'obj_3', 'tgt_7');
SELECT kan.set_extension_morphism_map('Lan_i_F', 'morph_ab', 'elem_xy');

-- Faithfulness check
SELECT kan.extension_is_faithful('Lan_i_F');
```

### Layer 4 — enriched categories

```sql
-- Register a K-linear enrichment
SELECT kan.register_enrichment('TL_Bool', 'Bool', 0, TRUE, NULL, 'Boolean linearisation');

-- Build a linear combination  f + g
SELECT kan.linear_combine('TL_Bool', 'f_plus_g', '3', '3', '{"e12":1,"e21":1}'::jsonb);

-- Test idempotent semiring (required for faithful reps)
SELECT kan.is_idempotent_semiring('TL_Bool');   -- → TRUE
```

### Layer 5 — profunctors and Yoneda

```sql
-- Register a profunctor H: C^op × D → Set
SELECT kan.register_profunctor('H_TL', 'TL', 'Bool', 'Boolean profunctor');

-- Set a cell H(d, c)
SELECT kan.set_profunctor_cell('H_TL', 'true', '2', 'e12_ne_0');

-- Yoneda embedding (creates one profunctor per object)
SELECT * FROM kan.yoneda_embed('TL');

-- Compose two profunctors
SELECT * FROM kan.profunctor_compose('Hom_TL', 'Hom_TL', 'Hom_sq');
```

### Layer 6 — adjunctions

```sql
-- Register L ⊣ R
SELECT kan.register_adjunction(
    'free_forget', 'free_kmod', 'forget_kmod',
    'eta_free_forget', 'eps_free_forget',
    'Free K-module ⊣ Forgetful'
);

-- Verify triangle identities (empty = satisfied)
SELECT * FROM kan.check_triangle_identities('free_forget') WHERE NOT passes;

-- Promote a Kan extension to an adjunction Lan_i ⊣ i*
SELECT kan.adjunction_from_extension('Lan_i_F');

SELECT * FROM kan.adjunction_summary;
```

### Reflexive closure

kan describes itself: `kan.sync_category('kan')` reflects kan's own FK graph
back into kan. `tools/kan_in_kan.py` runs the full six-phase closure and attests
the result via cert.

```bash
python tools/kan_in_kan.py
# Phases: sync × 3 → self-provenance → kan_self functor → confidence → persist → certify
# Exit 0 iff coverage = 1.0 and all claims valid
```

---

## cert — proof-carrying attestation

cert binds claims to checking methods, stores append-only version-pinned
certificates, and supports portable proof export. It is not a theorem prover —
it records that a claim was accepted by a method under stated assumptions,
verifiably and compositionally.

### Tables

| Table | Purpose |
|-------|---------|
| `cert.method` | Five method tiers (see above) |
| `cert.claim` | `statement` (UNIQUE), `claim_kind`, `method`, `probe_sql` |
| `cert.certificate` | Append-only: `(claim_id, seq)`, `status`, `evidence`, `valid_under`, provenance FK |
| `cert.artifact` | Formal tier: `path`, `sha256`, `checker_cmd` |
| `cert.witness` | Structured proof terms stored alongside certificates |
| `cert.derivation` | Proof composition DAG: `premise_ids[]` → `conclusion_id` under `rule` |

### Making a claim

```sql
-- comp_sql: in-DB probe returning (ok BOOLEAN, evidence JSONB)
INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
VALUES (
    'calx_expr',
    '{"n": 28}'::jsonb,
    '28 is a perfect number: σ(28) − 28 = 28',
    'computational', 'comp_sql',
    'SELECT (calx.aliquot_step(28) = 28) AS ok,
            jsonb_build_object(''result'', calx.aliquot_step(28)) AS evidence'
);

-- witness_carry: probe returns (ok, evidence, witness JSONB) — witness is stored
INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
VALUES (
    'calx_expr',
    '{"n": 12}'::jsonb,
    '12 has p-adic stratification {2:2, 3:1}',
    'computational', 'witness_carry',
    $probe$
        SELECT
            (v2 = 2 AND v3 = 1) AS ok,
            jsonb_build_object('v2', v2, 'v3', v3) AS evidence,
            jsonb_build_object(
                'kind', 'term',
                'levels', jsonb_build_object('prime_2', v2, 'prime_3', v3),
                'reconstruction', '2^2 * 3^1 = 12'
            ) AS witness
        FROM (
            SELECT
                (SELECT count(*)::int FROM calx.factorizations WHERE n=12 AND prime=2) AS v2,
                (SELECT count(*)::int FROM calx.factorizations WHERE n=12 AND prime=3) AS v3
        ) sub
    $probe$
);
```

### Checking claims

```sql
-- Check one claim (appends a certificate row)
SELECT cert.check(1);

-- Check one witness_carry claim (appends certificate + witness)
SELECT cert.check_with_witness(2);

-- Re-check all probe-driven claims
SELECT * FROM cert.check_all();

-- View latest status per claim
SELECT statement, status, evidence, checked_at FROM cert.standing;
```

### Side-effect-free re-verification (consumer use)

```sql
-- Replay probe and/or stored witness — no INSERT, no state change
SELECT ok, evidence, witness FROM cert.verify(1);

-- Verify a derivation's premises
SELECT * FROM cert.derivation_valid(1);
```

### Formal external artifacts

For claims backed by external scripts (Python, Lean, Agda):

```python
# tools/cert_formal.py handles this automatically.
# Manual registration:
```

```sql
SELECT cert.register_artifact(
    claim_id := 7,
    p_kind := 'python',
    p_path := 'proofs/perfect_28.py',
    p_sha256 := NULL,        -- NULL = TOFU on first run; captured automatically
    p_checker_cmd := 'python proofs/perfect_28.py'
);
```

```bash
# Run the formal harness (TOFU on first run, drift-detection thereafter)
python tools/cert_formal.py
```

### Proof composition (derivation DAG)

```sql
-- Assert: claim 3 is valid because claims 1 and 2 are valid under modus_ponens
INSERT INTO cert.derivation (conclusion_id, premise_ids, rule)
VALUES (3, ARRAY[1, 2], 'modus_ponens');

-- Assert a stratified lift: physics claim follows from integer claims
INSERT INTO cert.derivation (conclusion_id, premise_ids, rule)
VALUES (15, ARRAY[7, 9, 11], 'stratified_lift');

-- Validate the derivation (checks all premises have valid latest certificates)
SELECT * FROM cert.derivation_valid(1);
```

### Attaching witnesses manually

```sql
-- Attach a proof witness to the latest certificate for a claim
SELECT cert.attach_witness(
    p_claim_id := 1,
    p_kind     := 'trace',
    p_body     := '{"steps": ["σ(28)=56", "56-28=28", "fixed point"]}'::jsonb
);

-- Attach a categorical diagram witness
SELECT cert.attach_witness(
    p_claim_id := 5,
    p_kind     := 'kan_diagram',
    p_body     := '{"functor":"curry_to_calx","naturality":"verified","triangle":"satisfied"}'::jsonb
);
```

### Exporting a portable proof bundle

```sql
-- Export claims 1, 2, 3 as a self-contained JSONB bundle
SELECT cert.export_bundle(ARRAY[1, 2, 3]);
```

The bundle contains: claims, latest certificates, witnesses, derivation chains,
and artifact specs. A consumer imports the JSONB and calls `cert.verify()` on
each embedded `claim_id` to re-check without trusting the producer.

### kan-engine bridge

Step 79 auto-discovers every `kan.<x>_laws` view and ANDs all boolean columns
into a single comp_sql claim — defense in depth across the full arc:

```sql
SELECT * FROM cert.kan_engines_all_true();
-- ok, evidence: {engines_checked: N, all_true: true, engines: {...}}
```

### Full audit view

```sql
-- All claims, latest status, method, evidence
SELECT cl.id, cl.statement, cl.method, ce.status, ce.evidence, ce.checked_at
  FROM cert.standing ce
  JOIN cert.claim cl ON cl.id = ce.id
 ORDER BY cl.id;

-- Claims with stored witnesses
SELECT cl.statement, w.kind, w.body
  FROM cert.witness w
  JOIN cert.certificate ce ON ce.id = w.certificate_id
  JOIN cert.claim cl ON cl.id = ce.claim_id
 ORDER BY cl.id;

-- Derivation graph
SELECT cl_c.statement AS conclusion, d.rule, array_agg(cl_p.statement) AS premises
  FROM cert.derivation d
  JOIN cert.claim cl_c ON cl_c.id = d.conclusion_id
  JOIN cert.claim cl_p ON cl_p.id = ANY(d.premise_ids)
 GROUP BY cl_c.statement, d.rule;
```

---

## Bridge map

| Bridge | Mechanism |
|--------|-----------|
| calx / curry / kan → kan | `kan.sync_category(schema)` — FK reflection |
| curry ↔ calx | `kan.populate_curry_calx_functor()` |
| kan → kan (reflexive) | `kan.sync_category('kan')` + `kan_self` endofunctor |
| any pillar → cert | `cert.claim.probe_sql` + `cert.check()` |
| cert → curry (provenance) | every check inserts a `curry.inferences` row |
| formal artifact → cert | `cert_formal.py` + `cert.artifact` sha256 pin |
| kan-engine → cert | `cert.kan_engines_all_true()` (step 79) |
| claim → portable bundle | `cert.export_bundle(ids[])` |
| bundle claim → verdict | `cert.verify(claim_id)` — no side effects |
