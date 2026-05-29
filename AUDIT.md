# Trunkit Federation — Independent Audit Worksheet

**Target:** `Athena-Pro/Trunkit` @ tag `v0.2.4` + the live `trunk` Postgres federation
**Scope date:** 2026-05-29
**Auditor:** ________________________  **Date performed:** ____________

> **Independence principle.** Do not trust this worksheet's "Expected" column,
> the `cert.standing` view, or any prose claim. Each procedure is runnable; where
> possible, **recompute the underlying fact from primitives** and compare. Record
> what *you* observe in "Actual" and mark Pass/Fail yourself. A green ledger that
> you cannot independently reproduce is a finding, not a pass.

---

## 0. Environment setup

| # | Step | Command |
|---|---|---|
| 0.1 | Check out the pinned release | `git fetch --tags && git checkout v0.2.4` |
| 0.2 | Confirm the tree is clean & version | `git status --short` → empty; `grep '^version' pyproject.toml` → `0.2.4` |
| 0.3 | Bring up the DB | `docker compose up -d db-trunkit` |
| 0.4 | Confirm container + connectivity | `docker exec -i trunkit-db-trunkit-1 psql -U trunk -d trunk -c "select 1"` → `1` |

DSN for reference: `******localhost:5434/trunk`.
All SQL below runs as: `docker exec -i trunk-db-1 psql -U trunk -d trunk -c "<SQL>"`.

> **Caveat:** the federation DB is mutable (claims get re-checked, engines get
> populated). Reference values are as of v0.2.4 / 2026-05-29. If your counts
> differ, that is not automatically a fail — but every *deviation* must be
> explained by a state change you can point to. The **invariants** (§2, §3, §6)
> must hold regardless of counts.

---

## 1. Repository provenance (the three fixes under audit)

| # | Objective | Command | Expected | Actual | P/F |
|---|---|---|---|---|---|
| 1.1 | `cert.standing` uses LEFT JOIN (never-checked claims surface) | `grep -n "LEFT JOIN cert.certificate" src/calx/sql/40_cert.sql` | 1 match | | |
| 1.2 | Step-79 guard present (empty ≠ refuted) | `grep -n "v_empty\|v_violated" src/calx/sql/79_cert_kan_engines.sql` | ≥3 matches | | |
| 1.3 | Step-90 verifiers present | `ls src/calx/sql/90_cert_equip_probes.sql && grep -c "law_view_holds\|is_perfect" src/calx/sql/90_cert_equip_probes.sql` | file exists, ≥2 | | |
| 1.4 | Release history | `git log --oneline -6` | shows v0.2.4, equip, empty-engine guard, v0.2.3, cert.standing | | |
| 1.5 | No probe touches `COALESCE(...,FALSE)` collapse | `grep -rn "COALESCE(v_rowok, FALSE)" src/calx/sql/` | **0 matches** (the bug is gone) | | |

---

## 2. Ledger state (reproduce, then question it)

| # | Objective | Command (SQL) | Expected (ref) | Actual | P/F |
|---|---|---|---|---|---|
| 2.1 | Standing breakdown | `SELECT status, count(*) FROM cert.standing GROUP BY status ORDER BY 2 DESC;` | valid 154, unverified 14, refuted 8, pass 1, error 1 | | |
| 2.2 | Every claim appears in standing (LEFT JOIN works) | `SELECT (SELECT count(*) FROM cert.claim) = (SELECT count(*) FROM cert.standing);` | `t` | | |
| 2.3 | No claim is silently absent | `SELECT count(*) FROM cert.claim c LEFT JOIN cert.standing s ON s.claim_id=c.id WHERE s.claim_id IS NULL;` | `0` | | |

---

## 3. Contradiction soundness — **the core invariant**

The central claim under audit: *every `refuted` is a genuine violation; no `refuted`
is manufactured by an empty/unpopulated engine.*

| # | Objective | Command (SQL) | Expected | Actual | P/F |
|---|---|---|---|---|---|
| 3.1 | Enumerate refutations + evidence | `SELECT s.claim_id, cl.subject_kind, left(s.evidence::text,80) FROM cert.standing s JOIN cert.claim cl ON cl.id=s.claim_id WHERE s.status='refuted' ORDER BY 1;` | ~8 rows, each with a concrete defect (NaN/Inf/negative/wrong-count) | | |
| 3.2 | Engine bridge is NOT refuted on emptiness | `SELECT ok, evidence->>'violations' v, evidence->>'engines_empty' e FROM cert.kan_engines_all_true();` | `ok` is NULL or TRUE, `violations=0` | | |
| 3.3 | Soundness regression guard (claim 238) | `SELECT status FROM cert.standing WHERE claim_id=238;` | `valid` | | |
| 3.4 | **Adversarial:** force an empty engine, confirm it reports *unverified*, not *refuted* | see §3-ADV below | unverified | | |

**§3-ADV (tamper test — do in a throwaway transaction, ROLLBACK after):**
```sql
BEGIN;
CREATE OR REPLACE VIEW kan.audit_probe_laws AS SELECT NULL::boolean AS some_law WHERE false;
SELECT ok, evidence FROM cert.law_view_holds('audit_probe_laws');  -- expect ok = NULL (unverified)
ROLLBACK;
```
A return of `ok = FALSE` here would mean the empty≠refuted guard is broken → **fail**.

---

## 4. Verifier correctness (don't trust the functions — probe them)

