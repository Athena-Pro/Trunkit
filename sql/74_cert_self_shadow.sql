-- Unified model, step 74: attest the self-shadow multiplicity rho_self.
--
-- The self-syzygy (71/72) took ONE representation; the self-shadow counts
-- ALL of them -- the denumerant fiber over a term's own predecessors.
-- a_0=1 is no longer the CLOSER but the SUMMATORY/zeta operator: the
-- unbounded unit makes rho_self(n) = SUM_{m=0}^{a_n} rho_hat(m), the
-- cumulative of the no-unit denumerant (dual to step 70's static shadow
-- where F_1 was the binomial convolution kernel). Four laws (hash-pinned
-- self-contained checker proofs/self_shadow.py):
--   L1 well-defined  rho_self>=1 all n, >=2 for n>=2 (duplicate-1 syzygy);
--   L2 F_1 = zeta    full denumerant == summatory of the no-a_0 denumerant;
--   L3 head pin       canonical windowed rho_self vectors sha 64f009e29f7326bc;
--   L4 separation     self-shadow signature pairwise-separates the
--                     recursive corpus (relative => no adelic window).
-- The chestnut, counted regardless of size -- and the count classifies
-- WHICH recursive sequence produced it.
--
-- Live: kan carries the 'selfshadow' functor + tables
-- kan.self_shadow[_term] (views *_summary/_separation/_laws). Driven by
-- tools/cert_formal.py. Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'self_shadow',
       '{"count":"rho_self(n) = #reps of a_n over its own predecessors",
         "laws":["L1_well_defined","L2_F1_is_zeta","L3_head_pin",
                 "L4_separation"],
         "f1_role":"summatory/zeta: rho_self(n)=SUM_{m<=a_n} rho_hat(m)",
         "thesis":"relative => no window; F_1 turns the point-count into its cumulative",
         "canonical":"windowed rho_self vectors sha 64f009e29f7326bc"}'::jsonb,
       'the self-shadow multiplicity rho_self is well-defined (>=2 for n>=2) and the F_1 unit is the summatory/zeta operator rho_self(n) = SUM over m<=a_n of rho_hat(m); the self-shadow is a relative invariant that pairwise-separates the recursive corpus -- the chestnut counted regardless of size',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'the self-shadow multiplicity rho_self is well-defined (>=2 for n>=2) and the F_1 unit is the summatory/zeta operator rho_self(n) = SUM over m<=a_n of rho_hat(m); the self-shadow is a relative invariant that pairwise-separates the recursive corpus -- the chestnut counted regardless of size'
);
