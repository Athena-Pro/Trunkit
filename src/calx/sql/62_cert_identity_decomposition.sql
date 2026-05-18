-- Unified model, step 62: attest the capstone -- Id_seq ~= coproduct_k W_k.
--
-- The strata grading is promoted to a NATURAL ISOMORPHISM of endofunctors:
-- a strong-monoidal resolution of the identity in End(seq). Five laws
-- (hash-pinned self-contained checker proofs/identity_decomposition.py):
--   N1 G := coproduct_{k>=0} W_k is an endofunctor (rung-wise on morphisms);
--   N2 theta : G => Id_seq has iso components (inverse phi);
--   N3 theta is NATURAL: Id(f).theta_S == theta_S'.G(f) for every morphism
--      (true because omega(t) is an intrinsic term invariant -> every
--       morphism preserves a term's rung);
--   N4 Sum_{k>=0} W_k == Id_seq -- the FULL identity (W_0 = omega=0 units;
--      no omega>=1 truncation);
--   N5 strong monoidal: W_k(S (+) T)=W_k(S)(+)W_k(T), W_k(empty)=empty.
-- Canonical: naturals(120) FULL omega-decomposition = [1,40,66,13]
-- (sum 120 = |naturals(120)|).
--
-- Live, kan carries the endofunctor G_grading and the natural iso theta/phi
-- (tables kan.identity_decomposition[_witness], views *_summary/_laws).
-- Driven by tools/cert_formal.py. Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'identity_decomposition',
       '{"nat_iso":"theta : G => Id_seq  (G = coproduct_{k>=0} W_k)",
         "inverse":"phi : Id_seq => G (tag by intrinsic omega)",
         "laws":["N1_G_functorial","N2_components_iso","N3_theta_natural",
                 "N4_resolves_full_identity","N5_strong_monoidal"],
         "why_natural":"omega(t) is an intrinsic term invariant",
         "canonical":"naturals(120) full decomposition=[1,40,66,13]"}'::jsonb,
       'Id_seq is naturally isomorphic to the coproduct of the strata rungs: a strong-monoidal resolution of the identity (the grading IS the identity, decomposed)',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'Id_seq is naturally isomorphic to the coproduct of the strata rungs: a strong-monoidal resolution of the identity (the grading IS the identity, decomposed)'
);
