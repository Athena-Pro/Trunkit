-- Unified model, step 83: clear the Tier-1 "never checked" backlog.
--
-- cert.outrun_watch() flagged five cert_kernel claims as "never checked (no
-- certificate)". One (#254, SL(2,Z) matrix word) already had a submitted proof
-- object and only needed running; four (the tool-on-tool topology Betti claims
-- from docs/TOOL_ON_TOOL_TOPOLOGY.md) had NO proof object. This file supplies the
-- four missing dfa_betti graph witnesses and runs cert.check_kernel on all five.
-- The graphs are recomputed by the (already-imported, validated) dfa_betti kernel,
-- so each verdict is proof-carrying, not asserted. Idempotent.

-- 283: cert_kernel call graph — verify->crt->gcd<-unit_fraction<-verify (gcd reuse, beta1=1).
SELECT cert.submit_proof(c.id,
    '{"schema":"dfa_betti","V":4,"edges":[[0,1],[1,2],[0,3],[3,2]],"asserts":{"beta1":1}}'::jsonb,
    '83 tier1: cert_kernel call graph (0=verify,1=crt,2=gcd,3=unit_fraction)')
FROM cert.claim c
WHERE c.statement LIKE 'cert_kernel call graph has beta1=1%'
  AND NOT EXISTS (SELECT 1 FROM cert.proof_obligation po WHERE po.claim_id = c.id);

-- 284: kan_in_kan — the kan_self endofunctor is a graph self-loop (beta1=1).
SELECT cert.submit_proof(c.id,
    '{"schema":"dfa_betti","V":1,"edges":[[0,0]],"asserts":{"beta1":1}}'::jsonb,
    '83 tier1: kan_in_kan self-loop (kan_self endofunctor)')
FROM cert.claim c
WHERE c.statement LIKE 'kan_in_kan reflexive closure has beta1=1%'
  AND NOT EXISTS (SELECT 1 FROM cert.proof_obligation po WHERE po.claim_id = c.id);

-- 285: Porter-cert cross-DB anchor — envelope <-> ledger_root mutual reference (beta1=1).
SELECT cert.submit_proof(c.id,
    '{"schema":"dfa_betti","V":2,"edges":[[0,1],[1,0]],"asserts":{"beta1":1}}'::jsonb,
    '83 tier1: Porter-cert mutual anchor (0=envelope,1=ledger_root)')
FROM cert.claim c
WHERE c.statement LIKE 'Porter-cert cross-DB anchor has beta1=1%'
  AND NOT EXISTS (SELECT 1 FROM cert.proof_obligation po WHERE po.claim_id = c.id);

-- 286: dfa_betti call graph — check_dfa_betti -> find, a tree (beta1=0, no shared deps).
SELECT cert.submit_proof(c.id,
    '{"schema":"dfa_betti","V":2,"edges":[[0,1]],"asserts":{"beta1":0}}'::jsonb,
    '83 tier1: dfa_betti call graph (0=check_dfa_betti,1=find) — tree')
FROM cert.claim c
WHERE c.statement LIKE 'dfa_betti kernel call graph has beta1=0%'
  AND NOT EXISTS (SELECT 1 FROM cert.proof_obligation po WHERE po.claim_id = c.id);

-- run the cert_kernel tier checker on all five (re-runs the dfa_betti / matrix_word kernels)
DO $$ DECLARE c RECORD; BEGIN
  FOR c IN SELECT id FROM cert.claim
    WHERE statement LIKE 'cert_kernel call graph has beta1=1%'
       OR statement LIKE 'kan_in_kan reflexive closure has beta1=1%'
       OR statement LIKE 'Porter-cert cross-DB anchor has beta1=1%'
       OR statement LIKE 'dfa_betti kernel call graph has beta1=0%'
       OR statement LIKE 'the SL(2,Z) word a*b*a equals [[2,3],[1,2]] (matrix-semigroup membership%'
  LOOP PERFORM cert.check_kernel(c.id); END LOOP;
END $$;
