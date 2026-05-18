# Trunkit

> The smallest elephant in the room that's not too Coq-y and doesn't Lean too heavily on your system.

Proof-carrying code middleware on PostgreSQL. Trunkit is a self-contained schema stack
that attaches verifiable claims to mathematical objects, chains proofs compositionally,
and lets consumers re-verify results without trusting the producer — all inside a
database you already operate, with no specialist toolchain required.

No 3 GB compiler. No gigabytes of cached proof objects. No new runtime to learn.
Just PostgreSQL, Python, and ~1 MB of schemas that describe, attest, and verify themselves.

---

## Schemas

```
┌──────────────────────────────────────────────────────────┐
│  cert  — proof-carrying attestation                       │
│    witness · derivation · verify() · export_bundle()      │
├──────────────┬───────────────────────────────────────────┤
│  kan         │  curry                                     │
│  category    │  versioned provenance                      │
│  theory      │  + immutable constants                     │
├──────────────┴───────────────────────────────────────────┤
│  calx  — integer arithmetic bedrock                       │
│    primes · factorizations · dynamics · CRT · OEIS        │
└──────────────────────────────────────────────────────────┘
          PostgreSQL 16   ·   Python 3.11+
```

| Schema | Role |
|--------|------|
| **calx** | Dense prime factorisation of ℤ[1..N]; aliquot/derivative dynamics; CRT; OEIS sequence matching |
| **curry** | Immutable versioned constants and functions; append-only computational provenance |
| **kan** | Category-theory meta-layer: base categories → monoidal → NTs → Kan extensions → enrichment → profunctors → adjunctions; reflexively describes itself |
| **cert** | Proof-carrying attestation: five method tiers, structured witness storage, proof composition DAG, portable bundle export, side-effect-free consumer re-verification |

---

## Quick start

```bash
# 1. Start PostgreSQL
docker compose up -d db

# 2. Apply schemas (idempotent — safe to re-run)
make apply

# 3. Populate integers and compute reflexive closure
trunkit generate --limit 10000
trunkit close --write

# 4. Check all claims
trunkit standing
```

```bash
# Install
pip install trunkit
pip install -e ".[dev]"      # local development
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
# KIND: term | trace | counterexample | hash_chain | kan_diagram
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
| SQL (89 files, 00–88) | 89 | ~290 KB |
| Python tools | 37 | ~323 KB |
| Proof scripts | 24 | ~172 KB |
| Src + tests + config | ~30 | ~130 KB |
| **Total (no virtualenv)** | **~183** | **~1.1 MB** |

Compare: Lean 4 toolchain ≈ 2.9 GB per version; Mathlib compiled ≈ 4–10 GB per project.

---

## License

MIT
