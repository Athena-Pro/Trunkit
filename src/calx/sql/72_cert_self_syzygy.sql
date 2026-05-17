-- Unified model, step 72: attest the greedy self-syzygy expansion.
--
-- Each term expanded in its OWN descending predecessors; a_0=1 is the F_1
-- closer. RELATIVE => no adelic window: the explosive corpus is handled in
-- full. Four laws (hash-pinned self-contained checker proofs/self_syzygy.py):
--   G1 termination     a_0=1 => every final remainder is 0;
--   G2 reconstruction  SUM_k q_k a_k = a_n exactly (incl. astronomical terms);
--   G3 the crack       leading digit q_{n-1}=floor(a_n/a_{n-1}) eventually
--                      CONSTANT iff finite geometric growth
--                      (Catalan->3, Fibonacci->1, Motzkin->2) and UNBOUNDED
--                      iff super-exponential (Bell; Factorial digit = n+1);
--   G4 growth readout  the stable digit = floor of the asymptotic ratio
--                      (Catalan 4^-, Fibonacci phi, Motzkin 3^-).
-- The chestnut, cracked regardless of size -- and the fingerprint also
-- classifies WHICH sequences crack. Canonical: 5 leading-digit head
-- strings sha f780c48667fd63b2.
--
-- Live: kan carries the 'selfsyz' functor + tables kan.self_syzygy[_term]
-- (views *_summary/_crack/_laws). Driven by tools/cert_formal.py. Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'self_syzygy',
       '{"expansion":"a_n over descending predecessors; a_0=1 = F_1 closer",
         "laws":["G1_termination","G2_reconstruction","G3_crack_dichotomy",
                 "G4_growth_readout"],
         "readout":{"Catalan":3,"Fibonacci":1,"Motzkin":2,
                    "Bell":"unbounded","Factorial":"n+1 (unbounded)"},
         "thesis":"relative => no window; bounded leading digit <=> finite geometric growth",
         "canonical":"5 head strings sha f780c48667fd63b2"}'::jsonb,
       'the greedy self-syzygy terminates via the F_1 closer and reconstructs exactly; the leading digit is a bounded growth-readout for finite-geometric sequences and diverges for super-exponential ones -- the chestnut cracked regardless of size',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'the greedy self-syzygy terminates via the F_1 closer and reconstructs exactly; the leading digit is a bounded growth-readout for finite-geometric sequences and diverges for super-exponential ones -- the chestnut cracked regardless of size'
);
