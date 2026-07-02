<p align="center">
  <img src="assets/logo.png" alt="Trunkit" width="220" />
</p>

# Trunkit

> The smallest elephant in the room that's not too Coq-y and doesn't Lean too heavily on your system.

Proof-carrying code middleware on PostgreSQL. Trunkit is a self-contained schema stack
that attaches verifiable claims to mathematical objects, chains proofs compositionally,
and lets consumers re-verify results without trusting the producer — all inside a
database you already operate, with no specialist toolchain required.

No 3 GB compiler. No gigabytes of cached proof objects. No new runtime to learn.
Just PostgreSQL, Python, and ~1.5 MB of schemas.

---

## Stack

```
┌──────────────────────────────────────────────────────────────────┐
│  Porter  — agent context handoff                                  │
│    Precacher · Sources · close_session() · open_session()         │
│    cybernetic DFAs · composite pattern detection                  │
├──────────────────────────────────────────────────────────────────┤
│  Nerode  — deterministic automata engine                          │
│    DFA construction · minimization · product · session DFAs       │
│    sequence cache · cert-signed handoff envelopes                 │
├──────────────────────────┬───────────────────────────────────────┤
│  cert                    │  kan                                   │
│  proof-carrying          │  category theory meta-layer            │
│  attestation             │  (monoidal · NTs · Kan · profunctors)  │
├──────────────────────────┼───────────────────────────────────────┤
│  curry                   │  calx                                  │
│  versioned provenance    │  integer arithmetic bedrock            │
│  + immutable constants   │  (primes · CRT · dynamics · OEIS)     │
└──────────────────────────┴───────────────────────────────────────┘
   Trunkit DB  postgresql://trunk:trunk@localhost:5434/trunk
   Nerode DB   postgresql://nerode:nerode@localhost:5435/nerode
```

| Layer | Role |
|-------|------|
| **calx** | Dense prime factorisation of ℤ[1..N]; aliquot/derivative dynamics; CRT; OEIS exact + **cosine-candidate** matching; **C-finite/P-finite recurrence certificates** |
| **curry** | Immutable versioned constants and functions; append-only computational provenance |
| **kan** | Category-*structured* meta-layer: reflects Postgres FK graphs into objects/morphisms and checks **structural invariants** (triangle commutativity, product universal property, naturality, epi classification) as re-runnable probes — see the caveat below |
| **cert** | Proof-carrying attestation: six method tiers, structured witness storage, proof composition DAG, portable bundle export, consumer re-verification; **exact-domain shield**, **recurrence/morphism certificates**, **holographic (Merkle) commitments**, **image anchoring**, **crypto-succinct (arithmetised) claims** |
| **Nerode** | DFA/automata engine on PostgreSQL: construction, minimization, product, session DFAs, sequence cache, certified handoff envelopes |
| **Porter** | Agent context handoff: pre-pack external data, certify session boundaries, hand verified context to a new model with zero tool calls |

> **What "kan" does and does not claim.** kan performs *structural invariant
> checking*, **not formal proof**. A claim like "the calx → curry functor is
> faithful" is attested by a SQL probe that checks the **current database state**
> (e.g. the morphism map is injective on the rows present) — it is re-runnable
> evidence, not a machine-checked theorem about all inputs. Real proof lives in
> external Lean/Agda artifacts (the `formal_external` tier) or in the
> independent `cert_kernel` checkers. Treat `struct_kan` as "this categorical
> invariant holds over the data we have," with the same three-valued honesty
> (`valid`/`refuted`/`unverified`) as everywhere else.

### Why two databases?

Trunkit (calx/curry/kan/cert) and Nerode (automata/porter) run as **separate
PostgreSQL instances** by design, not by accident:

- **Failure & trust isolation** — the proof ledger (append-only, hash-chained;
  see `SECURITY.md`) must not share a backend with the automata/agent-handoff
  workload, which ingests external data and runs untrusted-ish session traces.
- **Independent lifecycle** — Nerode can be reset/rebuilt (it's a cache + DFA
  workspace) without touching the immutable cert ledger.
