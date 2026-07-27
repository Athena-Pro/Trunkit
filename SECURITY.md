# Security — threat model & hardening plan

*Status: Phase-1 audit. This is a design + policy document; the hardening
sketches below are **not yet implemented**. Generated 2026-05-30 against the
post-step-95 (immutable ledger) tree.*

> **One-line threat model.** Trunkit's entire security posture turns on a single
> trust boundary: **who may author a `cert.claim.probe_sql`?** A probe is SQL
> that the checker runs with its own privileges. If claim authorship is trusted,
> Trunkit is safe. If it is not, a probe is arbitrary code execution inside the
> PostgreSQL backend — a hole strictly larger, and more direct, than any
> speculative side-channel.

---

## 1. Trust boundaries

| Boundary | Trusted side | Untrusted side | Enforced by |
|---|---|---|---|
| Prover vs consumer | `--write` prover commands | read-only consumer commands | **CLI convention only — NOT the DB** ⚠ |
| Probe authorship | whoever writes `cert.claim` rows | callers of `cert.verify` / bundle consumers | nothing yet ⚠ |
| Ledger integrity | the producing DB | a received `export_bundle` | step-95 hash chain (integrity), **no authenticity yet** ⚠ |
| Kernel witnesses | — | any submitter (`cert.submit_proof`) | `cert_kernel` checks inert **data**, not code ✓ |

The first three rows are the work. The fourth — the `cert_kernel` tier — is the
model for what the others should become: **untrusted input must be data, not
instructions.** That is exactly the Spectre lesson (see §6).

---

## 2. CWE register (prioritized)

### P0 — CWE-89 / CWE-94: stored SQL executed by the checker

`cert.check`, `cert.verify`, and `cert.check_with_witness` run a probe string:

- [`src/calx/sql/40_cert.sql:101`](src/calx/sql/40_cert.sql) — `EXECUTE v_claim.probe_sql INTO v_ok, v_evidence;`
- [`src/calx/sql/86_cert_verify.sql:32`](src/calx/sql/86_cert_verify.sql) — `EXECUTE v_claim.probe_sql INTO v_ok, v_ev;`
- [`src/calx/sql/88_cert_witness_carry.sql:56`](src/calx/sql/88_cert_witness_carry.sql) — `EXECUTE v_claim.probe_sql INTO v_ok, v_ev, v_witness;`

The probe runs with the **full privileges of the role calling the function**.
A malicious probe can `DROP`/`TRUNCATE`, read `curry.*` provenance, call
`pg_read_file()` / `COPY ... PROGRAM`, or tamper with anything that role can
reach. `cert.verify` is documented "safe for untrusted callers" — but that holds
only if the *claim author* was trusted; the consumer re-runs the producer's SQL.

This is the **single most important issue** and is strictly worse than Spectre:
direct, non-speculative, deterministic code execution.

**Current mitigation:** none at the DB layer. Probes are wrapped in a
subtransaction (errors → `error`/`unverified`), which protects *availability of
the verdict* but not *confidentiality or integrity*.

### P0 — CWE-345 / CWE-347: integrity without authenticity

The step-95 chain (`row_hash`, `prev_hash`, `premise_hashes`, `cert.verify_chain`)
proves the ledger **was not edited in place**. It does **not** prove **who**
produced a record: anyone can recompute a fully valid chain from scratch, because
the hash inputs are all public and there is no signature. The ledger is
**tamper-evident, not tamper-proof**, and a received `export_bundle` carries no
proof of origin.

**Current mitigation:** append-only triggers (CWE-915, closed) + `verify_chain`
catch *in-place* mutation, not *forgery* or *substitution* of a whole chain.

### P1 — CWE-862 / missing DB-level authorization

The consumer/prover split is a CLI convention ([`src/calx/cli.py`](src/calx/cli.py)),
not a database grant. Anyone with the DSN can `INSERT cert.claim` and
`SELECT cert.check(...)`. There is no DB role that is "consumer-only".

