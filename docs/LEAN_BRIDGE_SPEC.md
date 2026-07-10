# T1 — Lean verification bridge: implementation spec

*Prepared 2026-06-20; shipped in `trunkit` 0.3.0. Targets the cert pillar. Companion to `docs/reports/Trunkit_Erdos_AI_Capability_Fit.md` (item T1). Grounds: `src/calx/sql/40_cert.sql`, `41_cert_formal.sql`, `tools/cert_formal.py`, `src/calx/cli.py`.*

---

## 0. Correction to the review's framing

The Erdős review claimed `formal_external` "only SHA-pins → stays `unverified`." Reading the code, that is **not** the actual state. The formal tier already:

- has `cert.artifact(claim_id, kind, path, sha256, checker_cmd, …)` where **`kind` already enumerates `lean`** (41_cert_formal.sql:20);
- runs `checker_cmd` via `subprocess` and sets the certificate to **`valid`** (exit 0) / **`refuted`** (non-zero) / `error` (timeout/missing), with a **hash-drift gate** (`cert_formal.py:266–294`);
- records `valid_under = {calx_schema_version, artifact_path, artifact_sha256}` and appends through the same append-only `cert.certificate` + `curry.inferences` path as in-DB probes.

So the generic external-checker mechanism **exists and works** — it is exercised today by ~20 Python proof artifacts (`proofs/*.py`). **The Lean bridge is therefore not a new mechanism; it is a Lean *adapter* plus the Lean-specific correctness gates the generic harness lacks.** That narrows the build and is the right frame for what follows.

---

## 1. Why Lean needs more than "run it, check exit code"

A Python proof artifact is trusted by: hash it, run it, exit 0 ⇒ valid. For Lean that is necessary but **not sufficient**, for five concrete reasons.

| # | Gap | Why a Python-style checker misses it |
|---|-----|--------------------------------------|
| G1 | **Artifact is a project + a declaration, not a file** | A Lean proof is `lakefile.lean` + `lean-toolchain` + a Mathlib dependency + `.lean` sources, and the thing being attested is *one named theorem* in it. Hashing a single `path` neither captures the build closure nor names the target. |
| G2 | **`lake build` exit 0 ≠ proved** | A proof can typecheck and exit 0 while containing `sorry` (Lean emits a *warning*, not an error), or while depending on an unsound custom axiom, or `native_decide`'s `ofReduceBool` trust. The real gate is **`#print axioms <decl>` ⊆ allowed set** and **no `sorryAx`** — not the exit code. |
| G3 | **The claim↔statement binding is unattested** | "Lean theorem `T` builds" is worthless unless `T`'s *type* is the Erdős statement. The checker must emit the pretty-printed signature so the binding is auditable. This residual trust step is irreducible and must be surfaced, not hidden. |
| G4 | **Builds are minutes and execute arbitrary code** | The harness's 60 s timeout (`cert_formal.py:164`) is far too short for Lean+Mathlib, and `shell=True` in `PROJECT_DIR` is fine for *trusted local* Python but unsafe for *externally supplied* Lean — Lean elaboration runs arbitrary code (`#eval`, `native_decide`), so **build = code execution** ⇒ sandbox + no-network is mandatory for AI/third-party artifacts. |
| G5 | **Reproducibility pin is integer-schema-shaped, not toolchain-shaped** | `valid_under` records `calx_schema_version`; a Lean attestation must instead pin `lean-toolchain`, the Mathlib revision from `lake-manifest.json`, and the per-file digests, or it isn't reproducible. |

Everything below exists to close G1–G5 **without** disturbing the working Python path.

---

## 2. Design

Three new, **purely additive** artifacts plus one harness branch:

```
src/calx/sql/41a_cert_formal_lean.sql   -- ALTER cert.artifact (+cols); register_lean_artifact()
tools/AxiomAudit.lean                 -- meta-program: axioms + signature + sorry check → JSON
tools/lean_check.sh                   -- driver: lake build → run auditor → emit JSON, exit code = verdict
tools/cert_formal.py                    -- ADD a kind=='lean' branch (Python path untouched)
src/calx/cli.py                         -- ADD `trunkit register-lean` (attest already dispatches)
```

