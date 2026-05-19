-- Unified model, step 64: attest the omega x Omega bigrading (horizontal).
--
-- Unifies the omega-tower and Omega-tower into one bigraded decomposition
-- M_{i,j}=[t:omega=i,Omega=j]. Five laws (hash-pinned self-contained
-- checker proofs/bigrading.py):
--   L1 commuting idempotents  W_i.B_j == B_j.W_i == M_{i,j};
--   L2 marginals              (+)_j M = W_i ,  (+)_i M = B_j
--                             (both towers are the bigrading's marginals);
--   L3 triangular support     M_{i,j}=empty unless i<=j (omega<=Omega),
--                             units only at (0,0) -- the Mobius-invertible
--                             incidence poset;
--   L4 full identity          (+)_{(i,j)} M_{i,j} = Id_seq (natural);
--   L5 Mobius / inclusion-exclusion :
--        chain:  B_j = zeta_{<=j} (-) zeta_{<=j-1};
--        excess: E_d=(+)_i M_{i,i+d} is a third full Id decomposition,
--                E_0 = squarefree principal idempotent (omega=Omega).
-- Canonical: naturals(120) joint support = 15 strata (sha 8046b361bb9b8007),
-- sizes summing to 120.
--
-- Live: kan carries M_bigrading / E_excess / zeta_Omega functors and tables
-- kan.bigrading[_support] (views *_summary/_triangular/_laws). Driven by
-- tools/cert_formal.py. Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'bigrading',
       '{"joint":"M_{i,j}=[t:omega=i,Omega=j]",
         "towers_as_marginals":["W_i=(+)_j M","B_j=(+)_i M"],
         "support":"triangular i<=j (incidence poset)",
         "mobius":["chain B_j=zeta<=j - zeta<=j-1",
                   "excess E_d=(+)_i M_{i,i+d}","E_0=squarefree(omega=Omega)"],
         "laws":["L1_commuting","L2_marginals","L3_triangular",
                 "L4_full_identity","L5_mobius"],
         "canonical":"naturals(120) support=15 strata, sum=120"}'::jsonb,
       'the omega x Omega bigrading unifies both strata towers as commuting idempotents with triangular support, marginals recovering each tower, and a Mobius/inclusion-exclusion inverse',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'the omega x Omega bigrading unifies both strata towers as commuting idempotents with triangular support, marginals recovering each tower, and a Mobius/inclusion-exclusion inverse'
);
