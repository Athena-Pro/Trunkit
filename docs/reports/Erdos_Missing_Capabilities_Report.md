# Erdos Missing Capabilities Report

This report outlines the missing capabilities in Trunkit that prevent it from being fully compatible with several entries on erdosproblems.com.

Based on our recent review and the goals detailed in `Trunkit_Erdos_AI_Capability_Fit.md`, the following capabilities are currently absent from the Trunkit codebase.

## 1. T2 — Finite combinatorial-object store + property probes (`comp_sql` beyond ℤ)

**Missing functionality:**
Trunkit currently operates entirely on the integer factorization lattice (`calx`). It lacks a schema for fundamental combinatorial structures such as:
* Graphs (vertices/edges)
* Point sets/configurations
* Set systems / hypergraphs

**Impact:**
Without this, Trunkit cannot host or re-check the most novel AI-driven combinatorial counterexamples and constructions, such as:
* Open AI's disproof of the planar unit-distance conjecture.
* AlphaEvolve construction sweeps (#36, #52, #67, #106, #391, #507, #1097).
* Kleitman set-system problems (#447, #487, #497, #498, #505, #1023).

**Required Build:**
We need generic finite-structure tables (e.g. `vertices`, `edges`, `points/coords`, `set_membership`) combined with parameterized property probes like `is_unit_distance_graph`, `chromatic_ge`, and `is_primitive_family`.

## 2. T3 — Numeric-bound / inequality cert tier

**Missing functionality:**
The current `cert` tier is strictly boolean (valid / refuted / unverified). It cannot represent or attest to graded outcomes such as a claimed inequality or an "improved explicit bound."

**Impact:**
This prevents Trunkit from expressing solutions for problems that demand bounded improvements, such as #348, #848, #524, #650, #788, #513, and #349.

**Required Build:**
A new certification tier that records claimed inequalities alongside re-runnable numeric evaluations at finite witness points. This would include a partial order structure for competing bounds.

## 3. T4 — OEIS conjecture→attestation workflow

**Missing functionality:**
While `oeis-match` exists, Trunkit lacks a first-class workflow to natively ingest an OEIS conjecture and attest a closed-form or recurrence match as a claim.

**Impact:**
Live OEIS-related AI discoveries (e.g., #271, #334, #396, #860, #872) require manual translation and cannot be smoothly recorded and attested.

**Required Build:**
A streamlined workflow built on top of `oeis-load` and `oeis-match` to explicitly register conjectures and attest matches as `comp_sql` (finite agreement) or `formal_external`.

## 4. T5 — Solution-provenance DAG

**Missing functionality:**
Trunkit does not currently have a schema to model the DAG of contributors (prior literature, AI output, human steps) and credit structures for a given solution.

**Impact:**
The community's hand-maintained credit structures on wikis cannot be mirrored or attested within Trunkit, missing the opportunity to serve as a comprehensive credit-and-correctness ledger.

**Required Build:**
A new `derivation`-backed schema where nodes represent contributors or artifacts, and edges represent relationships like "used", "formalized", or "corrected". Each node should carry a trust tier mapped to valid/partial/refuted states.
