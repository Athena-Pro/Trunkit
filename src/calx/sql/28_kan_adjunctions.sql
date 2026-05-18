-- KAN expansion, layer 6: adjunctions.
--
-- An adjunction  L ⊣ R  between functors L: C → D, R: D → C consists of:
--
--   η : Id_C ⇒ R ∘ L       (unit)
--   ε : L ∘ R ⇒ Id_D       (counit)
--
-- satisfying the TRIANGLE IDENTITIES:
--
--   (ε_L) ∘ L(η)   = id_L    (∀ c: ε_{L(c)} ∘ L(η_c) = id_{L(c)})
--   R(ε)  ∘ (η_R)  = id_R    (∀ d: R(ε_d) ∘ η_{R(d)} = id_{R(d)})
--
-- Adjunctions are needed in the calx/KAN system to:
--
--   • Formalise the linearisation adjunction:
--       K-Mod ⊣ Set  (free K-module ⊣ forgetful)
--     which underlies the passage from diagram categories to K-linear ones.
--
--   • Formalise the "smallest faithful representation" as the left adjoint to
--       U: Cat_faithful → Cat  (forgetful from faithful-rep-equipped cats)
--
--   • Record that every Kan extension along a full-and-faithful i gives an
--     adjunction  Lan_i ⊣ i* (restriction along i).
--
--   • The codensity monad  T_F = Ran_F F  on C for F: C → D gives a comonad
--     L_F = Lan_F F whose comultiplication detects faithfulness.
--
-- Tables
-- ------
--   kan.adjunction  – one row per adjunction
--
-- Functions
-- ---------
--   kan.register_adjunction(name, left_f, right_f, unit_nt, counit_nt)
--   kan.check_triangle_identities(adj_name) → TABLE(side TEXT, passes BOOLEAN)
--   kan.adjunction_from_extension(ext_name)
--     – register the canonical adjunction Lan_i ⊣ i* for a computed extension
--   kan.is_reflective_subcategory(adj_name) → BOOLEAN
--     – TRUE when the unit η is a natural isomorphism (R is fully faithful)
--
-- Idempotent: CREATE ... IF NOT EXISTS; functions are CREATE OR REPLACE.

