-- Unified model, step 56: attest the prime-members functor.
--
-- "How we got omega=1 matters functorially; the same process yields the
-- prime members of any sequence." Modelled as a kan endofunctor
-- prime_members : seq -> seq, S |-> PM(S) = the atomic (omega=1) terms of S.
-- Four laws (hash-pinned self-contained checker proofs/prime_members_functor.py):
--   1  well-typed   : every term of PM(X) has omega == 1;
--   2  idempotent   : PM . PM == PM (a projector / coreflection);
--   3  fixed points : PM(S)==S iff every term of S is a prime power
--                      (primes are fixed; mixed sequences are not);
--   4  total + coherent : defined on every input incl. empty / all-composite,
--      and PM over the naturals reproduces the canonical prime powers --
--      the very object the succ-kernel family member NW1 produced.
--
-- Live, the kan layer carries the functor itself: category 'seq' (72
-- objects), functor 'prime_members' with a total idempotent object map;
-- views kan.prime_members_functor / _fixed / _laws. Driven by
-- tools/cert_formal.py. Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'prime_members_functor',
       '{"functor":"prime_members : seq -> seq",
         "definition":"X |-> [t in X : omega(t)=1]  (atomic members)",
         "laws":["well_typed","idempotent","fixed_points=all_prime_power",
                 "total+canonical_coherence"],
         "fixed_points":["A000040 primes","NW1","ZW1"],
         "note":"the omega=1 family members were PM-images all along"}'::jsonb,
       'prime_members is a total idempotent endofunctor on sequences; the omega=1 stratum is functorial and yields the prime members of any sequence',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'prime_members is a total idempotent endofunctor on sequences; the omega=1 stratum is functorial and yields the prime members of any sequence'
);
