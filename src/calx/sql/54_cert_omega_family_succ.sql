-- Unified model, step 54: attest the successor-kernel omega family +
-- generative-kernel-dependence.
--
-- Answering "can the generative kernel just be succ [1,2,3,...]?": yes. With
-- the successor kernel the omega/Omega strata are exactly the canonical
-- arithmetic sequences (NW1 = prime powers, NW2/NW3 = 2/3 distinct primes,
-- NB2/NB3/NB4 = semiprimes / 3- / 4-almost-primes). Four laws (hash-pinned,
-- self-contained checker proofs/omega_family_succ.py):
--   1  successor strata == the canonical first-60 sequences;
--   2  every term satisfies its relation; per-member sha256 fingerprints;
--   3  KERNEL-DEPENDENCE: for every relation the successor member's
--      difference-tower H1 differs from its Aliquot-Recaman twin -- the
--      (omega,Omega) relation alone does NOT fix the homology; the
--      generative kernel does (predictable kernel -> partially collapsing
--      strata; chaotic kernel -> non-collapsing strata);
--   4  six pairwise-distinct successor members.
--
-- Driven by tools/cert_formal.py. Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'omega_family_succ',
       '{"generative_set":"successor kernel [1,2,3,...]",
         "members":{"NW1":"prime powers","NW2":"omega=2","NW3":"omega=3",
                    "NB2":"semiprimes","NB3":"3-almost-primes",
                    "NB4":"4-almost-primes"},
         "kernel_dependence":"succ vs Z000001 difference-tower differs for every relation",
         "laws":["canonical_strata","relation+sha256","kernel_dependence",
                 "6_distinct"]}'::jsonb,
       'successor-kernel omega family is the canonical arithmetic strata, and the omega/Omega family is provably generative-kernel-dependent',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'successor-kernel omega family is the canonical arithmetic strata, and the omega/Omega family is provably generative-kernel-dependent'
);
