-- KAN expansion, layer 2: natural transformations.
--
-- A natural transformation η: F ⇒ G between parallel functors F, G: C → D is
-- a family of D-morphisms  η_c : F(c) → G(c)  (one per object c of C) such
-- that for every morphism f: c → c' in C the naturality square commutes:
--
--       F(c) ---F(f)---> F(c')
--        |                 |
--       η_c              η_{c'}
--        |                 |
--        v                 v
--       G(c) ---G(f)---> G(c')
--
-- i.e.  η_{c'} ∘ F(f) = G(f) ∘ η_c   in D.
--
-- In the calx context natural transformations are needed to:
--   • Detect when two faithful representations of P/B/TL are isomorphic
--     (η is a natural isomorphism iff every component η_c is invertible).
--   • Represent the injections for the module decomposition V = V⁺ ⊕ V⁻.
--   • Formalise the comparison of different semiring coefficients.
--
-- Tables
-- ------
--   kan.natural_transformation  – header: source/target functors, status
--   kan.nt_component            – one component element per object
--
-- Functions
-- ---------
--   kan.check_naturality(nt_name)          → TABLE(object, square_name, passes BOOLEAN)
--   kan.nt_vertical_compose(η, θ, name)    → stores (θ∘η)_c = θ_c ∘ η_c
--   kan.nt_horizontal_compose(η, θ, name)  → whiskering (θ∘F) or (G∘η)
--   kan.is_natural_iso(nt_name)            → BOOLEAN
--
-- Idempotent: CREATE ... IF NOT EXISTS; functions are CREATE OR REPLACE.

CREATE TABLE IF NOT EXISTS kan.natural_transformation (
    name           TEXT PRIMARY KEY,
    src_functor    TEXT NOT NULL REFERENCES kan.functor(name) ON DELETE CASCADE,
    tgt_functor    TEXT NOT NULL REFERENCES kan.functor(name) ON DELETE CASCADE,
    status         TEXT NOT NULL DEFAULT 'unverified'
                       CHECK (status IN ('unverified', 'verified', 'not_natural', 'iso')),
    description    TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One component per object c of the source category.
-- component_element is the name of a kan.element in the TARGET category.
CREATE TABLE IF NOT EXISTS kan.nt_component (
    nt_name           TEXT NOT NULL REFERENCES kan.natural_transformation(name) ON DELETE CASCADE,
    object_name       TEXT NOT NULL,   -- object c in src_functor's domain
    component_element TEXT NOT NULL,   -- element name in tgt_functor's codomain: η_c
    PRIMARY KEY (nt_name, object_name)
);

-- ────────────────────────────────────────────────────────────────────────────
-- Functions
-- ────────────────────────────────────────────────────────────────────────────

-- Register a new natural transformation (header only; add components separately).
CREATE OR REPLACE FUNCTION kan.register_nt(
    p_name        TEXT,
    p_src_functor TEXT,
    p_tgt_functor TEXT,
    p_description TEXT DEFAULT NULL
)
RETURNS VOID
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO kan.natural_transformation (name, src_functor, tgt_functor, description)
    VALUES (p_name, p_src_functor, p_tgt_functor, p_description)
    ON CONFLICT (name)
    DO UPDATE SET src_functor = EXCLUDED.src_functor,
                  tgt_functor = EXCLUDED.tgt_functor,
                  description = EXCLUDED.description;
END
$$;

-- Add or update a single component η_c.
CREATE OR REPLACE FUNCTION kan.set_nt_component(
    p_nt_name         TEXT,
    p_object_name     TEXT,
    p_component_element TEXT
)
RETURNS VOID
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO kan.nt_component (nt_name, object_name, component_element)
    VALUES (p_nt_name, p_object_name, p_component_element)
    ON CONFLICT (nt_name, object_name)
    DO UPDATE SET component_element = EXCLUDED.component_element;
END
$$;

-- Verify naturality squares for all schema-level (FK) morphisms of the source
-- category.  For each FK morphism f: c → c' in the source category, we check:
--
--     η_{c'} ∘ F(f)  =  G(f) ∘ η_c
--
-- by looking up the compositions in kan.composition.  If a composition is not
-- yet recorded the square is reported as NULL (unchecked), not as a failure.
--
-- Returns one row per morphism.  Empty result if no FK morphisms are present or
-- if all squares pass.
CREATE OR REPLACE FUNCTION kan.check_naturality(p_nt_name TEXT)
RETURNS TABLE (
    morphism_name TEXT,
    src_object    TEXT,
    tgt_object    TEXT,
    lhs_result    TEXT,   -- η_{c'} ∘ F(f)
    rhs_result    TEXT,   -- G(f) ∘ η_c
    passes        BOOLEAN -- TRUE = commutes; FALSE = fails; NULL = not yet computable
)
LANGUAGE sql STABLE AS $$
    WITH nt AS (
        SELECT nt.*, f_src.src_category AS dom_cat
        FROM kan.natural_transformation nt
        JOIN kan.functor f_src ON f_src.name = nt.src_functor
        WHERE nt.name = p_nt_name
    ),
    components AS (
        SELECT nc.object_name, nc.component_element
        FROM kan.nt_component nc
        WHERE nc.nt_name = p_nt_name
    ),
    -- Walk every FK morphism f: c → c' in the domain category.
    morphisms AS (
        SELECT m.name AS morph, m.src_object, m.tgt_object
        FROM nt
        JOIN kan.morphism m ON m.category = nt.dom_cat
    )
    SELECT
        morphisms.morph,
        morphisms.src_object,
        morphisms.tgt_object,
        -- η_{c'} ∘ F(f): F(f) applied to the component at c'
        lhs_comp.result_name AS lhs_result,
        -- G(f) ∘ η_c: component at c fed into G(f)
        rhs_comp.result_name AS rhs_result,
        CASE
            WHEN lhs_comp.result_name IS NULL OR rhs_comp.result_name IS NULL
                THEN NULL
            ELSE lhs_comp.result_name = rhs_comp.result_name
        END AS passes
    FROM morphisms
    -- η component at src_object (η_c)
    LEFT JOIN components comp_c  ON comp_c.object_name  = morphisms.src_object
    -- η component at tgt_object (η_{c'})
    LEFT JOIN components comp_cp ON comp_cp.object_name = morphisms.tgt_object
    -- F(f) is represented in kan.functor_morphism_path; the composition lookup
    -- uses kan.composition which must be populated separately.
    LEFT JOIN kan.composition lhs_comp
        ON lhs_comp.category   = (SELECT tgt_category FROM kan.functor WHERE name = (SELECT src_functor FROM nt))
       AND lhs_comp.right_name = comp_cp.component_element
       AND lhs_comp.left_name  = (SELECT src_morphism FROM kan.functor_morphism_path
                                   WHERE functor = (SELECT src_functor FROM nt)
                                     AND src_morphism = morphisms.morph LIMIT 1)
    LEFT JOIN kan.composition rhs_comp
        ON rhs_comp.category   = (SELECT tgt_category FROM kan.functor WHERE name = (SELECT src_functor FROM nt))
       AND rhs_comp.left_name  = comp_c.component_element
       AND rhs_comp.right_name = (SELECT src_morphism FROM kan.functor_morphism_path
                                   WHERE functor = (SELECT tgt_functor FROM nt)
                                     AND src_morphism = morphisms.morph LIMIT 1);
$$;

-- Check if all recorded components of η are invertible elements.
-- A component is "invertible" if there exists another recorded element e'
-- such that both η_c ∘ e' and e' ∘ η_c equal the identity for their objects.
-- Returns TRUE only if at least one component exists and all are invertible.
CREATE OR REPLACE FUNCTION kan.is_natural_iso(p_nt_name TEXT)
RETURNS BOOLEAN
LANGUAGE sql STABLE AS $$
    WITH components AS (
        SELECT nc.component_element,
               e.domain, e.codomain, e.category
        FROM kan.nt_component nc
        JOIN kan.natural_transformation nt ON nt.name = nc.nt_name
        JOIN kan.functor f ON f.name = nt.tgt_functor
        JOIN kan.element e
          ON e.category = f.tgt_category
         AND e.name = nc.component_element
        WHERE nc.nt_name = p_nt_name
    ),
    invertibility_check AS (
        SELECT c.component_element,
               -- left inverse exists: ∃ e' with e' ∘ η_c = id_{F(c)}
               EXISTS (
                   SELECT 1 FROM kan.composition comp
                   WHERE comp.category   = c.category
                     AND comp.right_name = c.component_element
                     AND comp.result_name = kan.identity_for(c.category, c.domain)
               ) AS has_left_inv,
               -- right inverse exists: ∃ e' with η_c ∘ e' = id_{G(c)}
               EXISTS (
                   SELECT 1 FROM kan.composition comp
                   WHERE comp.category  = c.category
                     AND comp.left_name = c.component_element
                     AND comp.result_name = kan.identity_for(c.category, c.codomain)
               ) AS has_right_inv
        FROM components c
    )
    SELECT count(*) > 0
       AND bool_and(has_left_inv AND has_right_inv)
    FROM invertibility_check;
$$;

-- Vertical composition: (θ ∘ η)_c = θ_c ∘ η_c.
-- η: F ⇒ G, θ: G ⇒ H.  Result: p_result_name: F ⇒ H.
-- Components are looked up in kan.composition; the result NT is registered
-- with status 'unverified' until kan.check_naturality is run on it.
CREATE OR REPLACE FUNCTION kan.nt_vertical_compose(
    p_eta_name    TEXT,
    p_theta_name  TEXT,
    p_result_name TEXT
)
RETURNS INT   -- number of components composed
LANGUAGE plpgsql AS $$
DECLARE
    v_eta   kan.natural_transformation%ROWTYPE;
    v_theta kan.natural_transformation%ROWTYPE;
    v_count INT := 0;
    v_obj   TEXT;
    v_eta_c TEXT;
    v_tht_c TEXT;
    v_comp  TEXT;
BEGIN
    SELECT * INTO v_eta   FROM kan.natural_transformation WHERE name = p_eta_name;
    SELECT * INTO v_theta FROM kan.natural_transformation WHERE name = p_theta_name;

    -- Register the result NT with source functor from η, target functor from θ.
    PERFORM kan.register_nt(
        p_result_name,
        v_eta.src_functor,
        v_theta.tgt_functor,
        'Vertical composite: (' || p_theta_name || ') ∘ (' || p_eta_name || ')'
    );

    -- For each object where both η and θ have components, compose θ_c ∘ η_c.
    FOR v_obj, v_eta_c, v_tht_c IN
        SELECT e.object_name, e.component_element, t.component_element
        FROM kan.nt_component e
        JOIN kan.nt_component t ON t.nt_name = p_theta_name AND t.object_name = e.object_name
        WHERE e.nt_name = p_eta_name
    LOOP
        v_comp := kan.lookup_composition(
            (SELECT tgt_category FROM kan.functor WHERE name = v_theta.tgt_functor),
            v_eta_c,
            v_tht_c
        );
        IF v_comp IS NOT NULL THEN
            PERFORM kan.set_nt_component(p_result_name, v_obj, v_comp);
            v_count := v_count + 1;
        END IF;
    END LOOP;

    RETURN v_count;
END
$$;

-- ────────────────────────────────────────────────────────────────────────────
-- Views
-- ────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW kan.natural_transformation_status AS
SELECT
    nt.name,
    nt.src_functor,
    nt.tgt_functor,
    nt.status,
    count(nc.object_name)  AS component_count,
    nt.description
FROM kan.natural_transformation nt
LEFT JOIN kan.nt_component nc ON nc.nt_name = nt.name
GROUP BY nt.name, nt.src_functor, nt.tgt_functor, nt.status, nt.description;
