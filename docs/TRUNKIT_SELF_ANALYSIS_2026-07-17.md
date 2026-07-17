# Trunkit self-analysis — 2026-07-17

_The ledger's accumulated methods turned on the ledger itself: method
census, live re-probes vs recorded standing, new Lean-tower coverage, and
one architectural finding. All numbers measured live against
`trunk-db-trunkit-1` (:5434) this session._

## 1. What has accumulated

**19 methods, 753 claims.** Census highlights (cert.standing):

| Method family | Claims | State |
|---|---|---|
| `lean_gap` | **482 (64% of ledger)** | all `unverified` — honest sorry census of CondensedTEL, exactly matching today's file scan |
| `comp_sql` (workhorse) | 88 | 73 valid, 8 refuted, 5 unverified |
| `nerode_*` (7 methods) | 111 | all valid |
| `formal_external` | 29 | 13 valid, 12 unverified, 1 refuted |
| `cert_kernel`, `struct_kan`, others | ~43 | mixed |

Overall: 225 verified / 11 failed / 515 unknown (STATUS regenerated today).
The ledger's open surface is now dominated by the formalization tower —
which is what a theory-level campaign *should* look like on an honest
board (see `C:\AI-Local\tel\docs\THEORY_LEVEL_AUTOFORMALIZATION_FIT.md`).

## 2. Live probes vs the board: currently truthful, structurally fragile

Re-ran the live probe suite today:

- `tel_build_check.py`: **8/8** tel_project builds valid (cargo/rebar3/mix,
  all against `C:/AI-Local/tel-clean`).
- `tel_behavior_check.py`: **5/5** behavior claims valid (261–263, 265, 266).
- `tel_subject_guard.py`: all 9 build subjects exist (Tier 1).

So the board's ✅s are *currently* corroborated by fresh probes. But see §4.

## 3. New coverage: the Lean tower is now ledger-visible

The theory-level review found the ledger's blindspot: 482 sorries tracked,
but **elaboration health untracked** — and 5 CondensedTEL modules currently
fail elaboration outright (a state *worse than unverified*: their claims
cannot be stated).

Actions taken:

1. `local/tools/tel_build_check.py` extended: `lakefile.lean` → `lake build`
   (the accumulated method learned Lean; one clause, reuses all existing
   gating/board wiring).
2. **Claim 881** registered: "CondensedTEL Lean library elaborates (lake
   build; active tower in C:/AI-Local/tel, tel-clean port pending)" —
   `tel_project`, so it lands in the TEL-builds board area (now 9 claims).
3. Probed: `cert.live_build[881] = failed`, detail naming the failing
   modules (UIPresheaf, AdelicWithAncestry, BooladicStone,
   BooladicMetric_Clean, BooladicHarrison/Projection).
4. `cert.subject_probe[881]` recorded (subject exists).

Note the deliberate path choice: the claim probes the **active working
copy** (`C:/AI-Local/tel`), where the mathlib cache and this week's fixes
live — not tel-clean, whose lean tree is cold and unfixed. The statement
says so explicitly. Repoint when the port happens.

## 4. The architectural finding: two evidence planes that never meet

`cert.standing` (and therefore `cert.board` and STATUS.md) derives status
**exclusively from `cert.certificate`** — the hash-entangled, formally
attested ledger. The live probe tables (`cert.live_build`,
`cert.tel_behavior`, `cert.subject_probe`) are a second, fresher evidence
plane that **never flows into standing**. Consequences observed today:

- Claim 881: probe says `failed`; board says `⬜ not checked` (no
  certificate row exists yet).
- Claims 227–234: board says ✅ on certificate evidence dated 2026-06-21;
  today's probes happen to agree — but a build regression tomorrow would
  leave the ✅ standing until the next formal attestation pass.
- Claims 320/321 (statements *self-describing as refuted*): sitting in the
  attestation backlog without certificates — known-false claims whose
  refutations were never certificated.

This is by design (probes are not certificates; hand-inserting certificate
rows would break ledger-chain entanglement, so none were forged today).
But the board silently *presents* certificate staleness as current truth.

## 5. Attestation backlog (attest_run --dry-run)

16 formal-tier candidates lack current certificates, including: 881 (the
new Lean claim), 320/321 (uncertificated refutations), 160/161 (lost
reports needing re-runs), 388 (the kan no-vacuous-truth self-check), and
several image/sequence match claims. Write mode requires restarting the
MCP server with `TRUNKIT_ALLOW_WRITE=1` (or running `tools/cert_formal.py`
directly under that env).

## 6. Recommendations

1. **Run the formal attestation pass** (`TRUNKIT_ALLOW_WRITE=1`,
   `attest_run dry_run=false`). This certificates the 16-claim backlog —
   flipping 881 to ❌ on the board honestly and finally certificating the
   320/321 refutations.
2. **Make probe/certificate divergence visible**: a `cert.board_live` view
   joining standing with the probe tables, flagging rows where
   `live_build.status` contradicts certificate status and showing probe
   `checked_at` alongside certificate `checked_at`. The board should never
   present June evidence as July truth without saying so.
3. **Differentiate the 482 `lean_gap` claims** by elaboration state: gaps
   in the 5 broken files are *blocked* (unstatable), not merely
   undischarged. A `subject_probe`-style tier per Lean file (elaborates:
   y/n) would let the board show the tower's true shape.
4. **Port to tel-clean**: the lakefile Windows fix + CRNTypes import repair
   (in `C:\AI-Local\tel\lean-formalization`), then repoint claim 881.

## Session provenance

Probes and SQL run live 2026-07-17; census idempotent; one claim inserted
(881); one probe-tool extension (lake support); STATUS.md + STATUS_BOARD
regenerated (225/11/515 of 753). No certificate rows were written — the
attestation pass is deliberately left to write-enabled operation.
