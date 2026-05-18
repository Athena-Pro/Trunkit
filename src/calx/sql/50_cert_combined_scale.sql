-- Unified model, step 50: attest the combined invariant's behaviour AT SCALE.
--
-- Formal claim (the honest scale result): the combined difference+factorial
-- signature is a COMPLETE invariant on the original 9-classic corpus but
-- DEGRADES at N=23 to 20 distinct vectors / 23 sequences; the collision
-- kernel is structural -- {powers of 2,3,4} are mutually combined-equivalent
-- ({p^n} share gap-pattern AND factorization homology, prime-independent),
-- and primorials == evens.
--
-- Backed by the self-contained hash-pinned checker proofs/combined_scale.py
-- via tools/cert_formal.py. Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'combined_scale',
       '{"corpus_n":23,"complete_at_n":9,"distinct_at_n23":20,
         "kernel":{"prime_power_class":["A000079","A000244","A000302"],
                   "cross_family":["A002110","A005843"]},
         "interpretation":"combined invariant not complete at scale; kernel = single-prime-base exponentials"}'::jsonb,
       'combined invariant complete at N=9, degrades to 20/23 at N=23; kernel is the prime-power class',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'combined invariant complete at N=9, degrades to 20/23 at N=23; kernel is the prime-power class'
);