### P2 — CWE-208: observable timing discrepancy

Hash comparison uses `=` (not constant-time); `cert.verify` re-execution time is
data-dependent. **Irrelevant** for a public ledger of mathematical facts.
Becomes relevant only if the ledger holds confidential content (e.g. embargoed
VEX — see Phase 2). Scope: fix-if/when-sensitive.

### Closed / handled (credit where due)

| CWE | Control | Where |
|---|---|---|
| CWE-915 mutable history | append-only `BEFORE UPDATE/DELETE` triggers | step 95 ([`95_cert_ledger.sql`](local/sql/95_cert_ledger.sql)); revocations are appended events, never mutations (step 100, [`100_cert_lifecycle.sql`](src/calx/sql/100_cert_lifecycle.sql)) |
| CWE-20 improper input validation | three-valued "malformed → `unverified`", never a fake verdict | every `cert.kernel_*` ([`94_cert_kernel.sql`](src/calx/sql/94_cert_kernel.sql), [`calx/kernel.py`](src/calx/kernel.py)) |
| CWE-754 improper check of unusual conditions | empty engine → `unverified` not `refuted`; LEFT JOIN surfaces never-checked claims | steps 40/79 (audited in [`AUDIT.md`](docs/reports/AUDIT.md) §3) |

---

## 3. Hardening design A — probe sandbox (closes CWE-89/94)

Goal: a probe can compute a verdict over `calx`/`curry`/`kan` data and **nothing
else** — no writes, no filesystem, no long runs, no schema reach beyond a fixed
allowlist.

**A1. Dedicated low-privilege role.**
```
-- design sketch, not yet applied
CREATE ROLE cert_probe NOLOGIN;
REVOKE ALL ON ALL TABLES IN SCHEMA cert, curry FROM cert_probe;  -- no provenance writes
GRANT SELECT ON specific calx/kan read views TO cert_probe;      -- allowlist only
REVOKE EXECUTE ON FUNCTION pg_read_file, pg_ls_dir, ... FROM cert_probe;
```

**A2. Run the probe under that role with a pinned search_path and a timeout.**
The three `EXECUTE` sites wrap the probe in:
```
SET LOCAL ROLE cert_probe;
SET LOCAL search_path = calx, kan, pg_catalog;   -- no public, no cert/curry write paths
SET LOCAL statement_timeout = '5s';
SET LOCAL default_transaction_read_only = on;    -- probes never write
-- ... EXECUTE v_claim.probe_sql ... (already in a subtransaction)
RESET ROLE;
```
`SET LOCAL` confines the change to the subtransaction already in place.

**A3. Policy (the durable fix): untrusted facts never become `probe_sql`.**
`comp_sql` / `struct_kan` are **privileged authorship** — only trusted provers
write them. Anything from an untrusted source is submitted as a `cert_kernel`
**data witness** (`cert.submit_proof`) and checked by a fixed in-DB kernel that
takes no caller-supplied code. This is already true of the kernel tier; the doc
makes it a **stated invariant**, and §A1–A2 are defense-in-depth for the
trusted-but-fallible case (a prover that pastes a bad probe).

**A4. Static guard at insert time (optional, cheap).** A `cert.claim` BEFORE
INSERT trigger can reject probes whose text matches a denylist
(`pg_read_file`, `COPY`, `pg_sleep`, DDL verbs) — a tripwire, not a security
boundary (the boundary is A1–A3).

---

## 4. Hardening design B — ledger authenticity (closes CWE-345/347)

Goal: a record proves **who** asserted it and **when**, and a chain cannot be
forged or wholesale-substituted.

> **Shipped (step 100, `100_cert_lifecycle.sql`):** certificate *lifecycle* —
> append-only revocation (`cert.revocation`, `cert.revoke[_claim]`), validity
> windows carried inside the hash-committed `valid_under`
> (`SET trunkit.cert_ttl`), and `signer_id` capture from the `trunkit.signer`
> GUC on certificates and revocations. `signer_id` is an **identity claim in
> the provenance trail, not a cryptographic proof** — B1 below (key-backed
> signatures) remains open.

