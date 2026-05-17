-- Unified model, step 48: attest the combined-signature unification.
--
-- Formal claim: the combined (difference (+) factorial) 7-vector of nine OEIS
-- classics has specific measured values, AND three structural laws hold:
--   Law A  the combined signature is a COMPLETE invariant on the corpus
--          (all nine pairwise distinct);
--   Law B  Catalan/Bell/Motzkin -- identical under the difference lens --
--          are combined-distinct (the factorial lens resolves that class);
--   Law C  squares/cubes -- identical under the factorial lens -- are
--          combined-distinct (the difference lens resolves that class).
--
-- The unification capstone: each homology lens's blind spot is exactly
-- covered by the other. Backed by the self-contained hash-pinned checker
-- proofs/combined_signature.py via tools/cert_formal.py. Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'combined_signature',
       '{"vector":["d0","d1","d2","parity","omega","bigomega","shared_prime"],
         "laws":["A:complete_invariant_on_corpus",
                 "B:factorial_resolves_difference_class[A000108,A000110,A001006]",
                 "C:difference_resolves_factorial_class[A000290,A000578]"]}'::jsonb,
       'combined difference+factorial signature is a complete invariant; each lens resolves the other class',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'combined difference+factorial signature is a complete invariant; each lens resolves the other class'
);
