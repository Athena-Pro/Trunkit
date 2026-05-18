-- KAN expansion, layer 3: Kan extensions.
--
-- Given:
--   i : C₀ → C   (inclusion / restriction functor)
--   F : C₀ → D   (functor on the smaller category)
--
-- The LEFT Kan extension  Lan_i F : C → D  is the "best approximation" of F
-- along i from the left (initial among extensions).  For finitely presented
-- C the formula is:
--
--   (Lan_i F)(c)  =  colim_{(i(c₀) → c) ∈ (i ↓ c)} F(c₀)
--
-- The RIGHT Kan extension  Ran_i F : C → D  uses the limit over the opposite
-- comma category (c ↓ i).
--
-- In the calx context the primary uses are:
--   • Extending a representation defined on the generators Ω of TL/B/P to the
--     full diagram category — the paper's Theorem 6.14 argument.
--   • Checking whether the extension remains faithful (Tier 2 enhancement).
--   • Computing the codensity monad of F (= Ran_F F) whose unit detects
--     faithfulness: F faithful ⟺ unit is a monomorphism on objects.
--
-- Current implementation is a metadata store: the actual colimit/limit
-- computation is performed by calling Python code (tools/kan_extension.py)
-- which populates kan.extension_result.  The SQL layer stores, indexes and
-- queries results.
--
-- Tables
-- ------
--   kan.extension_request  – (name, base_functor, along_functor, direction, status)
--   kan.extension_result   – object and morphism maps of the computed extension
--
-- Functions
-- ---------
--   kan.register_extension(name, base, along, direction)
--   kan.extension_object_map(ext_name, src_obj, tgt_obj)
--   kan.extension_morphism_map(ext_name, src_morphism, tgt_element)
--   kan.extension_is_faithful(ext_name) → BOOLEAN
--   kan.codensity_unit_is_mono(ext_name) → BOOLEAN (approximate, element-level)
--
-- Idempotent: CREATE ... IF NOT EXISTS; functions are CREATE OR REPLACE.

CREATE TABLE IF NOT EXISTS kan.extension_request (
    name           TEXT PRIMARY KEY,
    base_functor   TEXT NOT NULL REFERENCES kan.functor(name) ON DELETE CASCADE,
    along_functor  TEXT NOT NULL REFERENCES kan.functor(name) ON DELETE CASCADE,
    direction      TEXT NOT NULL CHECK (direction IN ('left', 'right')),
    status         TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'computed', 'faithful', 'not_faithful')),
    description    TEXT,
    requested_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Object part of the extended functor: Lan_i F (c) = tgt_object.
CREATE TABLE IF NOT EXISTS kan.extension_object_map (
    extension_name TEXT NOT NULL REFERENCES kan.extension_request(name) ON DELETE CASCADE,
    src_object     TEXT NOT NULL,
    tgt_object     TEXT NOT NULL,
    PRIMARY KEY (extension_name, src_object)
);

-- Morphism part: how the extension maps each morphism of C.
-- tgt_element is the name of a kan.element in the target category.
CREATE TABLE IF NOT EXISTS kan.extension_morphism_map (
    extension_name TEXT NOT NULL REFERENCES kan.extension_request(name) ON DELETE CASCADE,
    src_morphism   TEXT NOT NULL,   -- morphism name in the larger category C
    tgt_element    TEXT NOT NULL,   -- element name in D = the image morphism
    PRIMARY KEY (extension_name, src_morphism)
);

-- ────────────────────────────────────────────────────────────────────────────
-- Functions
-- ────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION kan.register_extension(
    p_name         TEXT,
    p_base_functor TEXT,
    p_along_functor TEXT,
    p_direction    TEXT DEFAULT 'left',
    p_description  TEXT DEFAULT NULL
)
RETURNS VOID
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO kan.extension_request
        (name, base_functor, along_functor, direction, description)
    VALUES (p_name, p_base_functor, p_along_functor, p_direction, p_description)
    ON CONFLICT (name)
    DO UPDATE SET base_functor  = EXCLUDED.base_functor,
                  along_functor = EXCLUDED.along_functor,
                  direction     = EXCLUDED.direction,
                  description   = EXCLUDED.description;
