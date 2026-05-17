-- Unified model, step 51: attest the higher-homology (H2) finding.
--
-- Formal claim: the simplicial flag (clique) complex of the shared-prime
-- graph is ACYCLIC ABOVE H0 wherever computable -- b1_flag = 0 AND b2 = 0 --
-- even when the underlying graph carries hundreds of 1-cycles (every cycle
-- is filled by triangles). Witnessed on the fast within-budget exemplars:
-- primes (cyc=0, isolated 0-skeleton), tau (cyc=13), totient (cyc=171),
-- all with flag b1=b2=0. (Dense graphs are over enumeration budget and
-- honestly excluded; see kan.shared_prime_betti.)
--
-- Backed by the self-contained hash-pinned checker proofs/shared_prime_h2.py
-- via tools/cert_formal.py. Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'shared_prime_h2',
       '{"complex":"flag(clique) of shared-prime graph",
         "exemplars":{"A000040":{"cyc":0},"A000005":{"cyc":13},
                      "A000010":{"cyc":171}},
         "finding":"b1_flag=0 and b2=0 where computable; all 1-cycles triangle-filled"}'::jsonb,
       'shared-prime flag complex is acyclic above H0 (b1=b2=0); rich 1-cycles are all triangle-filled',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'shared-prime flag complex is acyclic above H0 (b1=b2=0); rich 1-cycles are all triangle-filled'
);
