-- Unified model, step 98: tool-on-tool topological signature.
--
-- Four structural claims derived from TOOL_ON_TOOL_TOPOLOGY.md (2026-06-01).
-- Each points the dfa_betti kernel (step 94) at one of Trunkit's own internal
-- constructions and records the Betti signature as a re-verifiable claim.
--
-- Topology recap (1-complex, undirected):
--   beta0 = connected components
--   beta1 = E - V + beta0  (independent cycles / circuit rank)
--   chi   = V - E = beta0 - beta1
--
-- All four claims carry method='cert_kernel' and probe_sql=NULL:
-- the producer submits a dfa_betti proof object via cert.submit_proof();
-- the independent kernel re-checks it. No SQL probe runs at check time.
--
-- Idempotent (ON CONFLICT DO NOTHING). Requires step 94 (cert.kernel_dfa_betti).

-- ---------------------------------------------------------------------------
-- 1. cert_kernel call graph: beta1=1 (shared gcd reuse cycle)
--
-- The dispatch looks like a tree (verify → 5 kernels → beta1=0), but gcd is
-- shared by check_crt AND check_unit_fraction, closing the cycle:
--   verify → check_crt → gcd ← check_unit_fraction ← verify
-- V=7, E=7, beta0=1, beta1=1, chi=0.
-- Modeling it with private gcds gives beta1=0, beta0=2: the single Betti bit
-- IS the DRY-factoring. dfa_betti itself has no shared deps (see claim 4).
-- ---------------------------------------------------------------------------
INSERT INTO cert.claim
    (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
VALUES (
    'trunkit_graph',
    '{"graph": "cert_kernel_call_graph",
      "construction": "shared_gcd_reuse",
      "V": 7, "E": 7,
      "beta0": 1, "beta1": 1, "chi": 0}'::jsonb,
    'cert_kernel call graph has beta1=1: gcd shared between check_crt and check_unit_fraction closes one independent cycle (V=7, E=7, chi=0)',
    'structural',
    'cert_kernel',
    NULL
)
ON CONFLICT (statement) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 2. kan_in_kan reflexive closure: beta1=1 (kan_self endofunctor self-loop)
--
-- kan.sync_category('kan') reflects kan's own FK graph back into kan,
-- producing the kan_self endofunctor — a graph self-loop on one vertex.
-- V=1, E=1, beta0=1, beta1=1, chi=0.
-- Claim #3 certifies kan_self is an identity endofunctor (algebraic property);
-- this claim certifies its topological signature (structural property).
-- ---------------------------------------------------------------------------
INSERT INTO cert.claim
    (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
VALUES (
    'trunkit_graph',
    '{"graph": "kan_in_kan_closure",
      "construction": "kan_self_endofunctor",
      "V": 1, "E": 1,
      "beta0": 1, "beta1": 1, "chi": 0}'::jsonb,
    'kan_in_kan reflexive closure has beta1=1: the kan_self endofunctor is a graph self-loop (V=1, E=1, chi=0)',
    'structural',
    'cert_kernel',
    NULL
)
ON CONFLICT (statement) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 3. Porter-cert cross-DB anchor: beta1=1 (mutual reference cycle)
--
-- Porter envelope embeds cert.ledger_root(); cert records the envelope hash
-- via cert.anchor_external — a mutual reference spanning both databases.
-- V=2, E=2, beta0=1, beta1=1, chi=0.
-- This is the same minimal-cycle signature (V=1,E=1 for a self-loop; V=2,E=2
-- for a two-node mutual ref) shared by every self-referential construction
-- in Trunkit: self-reference is always exactly one topological loop.
-- ---------------------------------------------------------------------------
INSERT INTO cert.claim
    (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
VALUES (
    'trunkit_graph',
    '{"graph": "cross_db_anchor",
      "construction": "porter_cert_mutual_ref",
      "nodes": ["porter_envelope", "cert_ledger_root"],
      "V": 2, "E": 2,
      "beta0": 1, "beta1": 1, "chi": 0}'::jsonb,
    'Porter-cert cross-DB anchor has beta1=1: envelope embeds ledger_root and cert records the envelope hash (V=2, E=2, chi=0)',
    'structural',
    'cert_kernel',
    NULL
)
ON CONFLICT (statement) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 4. dfa_betti kernel call graph: beta1=0 (no shared dependencies, pure tree)
--
-- dfa_betti is the newest kernel, imported from LQLE. Unlike check_crt and
-- check_unit_fraction (which share gcd), dfa_betti has no shared dependencies:
-- its call graph is a tree (beta1=0). This is the topological analogue of
-- "most self-contained" — and the only kernel for which the tool-on-tool audit
-- is genuinely recursive (dfa_betti measuring itself measures a tree).
-- ---------------------------------------------------------------------------
INSERT INTO cert.claim
    (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
VALUES (
    'trunkit_graph',
    '{"graph": "dfa_betti_call_graph",
      "construction": "kernel_self_contained",
      "beta0": 1, "beta1": 0}'::jsonb,
    'dfa_betti kernel call graph has beta1=0: no shared dependencies, topologically a tree (the most self-contained kernel)',
    'structural',
    'cert_kernel',
    NULL
)
ON CONFLICT (statement) DO NOTHING;