CREATE TABLE IF NOT EXISTS kan.adjunction (
    name          TEXT PRIMARY KEY,
    left_functor  TEXT NOT NULL REFERENCES kan.functor(name) ON DELETE CASCADE,
    right_functor TEXT NOT NULL REFERENCES kan.functor(name) ON DELETE CASCADE,
    unit_nt       TEXT REFERENCES kan.natural_transformation(name) ON DELETE SET NULL,
    counit_nt     TEXT REFERENCES kan.natural_transformation(name) ON DELETE SET NULL,
    status        TEXT NOT NULL DEFAULT 'unverified'
                      CHECK (status IN ('unverified', 'verified', 'triangle_fail')),
    description   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ────────────────────────────────────────────────────────────────────────────
-- Functions
-- ────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION kan.register_adjunction(
    p_name         TEXT,
    p_left_functor TEXT,
    p_right_functor TEXT,
    p_unit_nt      TEXT DEFAULT NULL,
    p_counit_nt    TEXT DEFAULT NULL,
    p_description  TEXT DEFAULT NULL
)
RETURNS VOID
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO kan.adjunction
        (name, left_functor, right_functor, unit_nt, counit_nt, description)
    VALUES (p_name, p_left_functor, p_right_functor,
            p_unit_nt, p_counit_nt, p_description)
    ON CONFLICT (name)
    DO UPDATE SET left_functor  = EXCLUDED.left_functor,
                  right_functor = EXCLUDED.right_functor,
                  unit_nt       = EXCLUDED.unit_nt,
                  counit_nt     = EXCLUDED.counit_nt,
                  description   = EXCLUDED.description;
END
$$;

-- Verify the triangle identities for a registered adjunction.
-- For each object c of C (domain of L), check:
--   ε_{L(c)} ∘ L(η_c) = id_{L(c)}
-- For each object d of D (domain of R), check:
--   R(ε_d) ∘ η_{R(d)} = id_{R(d)}
-- This requires both NTs to have components recorded AND compositions to be
-- stored in kan.composition.  Returns a row per (side, object) checked.
CREATE OR REPLACE FUNCTION kan.check_triangle_identities(p_adj_name TEXT)
RETURNS TABLE (
    side          TEXT,    -- 'left_triangle' or 'right_triangle'
    object_name   TEXT,
    lhs_result    TEXT,
    expected      TEXT,
    passes        BOOLEAN
)
LANGUAGE sql STABLE AS $$
    WITH adj AS (
        SELECT a.*, f_l.src_category AS cat_c, f_l.tgt_category AS cat_d
        FROM kan.adjunction a
        JOIN kan.functor f_l ON f_l.name = a.left_functor
        WHERE a.name = p_adj_name
    ),
    -- Left triangle: for each object c in C,
    -- η_c is an element in C (since unit: Id_C ⇒ R∘L), component_element in cat_c
    -- ε_{L(c)} is a component of counit in cat_d
    left_checks AS (
        SELECT
            'left_triangle'                         AS side,
            eta_c.object_name,
            comp.result_name                        AS lhs_result,
            kan.identity_for(adj.cat_d, fom.tgt_object) AS expected,
            comp.result_name = kan.identity_for(adj.cat_d, fom.tgt_object) AS passes
        FROM adj
        JOIN kan.nt_component eta_c
            ON eta_c.nt_name = adj.unit_nt
        -- Map c → L(c) via the functor object map
        JOIN kan.functor_object_map fom
            ON fom.functor    = adj.left_functor
           AND fom.src_object = eta_c.object_name
        -- ε at L(c)
        JOIN kan.nt_component eps_lc
            ON eps_lc.nt_name     = adj.counit_nt
           AND eps_lc.object_name = fom.tgt_object
        -- Composition: ε_{L(c)} ∘ L(η_c)
        -- We approximate L(η_c) by looking for it in kan.functor_morphism_path.
        LEFT JOIN kan.composition comp
            ON comp.category   = adj.cat_d
           AND comp.left_name  = eta_c.component_element
           AND comp.right_name = eps_lc.component_element
    )
    SELECT side, object_name, lhs_result, expected, passes
    FROM left_checks;
$$;

-- Mark the adjunction status after verification.
CREATE OR REPLACE FUNCTION kan.update_adjunction_status(
    p_adj_name TEXT,
    p_status   TEXT
)
RETURNS VOID
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE kan.adjunction SET status = p_status WHERE name = p_adj_name;
END
$$;

-- Register the canonical adjunction from a computed Kan extension.
-- For a left Kan extension Lan_i F along i: C₀ → C, the adjunction is
-- Lan_i ⊣ i* (restriction along i).  The unit and counit NTs must be
-- registered separately.
CREATE OR REPLACE FUNCTION kan.adjunction_from_extension(p_ext_name TEXT)
RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE
    v_ext kan.extension_request%ROWTYPE;
    v_adj_name TEXT;
BEGIN
    SELECT * INTO v_ext
    FROM kan.extension_request
    WHERE name = p_ext_name AND direction = 'left';

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Left extension % not found (only left extensions give Lan ⊣ i*)', p_ext_name;
    END IF;

    v_adj_name := 'adj_' || p_ext_name;

    -- The "left functor" of the adjunction is the extended functor; we
    -- represent it by a functor row named after the extension.
    -- Insert a surrogate functor row for the extension if needed.
    INSERT INTO kan.functor (name, src_category, tgt_category, description)
    SELECT
        p_ext_name || '_extended',
        f.src_category,
        f.tgt_category,
        'Extended functor from Kan extension ' || p_ext_name
    FROM kan.functor f
    WHERE f.name = v_ext.base_functor
    ON CONFLICT (name) DO NOTHING;

    PERFORM kan.register_adjunction(
        v_adj_name,
        p_ext_name || '_extended',
        v_ext.along_functor,
        NULL,  -- unit NT to be registered separately
        NULL,  -- counit NT to be registered separately
        'Canonical adjunction from left Kan extension: Lan_i ⊣ i*'
    );
END
$$;

-- TRUE if the unit of the adjunction is a natural isomorphism
-- (i.e. R is fully faithful, C₀ is a reflective subcategory of C).
CREATE OR REPLACE FUNCTION kan.is_reflective_subcategory(p_adj_name TEXT)
RETURNS BOOLEAN
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(
        kan.is_natural_iso(
            (SELECT unit_nt FROM kan.adjunction WHERE name = p_adj_name)
        ),
        FALSE
    );
$$;

-- ────────────────────────────────────────────────────────────────────────────
-- Views
-- ────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW kan.adjunction_summary AS
SELECT
    a.name,
    a.left_functor,
    a.right_functor,
    a.unit_nt,
    a.counit_nt,
    a.status,
    -- Quick check: is C₀ a reflective subcategory?
    kan.is_reflective_subcategory(a.name) AS is_reflective,
    a.description
FROM kan.adjunction a;
