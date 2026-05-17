-- Unified model, step 82: the kan layer is a PROARROW EQUIPMENT.
--
-- (Wood's proarrow equipment = Shulman's framed bicategory = a fibrant
-- double category: golem.ph.utexas.edu/category/2009/11/equipments.html.)
-- The kan schema already has the double-category data: objects (categories),
-- TIGHT arrows (kan.functor), LOOSE arrows (kan.profunctor), 2-cells
-- (kan.natural_transformation), kan.adjunction. "Equipment" is the
-- condition that every tight arrow f: A -> B has a COMPANION f_!: A-|->B
-- and a CONJOINT f^*: B-|->A, making the loose double category FIBRANT
-- (every niche has a cartesian filler = restriction / base change).
--
-- Certified on the project's already-certified strata POSETS (thin
-- categories, steps 57-64), where every axiom is an EXACT, EXHAUSTIVELY
-- checkable finite relation identity:
--   E1 companion + zig-zag  f_! a bimodule; U_A <= f_!(.)f^*,
--                           f^*(.)f_! <= U_B, f_!(.)f^*(.)f_! = f_!;
--   E2 conjoint + adjunction f^* the genuine right adjoint f_! -| f^*;
--   E3 fibrant / base change for EVERY bimodule M and tight p,q the
--                           restriction M(p,q) = p_!(.)M(.)q^* and is the
--                           cartesian (largest) filler;
--   E4 coherence            (g o f)_! = f_!(.)g_!, (g o f)^* = g^*(.)f^*,
--                           (id)_! = (id)^* = U  (pseudofunctorial).
--
-- Proved input-independently by proofs/equipment.py over posets A=chain3,
-- B=chain4, P=2x2 bigrading cell poset (E3 exhaustive over all 35 bimodules
-- A-|->B). The boolean kan.equipment_laws view is auto-discovered by the
-- step-79 kan-engine -> cert bridge: the live engine corroborates the
-- external proof (identical canonical sha). Idempotent.

CREATE TABLE IF NOT EXISTS kan.equipment (
    structure        TEXT PRIMARY KEY,        -- 'strata_posets'
    n_objects        INTEGER NOT NULL,        -- posets in the model
    n_tight          INTEGER NOT NULL,        -- tight test arrows
    n_bimodules      INTEGER NOT NULL,        -- bimodules A-|->B (E3 scope)
    companion_ok     BOOLEAN NOT NULL,        -- E1
    conjoint_ok      BOOLEAN NOT NULL,        -- E2
    fibrant_ok       BOOLEAN NOT NULL,        -- E3 base change/cartesian
    coherence_ok     BOOLEAN NOT NULL,        -- E4 pseudofunctorial
    head_sha         TEXT NOT NULL,
    verified_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kan.equipment_arrow (
    tight            TEXT PRIMARY KEY,        -- e.g. 'f:A->B'
    is_bimodule      BOOLEAN NOT NULL,        -- f_!, f^* are bimodules
    unit_ok          BOOLEAN NOT NULL,        -- U_A <= f_! (.) f^*
    counit_ok        BOOLEAN NOT NULL,        -- f^* (.) f_! <= U_B
    zigzag1_ok       BOOLEAN NOT NULL,        -- f_!(.)f^*(.)f_! = f_!
    zigzag2_ok       BOOLEAN NOT NULL,        -- f^*(.)f_!(.)f^* = f^*
    companion_card   INTEGER NOT NULL,        -- |f_!|
    conjoint_card    INTEGER NOT NULL         -- |f^*|
);

CREATE OR REPLACE VIEW kan.equipment_summary AS
SELECT structure, n_objects, n_tight, n_bimodules,
       (companion_ok AND conjoint_ok AND fibrant_ok
        AND coherence_ok) AS is_equipment,
       head_sha
  FROM kan.equipment;

CREATE OR REPLACE VIEW kan.equipment_arrow_witness AS
SELECT tight, is_bimodule, unit_ok, counit_ok, zigzag1_ok, zigzag2_ok,
       companion_card, conjoint_card
  FROM kan.equipment_arrow
 ORDER BY tight;

-- Boolean law view -- auto-discovered by cert.kan_engines_all_true() (79).
CREATE OR REPLACE VIEW kan.equipment_laws AS
SELECT (SELECT bool_and(companion_ok) FROM kan.equipment)  AS e1_companion,
       (SELECT bool_and(conjoint_ok)  FROM kan.equipment)  AS e2_conjoint,
       (SELECT bool_and(fibrant_ok)   FROM kan.equipment)  AS e3_fibrant,
       (SELECT bool_and(coherence_ok) FROM kan.equipment)  AS e4_coherence,
       (SELECT bool_and(unit_ok AND counit_ok AND zigzag1_ok
                        AND zigzag2_ok AND is_bimodule)
          FROM kan.equipment_arrow)                        AS all_arrows_ok;
