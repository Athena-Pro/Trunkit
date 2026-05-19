-- Unified model, step 76: attest the F_1 radix axis.
--
-- Steps 70/73 read lithon row-0 ONLY as unary (cell=1, mult C(16,s)) -- the
-- zeta kernel, depth = magnitude, why the explosive corpus had to be
-- windowed out. The binary reading (cell c = 2^c, unset cells significant)
-- is the dual extreme: multiplicity 1, depth = O(log a_n). The explosive
-- depth collapses; the two readings reconcile on a_n (radix trades depth
-- for multiplicity -- the same "two factorizations of one object" shape as
-- the bigrading / identity capstone). Four laws (hash-pinned self-contained
-- checker proofs/f1_radix.py):
--   R1 binary bijection  2^c super-increasing => mult 1 vs unary C(16,s);
--   R2 depth collapse    over ALL 60 terms binary depth O(log a_n) <<
--                        unary magnitude a_n;
--   R3 reconciliation    16-bit blocks decode to a_n exactly;
--   R4 carry / horizon   depth = 16*ceil(bitlen/16): the lithon 16-col
--                        horizon as a radix carry.
-- The explosive depth, collapsed -- canonical radix vectors sha 967d3ca7cdca8628.
--
-- Live: kan carries the 'f1radix' functor + tables kan.f1_radix[_term]
-- (views *_summary/_collapse/_laws). Driven by tools/cert_formal.py.
-- Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'f1_radix',
       '{"axis":"F_1 row-0 read at radix b: b=1 unary zeta, b=2 binary place-value",
         "laws":["R1_binary_bijection","R2_depth_collapse",
                 "R3_reconciliation","R4_carry_horizon"],
         "collapse":"unary depth = magnitude a_n; binary depth = O(log a_n)",
         "thesis":"the radix trades depth against multiplicity, never the integer",
         "canonical":"radix vectors sha 967d3ca7cdca8628"}'::jsonb,
       'the F_1 radix axis: row-0 read in binary place-value is a bijection (multiplicity 1, dual to the unary C(16,s) zeta kernel) that collapses explosive-term depth from the magnitude a_n to O(log a_n), reconciles exactly on a_n, and carries on the 16-column horizon -- the explosive depth collapsed',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'the F_1 radix axis: row-0 read in binary place-value is a bijection (multiplicity 1, dual to the unary C(16,s) zeta kernel) that collapses explosive-term depth from the magnitude a_n to O(log a_n), reconciles exactly on a_n, and carries on the 16-column horizon -- the explosive depth collapsed'
);
