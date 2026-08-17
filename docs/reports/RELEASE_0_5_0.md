# Trunkit 0.5.0 — the capability-gap release

*Closes the last two gaps in `docs/reports/Trunkit_Erdos_AI_Capability_Fit.md`, ships the
T2/T3 work that was already merged but never actually exercised, and fixes the reason it
wasn't.*

---

## Headline: CI was green on a schema that was never fully applied

The wheel audit for this release started as a packaging check and turned up something
worse. The Makefile and CI applied the calx schema with

```
for f in $(ls src/calx/sql/*.sql | LC_ALL=C sort -n); do psql ... -f "$f"; done
```

Every line begins `src`, so every `sort -n` key ties at 0 and the sort falls back to
lexicographic — where `100_` precedes `10_`. `100_cert_lifecycle.sql` was applied at
position **9**, nine files before `10_curry.sql` and long before the `cert` schema
existed. `psql` has no `ON_ERROR_STOP`, so it walked past **112 errors** and exited 0.

Measured against a fresh database, the old loop left:

| schema | old shell loop | package loader |
|---|---|---|
| cert | 15 tables, 34 functions | **33 tables, 80 functions** |
| comb | 12 tables, 58 functions | 12 tables, **61 functions** |

So tiers **100–109 never existed in CI**, including the **T3 numeric-bound tier** this
release is nominally shipping. 0.5.0 is the first build in which T3 is actually
exercised.

Both apply paths now call the package loaders — `trunkit init` (`calx.db.UNIFIED_FILES`)
and `nerode close --apply` (`nerode.db.SCHEMA_FILES`) — which were correct all along and
are now the single ordering authority.

`nerode` had the same construction and was correct only by luck of never reaching three
digits. Its two lists had drifted independently: `98_topological_signature.sql` and
`99_precacher_roundtrip_cert.sql` sat on disk outside `SCHEMA_FILES`, so the shell loop
applied them and `apply_schema` did not. Because every test database is built by
`apply_schema`, **the certificate that certifies the precacher close→open roundtrip had
never existed in the databases asserting that invariant.** Both are now listed, pinned by
a test.

## The wheel shipped a Lean build tree

`shared-data` is a force-include mapping: it honours neither `.gitignore` nor `exclude`.
Mapping the `proofs` and `tools` directories wholesale shipped whatever sat on the
builder's disk — **8,885 files / 97 MB of untracked Lean `.lake` output** (6.9 GB of it
exists locally), plus `__pycache__` and gitignored local-only scripts. That is precisely
the "3 GB compiler in the box" the README promises is never shipped.

| | 0.4.0 | 0.5.0 |
|---|---|---|
| wheel | 32.4 MB | **0.51 MB** |
| uncompressed | 104.3 MB | **1.47 MB** |
| entries | 10,076 | **197** |

1.47 MB is the documented core size. Every `shared-data` entry is now a single tracked
file, and a test asserts files-never-directories so the leak cannot return.

## New capability

### T5 — solution-provenance DAG (`114_prov_provenance.sql`)

Contributors, artifacts and the edges between them: the credit bookkeeping the Erdős
tracker maintains by hand in a wiki, given a schema.

Propagation is **inherited, not reimplemented**. A support edge mirrors into
`cert.derivation`, so `cert.tainted_closure` (102) does the transitive work and this layer
walks nothing.

**`corrected` is deliberately not a support edge.** `cert.derivation` means "conclusion
holds *because* the premises hold" — right for `used`/`formalized`/`prior_literature`. But
a correction does not depend on the flawed artifact being valid; it exists because it
isn't. Mirroring it would mean that recording that something was wrong also destroyed
belief in the thing that made it right.

Grades stay separate from verdicts. The tracker grades 🟢/🟡/🔴; `cert` is
valid/refuted/unverified and has no `partial`, and this release does not invent one.
An artifact with no bound claim is `unverified` forever, however green its grade.

### T4 — OEIS conjecture→attestation workflow (`115_oeis_attest.sql`)

