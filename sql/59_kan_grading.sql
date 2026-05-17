-- Unified model, step 59: coreflection + coproduct (grading) structure.
--
-- Promotes the strata tower from "complete orthogonal idempotents" to two
-- universal properties:
--
--   (A) Coreflection.  Each rung W_k is the RIGHT adjoint (coreflector) of
--       the inclusion i_k : Seq_{omega=k} -> Seq. Counit eps_k : W_k => Id
--       is the stratum inclusion; unit is an iso (W_k is the identity on its
--       fixed points); the triangle identities hold  ->  i_k -| W_k.
--
--   (B) Coproduct decomposition.  Completeness becomes a genuine coproduct:
--       S|{omega>=1}  ~=  COPRODUCT_k W_k(S)
--       with cocone iota_k : W_k(S) -> S, jointly surjective, pairwise
--       disjoint, and a UNIQUE mediating map (the rungs partition S) -- i.e.
--       the sequence object is a Z>=1-graded object.
--
-- Tables record the attested structure; the universal properties are proved
-- input-independently by proofs/grading.py. Idempotent.

CREATE TABLE IF NOT EXISTS kan.coreflection (
    rung_functor   TEXT PRIMARY KEY,           -- e.g. strata_W1
    grading        TEXT NOT NULL,              -- 'omega'
    k              INTEGER NOT NULL,
    inclusion      TEXT NOT NULL,              -- incl_W{k}
    counit_nt      TEXT NOT NULL,              -- counit_W{k}: W_k => Id
    adjunction     TEXT NOT NULL,              -- coreflect_W{k}: i_k -| W_k
    idempotent     BOOLEAN NOT NULL,
    counit_natural BOOLEAN NOT NULL,
    triangle_ok    BOOLEAN NOT NULL,
    is_coreflector BOOLEAN NOT NULL,
    verified_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kan.grading_decomposition (
    seq                TEXT NOT NULL,
    grading            TEXT NOT NULL,
    n_rungs            INTEGER NOT NULL,
    jointly_surjective BOOLEAN NOT NULL,        -- union of rungs = S|{w>=1}
    pairwise_disjoint  BOOLEAN NOT NULL,        -- orthogonality
    mediating_unique   BOOLEAN NOT NULL,        -- partition => unique cocone map
    recovers_object    BOOLEAN NOT NULL,        -- coproduct ~= S|{w>=1}
    is_coproduct       BOOLEAN NOT NULL,
    verified_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (seq, grading)
);

CREATE OR REPLACE VIEW kan.coreflection_summary AS
SELECT rung_functor, grading, k, inclusion, counit_nt, adjunction,
       is_coreflector,
       (idempotent AND counit_natural AND triangle_ok) AS laws_ok
  FROM kan.coreflection
 ORDER BY grading, k;

CREATE OR REPLACE VIEW kan.grading_summary AS
SELECT seq, grading, n_rungs, is_coproduct,
       (jointly_surjective AND pairwise_disjoint
        AND mediating_unique AND recovers_object) AS universal_ok
  FROM kan.grading_decomposition
 ORDER BY grading, seq;

-- One-row audit: every recorded coreflector is a coreflector, and every
-- recorded decomposition satisfies the coproduct universal property.
CREATE OR REPLACE VIEW kan.grading_laws AS
SELECT
  (SELECT count(*) FROM kan.coreflection)                              AS coreflectors,
  (SELECT bool_and(is_coreflector) FROM kan.coreflection)              AS all_coreflectors,
  (SELECT bool_and(idempotent AND counit_natural AND triangle_ok)
     FROM kan.coreflection)                                            AS all_adjunction_laws,
  (SELECT count(*) FROM kan.grading_decomposition)                     AS decompositions,
  (SELECT bool_and(is_coproduct) FROM kan.grading_decomposition)       AS all_coproducts;
