-- Unified model, step 70: attest the static adelic shadow.
--
-- The boildown fiber of val:lithon->seq -- the representational multiplicity
-- orthogonal to every multiplicative axis. Three laws (hash-pinned
-- self-contained checker proofs/shadow.py):
--   S1 super-increasing : each prime row {p^1..p^16} is super-increasing,
--                         so its subset sums are unique (per-row count in
--                         {0,1}); F_1 (row-0) is the SOLE source of
--                         representational multiplicity;
--   S2 factorization    : rho(N) = SUM_{s=0..16} C(16,s)*A(N-s) equals an
--                         independent 0/1 subset-sum DP over the full atom
--                         multiset, for all N<=300 -- the adelic
--                         factorization (F_1 binomial kernel (*) prime-power
--                         subset-sum count A) is exact; rho(1)=16;
--   S3 kernel separation: the coarse shadow signature SEPARATES the residual
--                         combined-invariant collision kernel that the whole
--                         multiplicative tower (omega/Omega/ht/bigrading/
--                         chromatic/combined) could NOT -- {pow2,pow3,pow4}
--                         pairwise distinct AND primorial != evens.
-- Canonical A[0..64] sha aa8a53978645a046.
--
-- Live: kan.shadow_term/_signature/_separation over the corpus; the engine
-- measured shadow separates 4/4 combined collisions. The shadow is the
-- orthogonal (fiber-direction) axis that completes the invariant system.
-- Driven by tools/cert_formal.py. Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'shadow',
       '{"rho":"SUM_{s=0..16} C(16,s)*A(N-s)",
         "A":"prime-power subset-sum count (per prime row super-increasing)",
         "F1":"row-0 binomial kernel = sole multiplicity source; rho(1)=16",
         "laws":["S1_super_increasing","S2_adelic_factorization_exact",
                 "S3_separates_collision_kernel"],
         "kernel":["pow2","pow3","pow4","primorial","evens"],
         "result":"shadow resolves 4/4 combined-invariant collisions",
         "canonical":"A[0..64] sha aa8a53978645a046"}'::jsonb,
       'the static adelic shadow factors as the F_1 binomial kernel convolved with the prime-power subset-sum count, and is the orthogonal axis that resolves the residual collision kernel the multiplicative tower could not',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'the static adelic shadow factors as the F_1 binomial kernel convolved with the prime-power subset-sum count, and is the orthogonal axis that resolves the residual collision kernel the multiplicative tower could not'
);
