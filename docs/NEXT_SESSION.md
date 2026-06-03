# Trunkit (Trunk) — Next Session TODOs

_Prepared 2026-06-03. Run from `C:/AI-Local/Trunk` (federation/cert ledger, branch
`packaging-honesty-fixes`)._ Companion compiler/language TODOs live in
`C:/AI-Local/tel-clean/docs/NEXT_SESSION.md`.

## Where things stand

The TEL capability board is fully green. Bring the DB up and confirm:
```
docker compose up -d db-trunkit                 # container trunk-db-trunkit-1, port 5434
export TRUNK_DSN=postgresql://trunk:trunk@localhost:5434/trunk
docker exec trunk-db-trunkit-1 psql -U trunk -d trunk -c "SELECT * FROM cert.board_summary WHERE area ILIKE 'TEL%' ORDER BY area"
```
Expected: TEL builds 8/8 · behavior 3/3 · constants 1/1 · graphics 2/2 · results&hygiene 5/5.

TEL capability tooling (all in `tools/`):
- `tel_build_check.py` — Tier-2 live builds (`tel_project`)
- `tel_behavior_check.py` — runs `tel-clean` telc on probe programs; verifies stdout
  + a `produces` PNG artifact (`tel_behavior` + `tel_graphics`)
- `tel_constants_check.py` / `tel_constants_seed.py` — drift-check & source→Postgres
  seeder for `curry.constants` (manifest: `tel_constants_manifest.json`)
- `tel_calx_render.py` / `tel_calx_frames.py` — render calx number theory *in TEL*
- claim SQL: `tel_*_claims.sql`, board areas in `src/calx/sql/93_cert_observability.sql`

## TODOs (prioritized)

1. **Fix the CLI drift (do first).** `trunkit standing` crashes: the pip-installed
   CLI queries `cert.standing.id`, but the schema column is `claim_id`. Either
   `pip install -e .` (align the installed package to the repo) or patch
   `src/calx/cli.py`. Until then, query the board via `psql` directly. This bites
   every session.

2. **Regenerate + commit `STATUS.md`** to reflect the all-green TEL areas:
   `python tools/gen_status.py`. ⚠️ The working tree has pre-existing WIP on
   `packaging-honesty-fixes` (`src/calx/kernel.py`, `src/calx/sql/94_cert_kernel.sql`,
   `tests/kernel_vectors.py`, `97_cert_self_topology.sql`, `98_topological_signature.sql`,
   `docs/TOOL_ON_TOOL_TOPOLOGY.md`) — keep commits scoped to your own files.

3. **Unify the TEL capability sweep.** One runner that executes build + behavior +
   constants + graphics checks and re-attests their claims, so "is TEL green?" is a
   single command (`python tools/tel_capability_sweep.py` or a Make target),
   optionally wired into CI.

4. **Live "TEL renders calx" claim.** The current `tel_graphics` calx claim verifies
   a *committed snapshot* (`examples/calx_divisors.tel`). Add a claim/check that runs
   `tel_calx_render.py` to regenerate from *current* calx and verifies the PNG — so
   the live calx→.tel→telc bridge is what's attested.

5. **More calx-in-TEL visualizations (creative).** Ulam prime spiral, aliquot-orbit
   trajectories from `calx.orbits`, factorization mosaic from `calx.factorizations`;
   or the real-time variant that renders a frame after each `trunkit generate` batch.

6. **Board graphic** (the original pre-pivot ask). A Mermaid-in-markdown or
   self-contained HTML representation of `cert.board_summary` (color-coded areas),
   generated alongside `STATUS.md`.

## Gotchas
- Compose service is **`db-trunkit`** (→ `trunk-db-trunkit-1`), not the old `db`/`trunk-db-1`.
- `curry.constants` schema is `(id, version, value bytea, type_signature, ...)` —
  values are **JSON-encoded** strings (decode before comparing); not the `name`-column
  shape the SKILL doc shows.
- Cross-repo: the TEL checkers shell out to `C:/AI-Local/tel-clean/target/debug/telc`
  with `cwd=repo`, so relative output paths land under tel-clean — keep telc built there.
- `cert.tel_behavior` is shared by both `tel_behavior` and `tel_graphics` claims.