`candidate → exact prefix → recurrence/morphism certificate → bounded composition →
optional anchored theorem`. No new store, no new schema, no new verification method — the
verification all belongs to 92/93/95.

**The global theorem is never derived from the finite chain.** Exactly one derivation edge
is earned — `exact-prefix(A,B) + certificate(B) ⊢ A explained over min(N,L) terms` — and
the theorem is anchored with zero premises. Deriving it would make the ledger report a
theorem `valid` because twelve terms lined up. `calx.oeis_attestation` exposes premise
counts so the rule is auditable rather than merely documented.

### #1196 baseline demonstration (`116_comb_primitive_set.sql`, `tools/demo_erdos_1196.py`)

111's `is_primitive_family` is the **set-system** form — no block strictly contains
another — which matches divisibility only for squarefree integers (2 divides 4 while `{2}`
does not strictly contain itself). 116 adds the **divisibility** form, which is what #1196
is actually about, beside it.

`demo_erdos_1196.py` runs every tier in one pass: four deposited integer sets probed and
carried as witnesses (T2), the extremal count as a width-0 enclosure (T3), the asymptotic
statement anchored as `formal_external` with no probe and no premises (T1), and the
four-contributor credit graph with a withdrawal propagating downstream (T5).

The demo's last step is the one a demo usually fudges: everything above it is `valid`, the
theorem stays `unchecked`, and the finite work does not support it. A primitive set is a
fact; #1196 is not.

## Also

- **`comb` and `prov` are now reflected into kan.** 110's header states the reuse as
  accomplished fact — "because `sync_category` reflects ANY schema, `comb` becomes a kan
  category the moment it exists" — but `sync_category` only ever ran on the names in
  `apply_unified`'s list, which named three schemas. `comb` had been invisible since T2.
  Both reflect cleanly (`comb` 10/10, `prov` 3/3). `cert` stays out deliberately;
  reflecting the ledger's own schema is a design question, not an omission.
- **psycopg stays pure Python.** `psycopg[binary]` ships no wheel for Termux/ARM, so it
  must not be the base dependency. The libpq-free path is `trunkit[binary]` — a real
  extra, now CI-smoke-tested on Linux alongside `[binary,mcp]`. Platform markers are not
  an alternative: they cannot separate glibc, musl and Termux/Android.
- **`mcp<2`** (`trunkit_mcp` uses the 1.x fastmcp API) and **`pydantic-settings<2.13`**
  (1.x `Settings` warns on an unresolved lifespan forward ref; warnings are errors here).
- `test_sources.py` took the `nerode_dsn` fixture and discarded it, so every `Precacher`
  fell back to `NERODE_DSN` or a hardcoded 5435 while the fixture read as if the test DSN
  were respected.

## Verification

**1036 passed, 0 failed, 95 skipped.** Every new layer verified on a fresh database via
`trunkit init`, and the load-bearing rules mutation-checked rather than trusted:

| mutation | tests that caught it |
|---|---|
| `corrected` carries support | 3 |
| provenance mirror disabled | 8 |
| theorem derives from finite evidence | 2 |
| horizon takes `greatest` not `least` | 2 |

## Known gaps

- **`make check` is broken.** `check-trunkit` runs `tools/kan_in_kan.py`, which is not in
  the repo; 79's header also references `tools/build_<x>.py`, likewise absent, which is why
  every kan `%_laws` view is empty on a fresh install. Pre-existing, untouched here.
- **73 nerode tests skip.** `test_phase1b/1c/2.py` hardcode
  `postgresql://nerode:nerode@localhost:5435/nerode` and ignore `NERODE_TEST_DSN`.
  Deliberately deferred: it is test infrastructure, not part of this fix.
- **T5 does not join the existing Erdős convention.** The ledger identifies problems as
  `subject_kind='erdos_problem'`, `subject_ref={'id': N}`; `prov.artifact.problem` is free
  text. 116's anchor uses the ledger convention, but `prov` does not yet.
- **17 test claims pollute the canonical ledger** (`{'id': 728}`, dated 2026-07-01 to
  2026-07-27), exactly as `conftest.py` warns. Not introduced by this release.
