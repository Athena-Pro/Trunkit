-- Unified model, step 81: attest the (co)limit closure of the strata category.
--
-- Steps 59/60 certified the COPRODUCT; 63/64 the omega x Omega bigrading
-- (commuting idempotents + Mobius inverse). This step closes the strata
-- category under the remaining finite (co)limits and attests it. Four laws
-- (hash-pinned self-contained checker proofs/colimit_closure.py over the
-- ambient set S = {2..2000}):
--   L1 PULLBACK     C_ij = W_i x_S B_j: the bigrading cell is the fiber
--                   product of the cospan W_i -> S <- B_j, with the limit
--                   universal property (largest common subobject, unique
--                   mediating map);
--   L2 PUSHOUT      the dual W_i +_{C_ij} B_j = W_i U B_j with coprojections
--                   agreeing on C_ij and |.| = |W_i|+|B_j|-|C_ij| (colimit
--                   universal property: smallest cocone);
--   L3 COPRODUCT    the empty-gluing (initial-object) case recovers the
--                   certified step-60 coproduct: pairwise-disjoint W_i,
--                   (+)_i W_i = S exactly;
--   L4 DISTRIBUTIVE W_i o B_j = B_j o W_i = C_ij (commuting idempotents),
--                   marginals recover each tower, and the 2-D Mobius /
--                   inclusion-exclusion inverse rebuilds every cell -- the
--                   bigrading IS the distributive product of the two towers.
-- Canonical signature sha 9ed9fe95d42d4d85; the live engine
-- (build_colimit_closure.py) produces the identical sha and its boolean
-- kan.colimit_closure_laws view is auto-corroborated by the step-79 bridge.
--
-- Live: kan carries the 'colimitclosure' functor + tables
-- kan.colimit_closure[_cell] (views *_summary/_cell_witness/_laws). Driven by
-- tools/cert_formal.py. Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'colimit_closure',
       '{"category":"strata = subobjects of S={2..2000} with inclusions",
         "laws":["L1_pullback","L2_pushout","L3_coproduct",
                 "L4_distributive"],
         "pullback":"C_ij = W_i x_S B_j (fiber product / limit)",
         "pushout":"W_i +_{C_ij} B_j = W_i U B_j (colimit); coproduct = empty gluing",
         "distributive":"commuting idempotents + 2-D Mobius inverse",
         "canonical":"sha 9ed9fe95d42d4d85"}'::jsonb,
       'the strata category is (co)limit-closed: the omega x Omega bigrading cell is the pullback W_i x_S B_j (limit), its dual pushout W_i +_{C_ij} B_j recovers the union with the certified coproduct as the empty-gluing case, and the omega/Omega towers commute via a Mobius-invertible distributive law',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'the strata category is (co)limit-closed: the omega x Omega bigrading cell is the pullback W_i x_S B_j (limit), its dual pushout W_i +_{C_ij} B_j recovers the union with the certified coproduct as the empty-gluing case, and the omega/Omega towers commute via a Mobius-invertible distributive law'
);
