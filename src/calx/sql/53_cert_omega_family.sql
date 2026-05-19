-- Unified model, step 53: attest the system-developed omega-relation family.
--
-- The system algorithmically developed a 6-member family from a generative
-- set (the Aliquot-Recaman kernel = Z000001's dynamics, not algebraic), each
-- member defined by an exact small-omega or big-Omega relation to that set:
--   ZW1/ZW2/ZW3 : omega(term) == 1 / 2 / 3   (distinct-prime strata)
--   ZB2/ZB3/ZB4 : Omega(term) == 2 / 3 / 4   (total-prime strata)
--
-- Four laws (hash-pinned, self-contained checker proofs/omega_family.py):
--   1  the generative set is exactly the Z000001 kernel;
--   2  every emitted term satisfies its (omega/Omega) relation exactly;
--   3  each 60-term member matches its measured sha256 fingerprint;
--   4  each member's constrained axis-stream is constant (the factorial
--      instrument's omega/Omega axis degenerates by construction) and the
--      six members are pairwise-distinct sequences.
--
-- Driven by tools/cert_formal.py. Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'omega_family',
       '{"generative_set":"Z000001 Aliquot-Recaman kernel",
         "members":{"ZW1":"omega=1","ZW2":"omega=2","ZW3":"omega=3",
                    "ZB2":"Omega=2","ZB3":"Omega=3","ZB4":"Omega=4"},
         "per_member_terms":60,
         "laws":["generative_set=Z000001","relation_exactly_realized",
                 "sha256_fingerprints","axis-constant_strata+6_distinct"]}'::jsonb,
       'system-developed omega-relation family: 6 algorithmically generated members with exact small-omega/big-Omega relations to the Z000001 generative set',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'system-developed omega-relation family: 6 algorithmically generated members with exact small-omega/big-Omega relations to the Z000001 generative set'
);
