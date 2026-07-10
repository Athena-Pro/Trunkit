# Trunkit — capability checklist & footprint / overlap analysis

*Prepared 2026-06-21. Sizes are **source-text KB** (SQL + Python), measured from the current tree, rounded — they gauge relative weight and overlap, not installed/compiled size. The only external runtime dependency is **`psycopg`** (shared by every module; never duplicated).*

## PyPI status (live)
`pip install trunkit` → **0.3.0** ([pypi.org/project/trunkit/0.3.0](https://pypi.org/project/trunkit/0.3.0/)), verified to resolve from PyPI. This release **ships the full expansion** — Lean bridge, vision, OEIS cosine, and the Nerode A·B·C quantitative/policy layers — and the wheel was confirmed to contain `41a`/`91`/`92`, `leanbridge.py`, `imagefeatures.py`, and `tools/lean_check.sh`, with the local-only loom files (`local/sql/97_kan_loom.sql`–`99_loom_lift.sql`) correctly absent. Core stays `psycopg`-only; `[image]` (Pillow) is an optional extra.

> **Numbering caveat (added 2026-07-01).** The 0.3.0 wheel check above said "97/98
> correctly absent" — at that time those numbers named the *local-only* loom files.
> The core schema has since grown its **own** `97_cert_crypto.sql` and
> `98_kan_scott.sql`, which **do** ship in the wheel. Any re-run of the packaging
> audit must check for the absence of the loom files *by name*, not by number.
> Post-0.3.0 additions not yet reflected in the tables below: crypto-succinct cert
> tier + `calx.arith`, the `trunkit_mcp` server package, universal verification
> method kernels (METHODS.md), the Fourier radial-ring descriptor, vendored
> `lithon_core`, and the Scott/domain-theory kan engine.

## Capability checklist

| # | Module | Capability | On PyPI 0.3.0? |
|---|--------|-----------|:--:|
| 1 | **calx** | prime factorisation of ℤ[1..N], ω/Ω, p-adic strata, aliquot/derivative dynamics, CRT, OEIS load + exact prefix match | ✅ |
| 2 | **curry** | immutable versioned constants/functions, append-only provenance | ✅ |
| 3 | **kan** | FK-graph → category reflection; structural-invariant probes (naturality, triangles, faithfulness, equipment) | ✅ |
| 4 | **cert** | proof-carrying attestation: 5 method tiers, witness store, derivation DAG, portable bundles, consumer re-verify, append-only ledger | ✅ |
| 5 | **Nerode** | DFA engine: build/minimise/product, regex→DFA, session DFAs, sequence cache, certified handoff | ✅ |
| 6 | **Porter** | agent context handoff; cybernetic DFAs; **policy gate / warden** (B·C layers) | ✅ |
| 7 | **Nerode-Q (A·B·C)** | quantitative EHA/WFFA, exact extremal bounds + witness, policy gate, carry/verify | ✅ |
| 8 | **Lean bridge** | `formal_external` Lean adapter: closure-digest drift gate + axiom/`sorry` audit, `register-lean` | ✅ |
| 9 | **Vision** | image anchoring (sha256) + pure-SQL cosine similarity; descriptor module; `[image]` tool; figure-extract scaffold | ✅ |
| 10 | **OEIS cosine** | scale-invariant candidate generator wired to the exact verifier (`oeis-cosine`) | ✅ |

## Per-module "own" footprint (beyond the shared kernel)

| Module | SQL | Python | Own total (approx) |
|--------|----:|-------:|----:|
| calx (number theory) | ~54 KB | ~6 KB | **~60 KB** |
| kan (category theory) | ~141 KB | — | **~141 KB** |
| cert (specialised attestations, beyond core) | ~60 KB | ~30 KB | **~90 KB** |
| Nerode (automata core) | ~154 KB | ~12 KB | **~166 KB** |
| Porter + Nerode-Q (A·B·C, warden, cybernetic) | ~107 KB | ~34 KB | **~141 KB** |
| Lean bridge | ~4 KB | ~9 KB | **~13 KB** |
| Vision | ~7 KB | ~9 KB | **~16 KB** |
| OEIS cosine | ~7 KB | ~1 KB | **~8 KB** |

## The shared "kernel" (the primitive elements every module reuses)

Counted **once** in the bundle; would be **re-duplicated** in every standalone package:

| Shared primitive | ~Size | Reused by |
|------------------|------:|-----------|
| DB layer (`db.py` ×2: connect, DSN resolve, schema apply) | ~9 KB | all |
| SQL bootstrap (`00_rehome`, `01_schema` core, roles/search_path) | ~20 KB | all |
| cert core (`40_cert` claim/certificate/check + `84–88` witness/derivation/verify/bundle) | ~30 KB | cert, Lean, Vision, OEIS, kan/calx claims |
| curry provenance (`10_curry` + adapter) | ~10 KB | all attestation |
| CLI scaffold (argparse base) | ~5 KB | all commands |
| generic cosine (`vec_cosine`/`image_cosine`) | ~1 KB | **Vision + OEIS** |
| **Shared kernel total** | **≈ 75 KB** + `psycopg` | |

## Overlap savings (standalone vs. bundled)

If the 8 capability modules were shipped as **independent packages**, each would have to re-embed the ~75 KB kernel (DB layer, cert core, bootstrap, curry, CLI, psycopg binding):

- Σ module-own footprints: **≈ 585 KB**
- Kernel duplicated across 8 standalones: 8 × 75 = **≈ 600 KB**
- **Sum of standalones ≈ 1,185 KB**

Bundled (kernel shipped once):

- Own (585 KB) + kernel once (75 KB) = **≈ 660 KB**
- **Savings ≈ 525 KB (~44%)** — i.e. `(8−1) × 75 KB` of de-duplicated primitives.

### Where the overlap pays off most
- **The three newest layers are nearly free.** Lean (13) + Vision (16) + OEIS cosine (8) ≈ **37 KB combined**, because they ride existing primitives instead of rebuilding them: all three reuse `cert.claim/certificate/check` (no new attestation/ledger engine ≈ 30 KB saved each), and Vision + OEIS share the **same cosine function** (one ~1 KB primitive serves both). Packaged standalone they'd be ~3× larger.
- **OEIS cosine** also reuses calx's integer bedrock + the exact `oeis_match` verifier — it adds only the candidate-ranking layer.
- **cert's five tiers** are one engine, not five: `comp_sql`, `struct_kan`, `formal_external` (Lean), `empirical_corpus`, `witness_carry` all share claim/certificate/check + the bundle/verify path.

*Methodology note:* source-text bytes, rounded; kernel attribution is approximate (some files straddle layers). The ~660 KB bundled figure tracks the README's ~603 KB SQL+Python estimate. Treat percentages as "roughly," not audited.