**B1. Per-record signature.** Add `signer_id TEXT`, `signature BYTEA` to
`cert.certificate`. Sign `row_hash` with Ed25519 at append time (key held by the
prover, *outside* the DB). `cert.verify_chain` gains a signature-verification
pass against a registered public-key table `cert.signer(signer_id, pubkey)`.
Forging a valid chain now requires the signing key, not just public inputs.

**B2. External anchoring (already scaffolded).** `cert.anchor_external` +
`cert.external_anchor` exist (step 95). Add a cadence: periodically publish
`cert.ledger_root()` to an external, independently-timestamped medium — a git tag,
an RFC-3161 TSA, or a Sigstore/transparency log. This bounds how far history can
be rewound even by a key holder, and gives third-party "this root existed at time
T" evidence. Aligns Trunkit with SLSA / in-toto / Sigstore supply-chain norms.

**B3. Bundle carries the proof of origin.** `export_bundle` (v2) already carries
`row_hash`/`prev_hash`/`premise_hashes`/`ledger_root`; extend to carry
`signer_id` + `signature` + the relevant `cert.signer` pubkey, so
`calx.ledger.verify_chain` can check **authenticity offline**, not just integrity.

---

## 5. Phase-1 deliverables checklist

- [x] `SECURITY.md` — this document (threat model + CWE register + designs A/B).
- [ ] Probe-sandbox implementation (design A) — **not started; needs a live DB to test.**
- [x] Certificate lifecycle: revocation, validity windows, signer identity (step 100).
- [ ] Ledger-signature + anchor cadence (design B) — **not started** (signer_id ships without key-backed signatures).
- [ ] `docs/reports/AUDIT.md` §10 "Adversarial / CWE" — tamper tests:
      forge a chain → must fail signature (B1);
      malicious probe (`pg_read_file`) → must be sandboxed/denied (A);
      substitute a whole chain → must fail external anchor (B2).
- [ ] DB-level consumer role (CWE-862) — grant matrix for read-only vs prover.

---

## 6. Spectre — scope statement (what Trunkit does and does not claim)

**Trunkit does not mitigate Spectre and must not claim to.** Spectre
(v1 bounds-bypass CVE-2017-5753; v2 BTI CVE-2017-5715; MDS/Retbleed/Downfall
lineage) is a CPU/microcode/OS/hypervisor-boundary defect. An application on
PostgreSQL + Python sits several layers above where the fix belongs.

Two things *are* in-model:

1. **Spectre as a design lens.** It is the canonical proof that *computation in a
   shared trust domain is not isolation*. That is precisely the principle behind
   the `cert_kernel` tier (a small checker over inert data) and precisely the
   principle the `EXECUTE probe_sql` path violates. Spectre therefore **validates
   the kernel direction and indicts the probe direction** — §3's "untrusted input
   is data, not code" is the same lesson one layer up.

2. **Spectre as a Phase-2 demonstration subject, not a defense.** Trunkit can
   *attest, re-verifiably,* a product's **exploitability status** for a
   Spectre-class CVE (e.g. "not_affected: mitigation present, microcode ≥ X")
   via a `config_present` kernel witness a consumer re-checks. It certifies the
   *VEX claim*, it does not patch the silicon. See Phase 2 (proof-carrying VEX),
   to be built only on the §3–§4 hardened base.

The blunt ordering: **CWE-89 is below-the-waterline of everything Spectre could
do, and it is non-speculative. Fix the probe surface and ledger authenticity
first; everything else (including any vulnerability-tracking application) depends
on it.**

---

## 7. Reporting

This is a research/proof-of-concept system; there is no production deployment or
embargo process. Security-relevant findings: open an issue at
`Athena-Pro/Trunkit`, or for sensitive reports email the maintainer. Do not file
exploit details against the public probe surface until §3 hardening lands —
until then, **assume `cert.claim` authorship equals backend code execution.**