| # | Objective | Command (SQL) | Expected | Actual | P/F |
|---|---|---|---|---|---|
| 4.1 | `is_perfect(28)` true | `SELECT ok, evidence FROM cert.is_perfect(28);` | `t`, aliquot_sum 28 | | |
| 4.2 | **Negative control:** `is_perfect(12)` false | `SELECT ok FROM cert.is_perfect(12);` | `f` (12's aliquot sum = 16) | | |
| 4.3 | **Negative control:** `is_perfect(6)` true | `SELECT ok FROM cert.is_perfect(6);` | `t` | | |
| 4.4 | Independent recompute (outside the DB) | `python -c "n=28;print(sum(d for d in range(1,n) if n%d==0)==n)"` | `True` | | |
| 4.5 | `law_view_holds` on a populated engine | `SELECT ok FROM cert.law_view_holds('strata_tower_laws');` | `t` | | |
| 4.6 | `law_view_holds` on a nonexistent view | `SELECT ok, evidence->>'error' FROM cert.law_view_holds('does_not_exist_laws');` | NULL + error string | | |

---

## 5. Formal tier (hash-pinned artifacts + drift detection)

| # | Objective | Command | Expected | Actual | P/F |
|---|---|---|---|---|---|
| 5.1 | Pinned artifacts exist | `SELECT claim_id, left(sha256,12), path FROM cert.artifact ORDER BY claim_id;` | rows for claims 7, 8, 11, 31 | | |
| 5.2 | Re-run harness is idempotent & re-verifies | `CALX_DSN=... python tools/cert_formal.py` | claims 7,8,11,31 → valid; ~16 `[ERR artifact missing]` | | |
| 5.3 | **Tamper test:** mutate a proof, confirm drift caught | append a comment to `proofs/perfect_28.py`, re-run 5.2 | hash mismatch flagged (NOT silently valid); **restore the file after** | | |
| 5.4 | Missing-checker manifest is honest | from 5.2 output, list the `[ERR artifact missing]` files | matches absent `proofs/*.py` (9,10,13 unbacked) | | |

---

## 6. Engine population (claims outrun data → now partially closed)

| # | Objective | Command (SQL) | Expected | Actual | P/F |
|---|---|---|---|---|---|
| 6.1 | Engines populated | `SELECT evidence->>'engines_checked' checked, evidence->>'engines_empty' empty FROM cert.kan_engines_all_true();` | checked 10, empty 4 | | |
| 6.2 | Base data loaded | `SELECT (SELECT count(*) FROM calx.sequences), (SELECT count(*) FROM kan.sequence_terms);` | ~29, ~1611 (>0) | | |
| 6.3 | Empty engines report unverified, not refuted | `SELECT cl.subject_kind, s.status FROM cert.standing s JOIN cert.claim cl ON cl.id=s.claim_id WHERE cl.subject_kind IN ('grading','lithon','identity_decomposition');` | all `unverified` | | |
| 6.4 | Known build failures reproducible | `python tools/build_grading.py` ; `python tools/build_lithon.py` | grading: FK `category seq`; lithon: `ModuleNotFoundError: core` | | |

---

## 7. Append-only provenance (cert is a ledger, not a mutable cell)

| # | Objective | Command (SQL) | Expected | Actual | P/F |
|---|---|---|---|---|---|
| 7.1 | History retained across status flips | `SELECT claim_id, seq, status FROM cert.certificate WHERE claim_id=234 ORDER BY seq;` | ≥2 rows (sidecar: refuted→valid both kept) | | |
| 7.2 | Re-check appends, never mutates | run `SELECT cert.check(7);` twice; `SELECT count(*) FROM cert.certificate WHERE claim_id=7;` | count **increases** by 1 each run | | |
| 7.3 | Session attestations present | `SELECT id, subject_kind FROM cert.claim WHERE id IN (235,236,237,238,239,240);` | 6 rows (repo_layout/trunkit_method/cert_soundness) | | |

---

## 8. Independent end-to-end recomputation (trust nothing)

Pick **3 `valid` comp_sql claims at random** and, for each, read its `probe_sql`,
run that SQL yourself, and confirm `ok = TRUE` independently of `cert.standing`:
```sql
SELECT id, probe_sql FROM cert.claim WHERE id = <random valid id>;
-- then paste and run the probe_sql; confirm ok = true
```
Pick **2 `refuted` claims** and confirm the defect is real (e.g. open the cited
experiment file, or recompute the statistic) — not a stale/aspirational threshold.

| Claim id | Tier | Independent result | Matches ledger? |
|---|---|---|---|
| | | | |
| | | | |
| | | | |
| (refuted) | | | |
| (refuted) | | | |

---

## 9. Findings & sign-off

**Counts:** Pass ____ / Fail ____ / N/A ____  out of the procedures above.

**Material findings (any Fail, or any green you could not independently reproduce):**
1. ________________________________________________________________
2. ________________________________________________________________
3. ________________________________________________________________

**Known-accepted gaps (already self-reported by the system — not findings):**
- 4/14 kan engines unpopulated (grading, lithon, identity_decomposition + 1); honestly `unverified`.
- 3 formal claims (9, 10, 13) lack `proofs/*.py` checkers; honestly `unverified`.
- 8 genuine `refuted` data-quality contradictions in external experiment files (Feigenbaum/MDL/BIC) — *expected* to be red.

**Auditor opinion** (circle): SOUND / SOUND-WITH-EXCEPTIONS / UNSOUND

**Signature:** ____________________________   **Date:** ____________

---
*Generated 2026-05-29 against Trunkit v0.2.4. Reference counts reflect the live
federation DB at that time; the soundness invariants (§3, §4.2, §5.3, §6.3) are
state-independent and must hold for any honest snapshot.*
