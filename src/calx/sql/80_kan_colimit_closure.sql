-- Unified model, step 80: the (co)limit closure of the strata category.
--
-- The strata category = subobjects of the ambient set S with inclusions.
-- Steps 59/60 certified the COPRODUCT (S|{omega>=1} ~= coproduct_k W_k);
-- steps 63/64 certified the omega x Omega bigrading as commuting idempotents
-- with a Mobius inverse. This step assembles the remaining universal
-- constructions so the category is closed under finite (co)limits:
--
--   PULLBACK     the bigrading cell C_ij = W_i x_S B_j is the fiber product
--                of the cospan W_i -> S <- B_j (the LIMIT: largest common
--                subobject; mediating map exists & is unique).
--   PUSHOUT      its dual W_i +_{C_ij} B_j = W_i U B_j with coprojections
--                agreeing on C_ij and |.| = |W_i|+|B_j|-|C_ij| (the COLIMIT:
--                smallest cocone); the empty-gluing case recovers the
--                certified step-60 coproduct.
--   DISTRIBUTIVE W_i o B_j = B_j o W_i = C_ij (commuting idempotents),
--                marginals recover each tower, and the 2-D Mobius /
--                inclusion-exclusion inverse rebuilds every cell -- the
--                bigrading IS the distributive product of the two towers.
--
-- Proved input-independently by proofs/colimit_closure.py. The boolean
-- _laws view is auto-discovered by the step-79 kan-engine -> cert bridge,
-- so the live engine corroborates the external proof. Idempotent.

CREATE TABLE IF NOT EXISTS kan.colimit_closure (
    structure        TEXT PRIMARY KEY,        -- 'strata'
    corpus_lo        INTEGER NOT NULL,
    corpus_hi        INTEGER NOT NULL,
    n_cells          INTEGER NOT NULL,        -- occupied bigrading cells
    pullback_ok      BOOLEAN NOT NULL,        -- L1: cell = fiber product + UP
    pushout_ok       BOOLEAN NOT NULL,        -- L2: glue = union + incl-excl
    coproduct_ok     BOOLEAN NOT NULL,        -- L3: empty-gluing = step-60
    distributive_ok  BOOLEAN NOT NULL,        -- L4: commuting + Mobius inverse
    head_sha         TEXT NOT NULL,
    verified_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kan.colimit_cell (
    omega_i          INTEGER NOT NULL,        -- omega value i
    bigomega_j       INTEGER NOT NULL,        -- Omega value j
    card             INTEGER NOT NULL,        -- |C_ij| (the pullback object)
    is_pullback      BOOLEAN NOT NULL,        -- C_ij = W_i ∩ B_j exactly
    mobius_ok        BOOLEAN NOT NULL,        -- 2-D incl-excl rebuilds card
    PRIMARY KEY (omega_i, bigomega_j)
);

CREATE OR REPLACE VIEW kan.colimit_closure_summary AS
SELECT structure, corpus_lo, corpus_hi, n_cells,
       (pullback_ok AND pushout_ok AND coproduct_ok
        AND distributive_ok) AS limit_closed,
       head_sha
  FROM kan.colimit_closure;

-- The pullback/Mobius witness, cell by cell.
CREATE OR REPLACE VIEW kan.colimit_cell_witness AS
SELECT omega_i, bigomega_j, card, is_pullback, mobius_ok
  FROM kan.colimit_cell
 ORDER BY omega_i, bigomega_j;

-- Boolean law view -- auto-discovered by cert.kan_engines_all_true() (step 79).
CREATE OR REPLACE VIEW kan.colimit_closure_laws AS
SELECT (SELECT bool_and(pullback_ok)     FROM kan.colimit_closure) AS l1_pullback,
       (SELECT bool_and(pushout_ok)      FROM kan.colimit_closure) AS l2_pushout,
       (SELECT bool_and(coproduct_ok)    FROM kan.colimit_closure) AS l3_coproduct,
       (SELECT bool_and(distributive_ok) FROM kan.colimit_closure) AS l4_distributive,
       (SELECT bool_and(is_pullback)     FROM kan.colimit_cell)    AS all_cells_pullback,
       (SELECT bool_and(mobius_ok)       FROM kan.colimit_cell)    AS all_cells_mobius;
