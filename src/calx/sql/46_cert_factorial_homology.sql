-- Unified model, step 46: attest the factorial-homology finding.
--
-- Formal claim: the factorial-homology signatures of nine OEIS classics have
-- specific measured values, AND two structural laws hold:
--   * primes have shared_prime H1 = 0 (distinct primes pairwise coprime); and
--   * squares (A000290) and cubes (A000578) have an IDENTICAL factorial
--     signature [0,0,0,618] (rad(n^2)=rad(n^3)=rad(n) -> powers preserve
--     prime support -> structurally identical prime-interleaving graphs).
--
-- Backed by the self-contained hash-pinned checker
-- proofs/factorial_homology_signature.py, driven by tools/cert_formal.py.
-- Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'factorial_homology',
       '{"sequences":["A000040","A000041","A000045","A000108","A000110",
                      "A000217","A000290","A000578","A001006"],
         "axes":["parity","omega","bigomega","shared_prime"],
         "laws":["primes:shared_prime_H1=0",
                 "squares==cubes:power_prime_support_invariance"]}'::jsonb,
       'factorial-homology signatures hold; primes pairwise coprime; squares == cubes',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'factorial-homology signatures hold; primes pairwise coprime; squares == cubes'
);
