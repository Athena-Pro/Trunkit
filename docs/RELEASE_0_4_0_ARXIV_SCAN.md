# Pre-release arXiv scan — the 0.4.0 verification surface

*2026-07-02. Quick fit-scan (abstracts) before cutting 0.4.0 (vacuity meta-probe, MCP
cert-family verifiers, universal method kernels, crypto tier, Scott engine, schema-slot
expansion). Lens: does anything supersede our design, reveal a missing capability that
should block release, or demand a hardening pass on the newly-shipped MCP surface?*

## Verdict: ship.

Nothing supersedes any 0.4.0 component; four independent lines of work validate the
release's core bets, one same-week paper is a natural companion for `quote_carry`, and
the MCP-security literature yields a short post-release hardening list — none of it
blocking, since Trunkit's MCP surface already has the properties those papers ask for.

## What validates the release

- **The crypto tier implements a paper that is still a proposal.** Gabbay's
  arithmetisation ([2606.23768](https://arxiv.org/abs/2606.23768), 2026-06-22, v1 —
  no revisions since) explicitly "outlines the approach at a high level" and "notes the
  deployment questions that a full implementation must answer." Trunkit's
  `97_cert_crypto` + `calx.arith` (+ the `arith_verify` MCP tool with its allowlist
  JSON codec) is a working, tested, consumer-re-verifiable implementation of the core
  translation — worth saying plainly in the release notes.
- **The "LLM proposes, deterministic verifier decides" pattern is being independently
  reinvented.** PCN-Rec ([2601.09771](https://arxiv.org/abs/2601.09771)) has an LLM emit
  a structured JSON certificate and accepts only what a deterministic verifier recomputes
  — exactly the `csp_carry`/`arith_check` verify-easy frame, applied to recommender
  governance. Proof-Carrying Agent Actions ([2606.04104](https://arxiv.org/abs/2606.04104))
  wraps agent actions in portable certificate envelopes with replay-ready proof — the
  Porter envelope + cert anchor shape. Convergent evolution, not competition.
- **Our Lean bridge matches the published trust-boundary shape.** Proof-Carrying
  Certificates for LLM Pipelines ([2605.16407](https://arxiv.org/abs/2605.16407)) defines
  certificate validity as a Lean 4 kernel type-check plus a sorry-free transitive axiom
  audit against a pinned trusted set — precisely the `lean_check.sh` + `AxiomAudit.lean`
  + closure-digest design of `41a`/`formal_external`.
- **`quote_carry`'s premise is now a measured crisis.** A same-week large-scale audit
  ([2605.07723](https://arxiv.org/abs/2605.07723)) conservatively counts ~147k hallucinated
  citations in 2025 papers; Phantom References / RefChecker
  ([2607.00738](https://arxiv.org/abs/2607.00738), published 2026-07-01) finds ~1 in 20
  NeurIPS/USENIX-Security papers carrying fabricated references, and GhostCite / CiteAudit
  ([2602.06718](https://arxiv.org/abs/2602.06718),
  [2602.23452](https://arxiv.org/abs/2602.23452)) build resolution pipelines. All of them
  verify reference *existence/metadata*; `quote_carry` grounds the *content at an exact
  span* by hash — the complementary, stronger primitive none of them has. Positioning:
  RefChecker-style resolution finds the document; `quote_carry` pins what it says.
- **Tamper-evident Merkle logs are becoming table stakes for agent infrastructure**
  (MemLineage [2605.14421](https://arxiv.org/abs/2605.14421), Aegon
  [2604.06693](https://arxiv.org/abs/2604.06693), OCELOT
  [2606.12341](https://arxiv.org/abs/2606.12341)) — the hash-chained ledger +
  holographic commitments (96) sit squarely on trend.

## Post-release hardening list (from the MCP-security cluster)

The literature is loud here — tool poisoning as the top client-side vector
([2603.22489](https://arxiv.org/abs/2603.22489)), the MCP-38 taxonomy
([2603.18063](https://arxiv.org/abs/2603.18063)), "no single defense covers more than
34% of threats" (MCPSHIELD, [2604.05969](https://arxiv.org/abs/2604.05969)), and 106
taint-style 0-days across 39,884 real MCP servers (VIPER-MCP,
[2605.21392](https://arxiv.org/abs/2605.21392)). Checked against `trunkit_mcp`: static
tool metadata (no dynamic descriptions to poison), typed parameters, no shell/filesystem
sinks reachable from tool input, prover tools deny-by-default behind
`TRUNKIT_ALLOW_WRITE`, and the arith decoder is allowlist-only. No blocker. Post-release:

- **`bundle_verify` artifact-path probing.** Artifact sha256 checks resolve bundle-supplied
  paths against the local filesystem (base_dir `.`), so a hostile bundle can probe file
  existence/content-hash equality. Low severity (read-only, hash-match oracle); constrain
  to a declared artifact root or make path resolution opt-in.
- **Merkle odd-tail duplication caveat.** `cert.merkle_root` duplicates the odd tail
  (Bitcoin-style), so `[a,b,c]` and `[a,b,c,c]` share a root. Harmless for the fixed
  five-leaf claim commitment; for variable-length traces, record the leaf count alongside
  the root (the MCP tool already returns `leaf_count`) or adopt RFC-6962 domain-separated
  hashing if interop with CT-style logs ever matters.
- **Optional: attested admission.** [2605.24248](https://arxiv.org/abs/2605.24248) proposes
  signed, well-known clearance documents + per-server tool allowlists for MCP servers.
  If trunkit-mcp is ever deployed beyond localhost, publishing such a clearance doc is
  cheap alignment with where client-side policy is heading.

## Idea worth stealing (later)

- **Maximal Certifiable Residue** ([2605.16407](https://arxiv.org/abs/2605.16407)): on
  partial verification failure, return the maximum-weight *certifiable subset* with
  dropped claims audit-logged, instead of a single aggregate verdict. A natural future
  refinement for `bundle_verify` on mixed bundles.

## Bottom line

0.4.0's verification surface is at or ahead of the frontier everywhere it plays: the
crypto tier implements a three-week-old proposal, the method kernels answer a
citation-integrity problem the field just finished measuring, and the MCP server already
has the structural properties the security literature demands. **Cut the release;**
schedule the two hardening items as ordinary post-release work.

Sources: arXiv [2606.23768](https://arxiv.org/abs/2606.23768),
[2601.09771](https://arxiv.org/abs/2601.09771), [2606.04104](https://arxiv.org/abs/2606.04104),
[2605.16407](https://arxiv.org/abs/2605.16407), [2607.00738](https://arxiv.org/abs/2607.00738),
[2605.07723](https://arxiv.org/abs/2605.07723), [2602.06718](https://arxiv.org/abs/2602.06718),
[2602.23452](https://arxiv.org/abs/2602.23452), [2605.14421](https://arxiv.org/abs/2605.14421),
[2604.06693](https://arxiv.org/abs/2604.06693), [2606.12341](https://arxiv.org/abs/2606.12341),
[2603.22489](https://arxiv.org/abs/2603.22489), [2603.18063](https://arxiv.org/abs/2603.18063),
[2604.05969](https://arxiv.org/abs/2604.05969), [2605.21392](https://arxiv.org/abs/2605.21392),
[2605.24248](https://arxiv.org/abs/2605.24248).
