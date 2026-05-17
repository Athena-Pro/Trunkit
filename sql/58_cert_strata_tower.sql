-- Unified model, step 58: attest the strata tower.
--
-- The graded family W_k(S)=[t:omega(t)=k], B_k(S)=[t:Omega(t)=k] is a tower
-- of orthogonal idempotent endofunctors generalising prime_members (=W_1).
-- Five laws (hash-pinned self-contained checker proofs/strata_tower.py):
--   1  idempotent  : W_k.W_k=W_k, B_k.B_k=B_k;
--   2  orthogonal  : W_j.W_k=[] (j!=k) within each grading;
--   3  complete    : disjoint-union_k W_k(S) = S|{omega>=1} -- the tower
--                     resolves the identity;
--   4  refinement  : omega<=Omega, so the omega-tower is coarser than the
--                     Omega-tower (W_k subset of union_{j>=k} B_j);
--   5  bottom rung : W_1 == prime_members.
-- Canonical fingerprint: omega-strata sizes of naturals 1..120 = [40,66,13].
--
-- Live, kan carries 7 rung functors strata_W1..W3 / strata_B1..B4 over the
-- abstract 'tower' category; views kan.strata_tower / _laws. Driven by
-- tools/cert_formal.py. Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'strata_tower',
       '{"omega_tower":"W_k=[t:omega=k]","Omega_tower":"B_k=[t:Omega=k]",
         "laws":["idempotent","orthogonal","complete","refinement",
                 "bottom_rung=prime_members"],
         "canonical":"omega-strata sizes naturals(120)=[40,66,13]",
         "structure":"complete orthogonal system of idempotent endofunctors"}'::jsonb,
       'the strata tower is a complete system of orthogonal idempotent endofunctors; prime_members is its bottom rung and the omega-tower refines the Omega-tower',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'the strata tower is a complete system of orthogonal idempotent endofunctors; prime_members is its bottom rung and the omega-tower refines the Omega-tower'
);