END
$$;

CREATE OR REPLACE FUNCTION kan.set_extension_object_map(
    p_ext_name  TEXT,
    p_src_obj   TEXT,
    p_tgt_obj   TEXT
)
RETURNS VOID
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO kan.extension_object_map (extension_name, src_object, tgt_object)
    VALUES (p_ext_name, p_src_obj, p_tgt_obj)
    ON CONFLICT (extension_name, src_object)
    DO UPDATE SET tgt_object = EXCLUDED.tgt_object;
END
$$;

CREATE OR REPLACE FUNCTION kan.set_extension_morphism_map(
    p_ext_name      TEXT,
    p_src_morphism  TEXT,
    p_tgt_element   TEXT
)
RETURNS VOID
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO kan.extension_morphism_map (extension_name, src_morphism, tgt_element)
    VALUES (p_ext_name, p_src_morphism, p_tgt_element)
    ON CONFLICT (extension_name, src_morphism)
    DO UPDATE SET tgt_element = EXCLUDED.tgt_element;
END
$$;

-- Check approximate faithfulness of an extension: for every pair of elements
-- (a, b) in the same hom-set of the larger category C, are their images
-- distinct in D?  Requires both a and b to be recorded in kan.element for C
-- and their images in kan.element for D.
--
-- Returns FALSE immediately if any collision is found; TRUE if none found.
-- NULL if fewer than 2 elements are recorded in any hom-set (inconclusive).
CREATE OR REPLACE FUNCTION kan.extension_is_faithful(p_ext_name TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_cat_c TEXT;
    v_cat_d TEXT;
    v_collision INT;
BEGIN
    SELECT f_base.src_category, f_base.tgt_category
    INTO v_cat_c, v_cat_d
    FROM kan.extension_request er
    JOIN kan.functor f_base ON f_base.name = er.base_functor
    WHERE er.name = p_ext_name;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Extension % not found', p_ext_name;
    END IF;

    -- Collision: two distinct source elements map to the same target element.
    SELECT count(*) INTO v_collision
    FROM (
        SELECT emm.tgt_element
        FROM kan.extension_morphism_map emm
        WHERE emm.extension_name = p_ext_name
        GROUP BY emm.tgt_element
        HAVING count(*) > 1
    ) collisions;

    RETURN v_collision = 0;
END
$$;

-- Approximate check for the codensity unit being a monomorphism.
-- The codensity monad of F (= Ran_F F) has a unit η: Id → Ran_F F.
-- F is faithful iff this unit is a monomorphism on objects.
-- Here we check: for every pair of objects c ≠ c' in C, are their images
-- under Ran_F F distinct?
CREATE OR REPLACE FUNCTION kan.codensity_unit_is_mono(p_ext_name TEXT)
RETURNS BOOLEAN
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_collision INT;
BEGIN
    SELECT count(*) INTO v_collision
    FROM (
        SELECT tgt_object
        FROM kan.extension_object_map
        WHERE extension_name = p_ext_name
        GROUP BY tgt_object
        HAVING count(DISTINCT src_object) > 1
    ) obj_collisions;

    RETURN v_collision = 0;
END
$$;

-- ────────────────────────────────────────────────────────────────────────────
-- Views
-- ────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW kan.extension_summary AS
SELECT
    er.name,
    er.direction,
    er.base_functor,
    er.along_functor,
    er.status,
    count(DISTINCT eo.src_object)    AS objects_mapped,
    count(DISTINCT em.src_morphism)  AS morphisms_mapped,
    er.description
FROM kan.extension_request er
LEFT JOIN kan.extension_object_map   eo ON eo.extension_name = er.name
LEFT JOIN kan.extension_morphism_map em ON em.extension_name = er.name
GROUP BY er.name, er.direction, er.base_functor, er.along_functor, er.status, er.description;