- **Cross-instance entanglement is by value, not by FK** — a Porter envelope
  embeds `cert.ledger_root()` and the cert side records the envelope hash via
  `cert.anchor_external`, so the two are cryptographically linked without a
  shared transaction (a single physical chain can't span two instances anyway).

The cost is real (two DSNs, two `apply` targets); the benefit is that a
compromised or wiped Nerode cannot corrupt or rewrite proof history.

---

## Quick start

From pip (requires Docker for the bundled databases):

```bash
pip install trunkit   # installs both the trunkit and nerode CLIs
trunkit quickstart      # writes docker-compose.yml, starts both DBs, applies both schemas
trunkit generate --limit 10000
trunkit standing
```

`trunkit quickstart --compose-only` just writes the compose file if you'd
rather start the databases yourself; already-running instances are picked up
via `$CALX_DSN` / `$NERODE_DSN`.

From a repo checkout:

```bash
# 1. Start both PostgreSQL instances
docker compose up -d db-trunkit db-nerode

# 2. Apply all schemas (idempotent)
make apply

# 3. Trunkit: populate integers and run reflexive closure
trunkit generate --limit 10000
trunkit close --write

# 4. Porter: pre-pack a morning brief and open it as Model B
python scripts/morning_brief_demo.py
```

Environment variable: `CALX_DSN=postgresql://trunk:trunk@localhost:5434/trunk`

---

## CLI

Trunkit ships a dual-surface CLI. Consumer commands are read-only and safe for LLM use.
Prover commands require `--write` to record; they dry-run without it.

### Consumer — read-only

```bash
trunkit verify <claim_id>
# Re-verifies a claim without inserting. Replays the stored witness or
# re-runs the probe SQL in a subtransaction. Exits 0 if valid.

trunkit standing [--method M] [--status S]
# Lists all claims with their latest attestation status.
# Filter by method (comp_sql, struct_kan, formal_external,
# empirical_corpus, witness_carry) or status (valid, refuted, unverified).

trunkit export <id> [<id> ...]
# Emits a self-contained JSONB bundle to stdout:
# claims + certificates + witnesses + derivations.
# Portable — consumers can re-verify without a Trunkit install.

trunkit verify --bundle FILE [--offline]
# Consumer side of export: re-verifies a received bundle.
# Replays each embedded probe against YOUR database (rolled back after),
# checks witness presence, and verifies artifact sha256 hashes against
# local files. --offline skips probe replay (structural checks only).
# Three-valued per claim: VALID / REFUTED / UNVERIFIED. Exits 0 iff all valid.
```

```
$ trunkit standing
  [✓] #  1  comp_sql              valid         2026-05-19 14:32  28 is a perfect number: σ(28) − 28 = 28
  [✓] #  2  witness_carry         valid         2026-05-19 14:32  12 has p-adic stratification {2:2, 3:1}
  [✓] #  3  struct_kan            valid         2026-05-19 14:33  calx → curry functor is faithful
  [?] #  4  formal_external       unverified    —                 σ(28) = 56 (external Python proof)

$ trunkit verify 2
  [✓] claim 2  →  VALID
  evidence : {
      "v2": 2,
      "v3": 1
  }
  witness  : {
      "kind": "term",
      "levels": {"prime_2": 2, "prime_3": 1},
      "reconstruction": "2^2 * 3^1 = 12"
  }
```

### Prover — require `--write`

```bash
trunkit check <claim_id> [--write]
# Dry-run: shows what the claim would attest as (via cert.verify).
# With --write: runs cert.check() and records a certificate.

trunkit attest [--write]
# Dry-run: reports formal-tier claims that would be attested.
# With --write: runs cert_formal.py and records all formal-tier certificates.

trunkit close [--write]
# Dry-run: reports intent without side effects.
# With --write: computes reflexive closure — curry fixed points
# (primitive eigenforms) + kan Perron-Frobenius attractor — and
# records eigenform claims.

trunkit witness <claim_id> --kind KIND --body JSON [--write]
# Attach a structured proof witness to a claim.
# KIND: term | trace | counterexample | hash_chain | kan_diagram | quote_span

trunkit register-lean <claim_id> --root DIR --decl Module.thm [--write]
# Register a Lean (Lake) project + declaration as a formal_external artifact.
# Re-checked by tools/lean_check.sh: lake build + axiom / `sorry` audit.
```

### calx data

```bash
trunkit init                           # apply schema DDL
trunkit generate --limit N             # populate integers 1..N
trunkit validate [--limit N]           # compare ω/Ω against OEIS
trunkit reset                          # drop all calx tables
trunkit oeis-load [--family F]         # fetch curated OEIS b-files
trunkit oeis-match [--orbit-id ID | --all]
trunkit compose-match
trunkit oeis-cosine --seq-id A000045    # scale-invariant growth-shape candidates, confirmed by exact prefix
```

---

## Porter — model-to-model context handoff

Each model session starts with no memory of what the previous session fetched, proved,
or decided. Porter pre-packs that context — external data, DFA states, proof certificates
— into an envelope before the session closes. The next model calls
`Precacher.open(envelope, session_id)` and has everything ready, cert verified, with
zero tool calls.

```python
from datetime import date
from nerode.precache import Precacher
from nerode.sources import WeatherSource, TickerSource, HNSource

today = date.today().isoformat()

# Model A — fetch and pack before closing
with Precacher(f"brief-{today}") as pc:
    pc.fetch(f"weather:london:{today}", WeatherSource(51.5, -0.1, label="London"))
    pc.fetch(f"ticker:AAPL:{today}",    TickerSource("AAPL"))
    pc.fetch(f"news:hn:top5:{today}",   HNSource(5))
# __exit__ calls close_session(); pc.envelope is now set

# Model B — open the envelope in a separate process/connection
ctx = Precacher.open(pc.envelope, "model-b-001")
resolved = ctx["resolved"]        # {"weather:london:…": {…}, "ticker:AAPL:…": {…}, …}
cert_ok  = ctx["prior_session"]["cert_valid"]   # True
```

### Cybernetic monitoring

Porter includes DFA-based pattern detectors that fire over metric and control streams:

| DFA | Pattern | Meaning |
|-----|---------|---------|
| `metric_rise_3` | `U{3,}` | 3+ consecutive rises |
| `metric_oscillate` | `(UD){3,}` | oscillation / gain too high |
| `dead_time_5/10/20` | `A_{k,}` | action without response in k steps |
| `homeostasis_alarm_5` | `O{5,}` | 5+ steps outside target band |
| `dead_time_5_x_metric_oscillate` | composite | oscillating AND unresponsive |

```python
# Log a paired (metric, control) event and scan all relevant DFAs
conn.execute(
    "SELECT nerode.log_cybernetic(%s, 'metric_x_control', %s)",
    (session_id, "UA")   # metric rose (U), action taken (A)
)
# pg_notify('nerode_control_warn', ...) fires if any pattern matches
```

---

## cert method tiers

| Method | Trust root | Use |
|--------|-----------|-----|
| `comp_sql` | In-DB probe | Computational facts about integers or categorical counts |
| `struct_kan` | Existing kan invariant | Naturality, triangle identities, faithfulness checks |
| `formal_external` | SHA256-pinned external artifact | Python/Lean/Agda proof scripts |
| `empirical_corpus` | Provenance only | Corpus document assertions |
| `witness_carry` | In-DB witness term | Structured proof terms stored alongside certificates; consumer-replayable |
| `crypto_succinct` | Pinned verifying key + external verifier | Succinct (optionally zero-knowledge) proofs over arithmetised first-order claims (`calx.arith`); the prover stays outside the package |

> **Exactness shield.** Every claim also carries a `domain` tag (`exact_int` /
> `rational` / `algebraic` / `interval` / `float_heuristic`). A ledger trigger
> guarantees a `float_heuristic` claim can **never** record a `valid`
> certificate — only `unverified`. Heuristics (e.g. cosine similarity)
> *propose*; exact probes *decide*.

---

## Attestable structures (calx / cert)

Beyond integer-fact and categorical claims, cert ships re-checkable, **exact** certificate families:

- **Recurrence certificates** — a C-finite/P-finite recurrence (coefficients + initial terms) regenerates a sequence; `cert.recurrence_*` verifies it exactly and refuses non-exact division.
- **Sequence morphisms** — `cert.morphism_*` proves an exact map (scale / affine / index-shift) between two sequences — the structural relation a cosine candidate only hints at.
- **OEIS cosine candidates** — `calx.oeis_cosine_candidates` surfaces scale-invariant growth-shape neighbours, each confirmed by exact prefix agreement.
- **Image anchoring + similarity** — `cert.image_*` anchors a figure by sha256 and scores perceptual cosine similarity (pure-SQL; the `[image]` extra is only for decoding).
- **Holographic commitments** — `cert.merkle_root` / `cert.claim_commitment` commit an arbitrarily long trace to a 32-byte root (core `sha256`).

---

## PCC properties

| Property | Mechanism |
|----------|-----------|
| Proof travels with code | `cert.witness` stores structured proof terms alongside every certificate |
| Proofs compose | `cert.derivation` encodes a DAG of premises → conclusion under named rules |
| Consumer re-verifies | `cert.verify(claim_id)` replays without INSERTing — safe for untrusted callers |
| Bundle is portable | `cert.export_bundle(ids[])` emits self-contained JSONB: claims + certs + witnesses + derivations |

---

## Bundle size

| Component | Files | Size |
|-----------|-------|------|
| SQL (calx 00–98 + nerode 00–C0) | ~117 | ~711 KB |
| Python packages (`src/`: calx + nerode + trunkit_mcp) | ~25 | ~183 KB |
| Python tools | ~17 | ~188 KB |
| Proof scripts | 6 | ~42 KB |
| Tests | ~45 | ~296 KB |
| **Total (no virtualenv)** | **~210** | **~1.4 MB** |

For scale only (not a capability comparison): a Lean 4 toolchain is ≈ 2.9 GB
per version and a compiled Mathlib ≈ 4–10 GB per project. **Trunkit is not a
substitute for a proof assistant** — Lean/Mathlib verify arbitrary
human-authored theorems, whereas Trunkit re-checks a fixed, small set of
certificate schemas (factorization, CRT, Egyptian fractions, matrix words,
recurrence & morphism certificates, image / OEIS cosine candidates) plus
re-runnable in-DB probes. The size figures say only that Trunkit fits in a
database you already run; they do **not** imply equivalent verification power.

---

## Repository layout

```
src/
  calx/       — Trunkit Python package (calx + kan + curry + cert)
              + leanbridge.py · imagefeatures.py · recurrence.py
  nerode/     — Nerode/Porter Python package
    sql/      — 38 idempotent SQL schema files (00_bootstrap → C0_verify),
                incl. quantitative EHA/WFFA (A0–A6) + Porter policy gate (B0–B4, C0)
    precache.py   — Precacher context manager (Porter API)
    sources.py    — WeatherSource, TickerSource, HNSource, TickerHistorySource
    adapters.py   — HttpSource, CallableSource, resolve(), with_retry()
    db.py         — connection utilities + SCHEMA_FILES list
  trunkit_mcp/ — MCP server (`trunkit-mcp`): consumer verify/export tools always
                on; prover tools gated behind TRUNKIT_ALLOW_WRITE=1
scripts/
  morning_brief_demo.py   — end-to-end Porter demo
tests/
  test_sources.py         — network tests (pytest -m network)
  test_cybernetic.py      — cybernetic DFA tests
  test_composite_dfa.py   — paired-alphabet composite DFA tests
  test_dead_time_factory.py
  test_*.py               — unit tests for all schema layers
proofs/
  *.py    — Trunkit proof scripts
tools/
  cert_formal.py                        — formal-tier harness (python + lean checkers)
  lean_check.sh, AxiomAudit.lean        — Lean bridge driver + axiom/`sorry` auditor
  image_features.py, figure_extract.py  — vision tools ([image] extra)
  oeis_loader.py, oeis_match.py, compose_match.py — OEIS workflows
```

---

## License

MIT
