-- Unified model, step 61: the identity decomposition (capstone).
--
-- Promotes the per-object coproduct (step 60) to a NATURAL ISOMORPHISM of
-- endofunctors:
--
--      Id_seq  ~=  G ,   G := COPRODUCT_{k>=0} W_k        (theta : G => Id)
--
-- i.e. the strata grading is a RESOLUTION OF THE IDENTITY in End(seq):
--   * G is an endofunctor (rung-wise on morphisms);
--   * theta : G => Id_seq is a natural transformation whose every component
--     is an iso (inverse phi : Id => G);  hence a natural iso;
--   * Sum_{k>=0} W_k == Id_seq  (W_0 = the omega=0 units rung, so the FULL
--     identity is decomposed -- no omega>=1 truncation);
--   * strong monoidal:  W_k(S (+) T) = W_k(S) (+) W_k(T),  W_k(empty)=empty,
--     so (seq,(+),empty) is an N-graded monoidal category.
--
-- The naturality holds because omega(t) is an INTRINSIC term invariant:
-- every morphism preserves a term's rung. Proved input-independently by
-- proofs/identity_decomposition.py. Idempotent.

CREATE TABLE IF NOT EXISTS kan.identity_decomposition (
    functor_G       TEXT PRIMARY KEY,        -- 'G_grading'
    nat_iso         TEXT NOT NULL,           -- 'theta'
    nat_iso_inverse TEXT NOT NULL,           -- 'phi'
    g_functorial    BOOLEAN NOT NULL,        -- N1
    components_iso  BOOLEAN NOT NULL,        -- N2
    theta_natural   BOOLEAN NOT NULL,        -- N3
    resolves_id     BOOLEAN NOT NULL,        -- N4  Sum_k W_k = Id_seq
    strong_monoidal BOOLEAN NOT NULL,        -- N5  W_k preserves (+)
    is_natural_iso  BOOLEAN NOT NULL,
    verified_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Per-object witnesses of the resolution of the identity (full Id, incl. W0).
CREATE TABLE IF NOT EXISTS kan.identity_decomposition_witness (
    seq           TEXT PRIMARY KEY,
    n_terms       INTEGER NOT NULL,
    n_rungs       INTEGER NOT NULL,          -- incl. W0
    coproduct_eq_id BOOLEAN NOT NULL,        -- ⊔_{k>=0} W_k(S) == S exactly
    verified_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE VIEW kan.identity_decomposition_summary AS
SELECT functor_G, nat_iso, nat_iso_inverse, is_natural_iso,
       (g_functorial AND components_iso AND theta_natural
        AND resolves_id AND strong_monoidal) AS all_laws
  FROM kan.identity_decomposition;

CREATE OR REPLACE VIEW kan.identity_decomposition_laws AS
SELECT
  (SELECT bool_and(is_natural_iso) FROM kan.identity_decomposition)        AS natural_iso,
  (SELECT bool_and(g_functorial AND components_iso AND theta_natural
                   AND resolves_id AND strong_monoidal)
     FROM kan.identity_decomposition)                                      AS all_laws,
  (SELECT count(*) FROM kan.identity_decomposition_witness)                AS witnesses,
  (SELECT bool_and(coproduct_eq_id)
     FROM kan.identity_decomposition_witness)                              AS all_witness_full_id;