### 2.1 Schema (G1, G5) — `41a_cert_formal_lean.sql`

Additive `ALTER … ADD COLUMN IF NOT EXISTS` on `cert.artifact` (keeps every existing row/working path valid):

- `project_root TEXT` — repo-relative Lake project dir (e.g. `proofs/erdos728`).
- `target_decl TEXT` — the attested declaration (e.g. `Erdos728.main`).
- `file_digests JSONB` — `{relpath: hex_sha256}` over the **declared build-closure file set** (lakefile, `lean-toolchain`, `lake-manifest.json`, the proof `.lean`s).
- `toolchain JSONB` — `{lean: "leanprover/lean4:v4.x", mathlib_rev: "<commit>", lake: "x.y"}`.

The existing scalar `sha256` holds, for `kind='lean'`, a **closure digest** over that map — so the drift gate in `cert_formal.py` keeps working unchanged: one trusted digest, recomputed the same way. **Hashing stays in Python** (the harness already uses `hashlib`; this codebase has no SQL-side hashing, so the migration adds none and needs no `pgcrypto`). Single-source-of-truth recipe, implemented in `cli.py` and re-used by the harness:

```
lines := sorted("<relpath>:<hex_sha256>" for each file in the closure)
closure_digest := sha256("\n".join(lines))
```

Helper `cert.register_lean_artifact(claim_id, project_root, target_decl, file_digests, toolchain, closure_digest, checker_cmd)` wraps `cert.register_artifact` with `kind='lean'`, `path := project_root`, `sha256 := closure_digest`. Idempotent, same `ON CONFLICT (claim_id)` upsert as the base.

### 2.2 Lean auditor (G2, G3) — `tools/AxiomAudit.lean`

A tiny meta-program, run via `lake env lean --run`, that for `target_decl`:

1. resolves the decl (missing ⇒ exit 2);
2. collects its axiom set via the same machinery as `#print axioms`;
3. emits JSON: `{ "decl": "...", "type": "<pretty-printed signature>", "axioms": ["propext","Classical.choice","Quot.sound"], "uses_sorry": false }`;
4. **exit non-zero iff** `uses_sorry` **or** `axioms ⊄ ALLOWED`. `ALLOWED` defaults to the three Mathlib-trusted axioms; `native_decide`'s `Lean.ofReduceBool` is **excluded by default** (configurable via env, recorded in evidence).

The printed `type` is what makes G3 auditable: it travels into the certificate so a consumer can confirm the Lean statement is a faithful formalization of the Erdős problem.

### 2.3 Driver (G4) — `tools/lean_check.sh`

`lean_check.sh <project_root> <target_decl>`:

1. `cd <project_root>`; `lake exe cache get` (Mathlib oleans from cache — **no build-from-source, no network during attest**); `lake build <module>` with `LEAN_CHECKER_TIMEOUT` (default 1200 s);
2. on build failure ⇒ exit 1 (harness ⇒ `refuted`);
3. else `lake env lean --run ../../tools/AxiomAudit.lean <target_decl>` and print its JSON to stdout;
4. exit code = auditor's exit code.

Sandboxing is a **deployment wrapper**, not script logic: run the driver under `firejail --net=none --read-only=<root>` (or a rootless container) for untrusted artifacts. Local first-party proofs may skip it, exactly as the Python path does today. State this split explicitly in `SECURITY.md`.

### 2.4 Harness branch (G2 evidence) — `tools/cert_formal.py`

Add **one branch** keyed on `artifact.kind` inside the existing run loop, leaving the Python path byte-for-byte unchanged:

- when `kind == 'lean'`: after the existing drift gate passes, set `checker_cmd` (already stored per-artifact) and run it with the longer `LEAN_CHECKER_TIMEOUT`; **parse stdout as JSON**; set
  - `status = 'valid'` iff exit 0 **and** JSON `uses_sorry == false` **and** `axioms ⊆ allowed`;
  - else `status = 'refuted'` with the reason;
- merge the auditor JSON (`axioms`, `type`, `mathlib_rev`, `lean_toolchain`) into `evidence`, and the toolchain pin into `valid_under` (alongside the existing `artifact_sha256`).

