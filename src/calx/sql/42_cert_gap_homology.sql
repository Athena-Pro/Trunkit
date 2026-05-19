-- Unified model, step 42: a formal claim from a real research project.
--
-- Attests the central quantitative claim of the Gap Pattern Homology project
-- (targeting CPP 2027): the
-- prime gap-pattern simplicial complex has H1 rank > 0 that strictly grows
-- with N, with d1.d2 = 0 at every scale.
--
-- Formal tier: backed by the self-contained hash-pinned external checker
-- proofs/gap_homology_primes.py, driven by tools/cert_formal.py. This is the
-- formal tier doing what it was built for — a non-trivial research result,
-- not a toy fact. Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'homology_fact',
       '{"object":"prime_gap_pattern_complex",
         "invariant":"H1",
         "schedule":[50,100,200,500,1000],
         "measured_H1":[3,8,30,59,128],
         "project":"Erdos/paper-gap-pattern-homology"}'::jsonb,
       'prime gap-pattern complex H1 strictly grows (3,8,30,59,128) with d1.d2=0',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'prime gap-pattern complex H1 strictly grows (3,8,30,59,128) with d1.d2=0'
);