Because `cert.certificate.status`/`evidence`/`valid_under` are already free-form (`40_cert.sql:43–51`), **no certificate-schema change is needed** — the Lean evidence is just richer JSON.

### 2.5 CLI

- `trunkit register-lean <claim_id> --root proofs/erdos728 --decl Erdos728.main` → computes `file_digests`/`toolchain`, calls `cert.register_lean_artifact`.
- `trunkit attest [--write]` is unchanged — it already loads `cert_formal` and iterates all `claim_kind='formal'` claims; the new branch handles Lean transparently.

---

## 3. Worked example — Erdős #728

The fully-AI Lean solution (Aristotle/Harmonic; writeup arXiv:2601.07421) is the canonical first target.

```bash
# 1. claim (formal tier; probe_sql NULL ⇒ harness-driven, per 41_cert_formal.sql:82)
psql "$CALX_DSN" -c "INSERT INTO cert.claim
  (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
  VALUES ('erdos_problem', '{\"id\":728}', 'Erdős #728: <faithful statement>',
          'formal', 'formal_external', NULL);"

# 2. drop the Lean project at proofs/erdos728/ (lakefile + lean-toolchain + sources),
#    then register it (computes file_digests + toolchain pin)
trunkit register-lean <claim_id> --root proofs/erdos728 --decl Erdos728.main

# 3. attest: lake build → AxiomAudit → certificate
trunkit attest --write
#   [OK ] claim N seq1 -> valid  (lean: proofs/erdos728)
#         axioms=[propext,Classical.choice,Quot.sound] sorry=false
#         type="∀ … (the Erdős #728 statement)"

# 4. the bundle now carries the toolchain pin; a consumer re-verifies with the
#    SAME lean-toolchain + mathlib_rev — reproducible, producer-untrusted.
trunkit export <claim_id>
```

A `sorry`-laden or custom-axiom "proof" of the same claim ⇒ `refuted`, not silently `valid` — which is the entire point of G2.

---

## 4. Test plan

Add a `lean`-marked suite (mirroring the existing `pytest -m network` convention) over a 4-case fixture project so CI without an `elan` toolchain skips cleanly:

1. **clean proof** of a trivial lemma ⇒ `valid`, axioms ⊆ allowed.
2. **`sorry` proof** ⇒ `refuted` (`uses_sorry`), even though `lake build` exits 0 — the regression guard for G2.
3. **custom-axiom proof** (`axiom evil : False`) ⇒ `refuted` (axiom ⊄ allowed).
4. **hash-drifted file** (touch a source after register) ⇒ `refuted` by the existing drift gate, before any build.

Pure-Python unit tests (no toolchain) for: the axiom-gate set logic, `digest_of(file_digests)` canonicalization, and the JSON-evidence merge. CI lane caches `~/.elan` + `.lake`.

---

## 5. Honesty boundary (unchanged Trunkit stance)

The bridge does **not** put Lean in the database; the checker stays external — consistent with the formal tier's epistemic model (trust root = independent artifact + checker, not calx). Two residual trust assumptions remain and must be stated on the claim, not hidden:

1. **Statement faithfulness (G3):** Trunkit attests "decl of *this printed type* builds with *these axioms*." That the printed type faithfully formalizes Erdős #728 is a human judgement; the certificate records the type so the judgement is *auditable*, not eliminated.
2. **Toolchain trust:** `valid` means "valid under the pinned `lean-toolchain` + Mathlib rev." A consumer re-verifying under a different toolchain may get `unverified` until they match the pin — the same three-valued honesty as everywhere else.

---

## 6. Build order

1. `41a_cert_formal_lean.sql` (additive ALTER + helper) — zero risk to existing rows.
2. `AxiomAudit.lean` + `lean_check.sh` + the 4-case fixture — testable in isolation, no DB.
3. `cert_formal.py` Lean branch + `register-lean` CLI — the only edit to working code; gated behind `kind=='lean'`.
4. `#728` worked example as the acceptance test; then batch-register the §2(b) backlog (#205, #397, #457, #1026, #1051, …).

Net new code is small because the certificate ledger, drift gate, provenance, and bundle export are all reused. The Lean-specific surface is the auditor, the driver, four columns, and one harness branch.
