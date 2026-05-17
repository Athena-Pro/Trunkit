-- KAN expansion, layer 4: enriched categories.
--
-- A V-enriched category C has hom-objects C(a,b) ∈ V rather than hom-sets.
-- The calx system needs enrichment to represent:
--
--   K-linear categories  P(K,δ), B(K,δ), TL(K,δ)
--     hom-sets are free K-modules on the diagram basis; K is a commutative ring.
--     (The paper's diagram categories linearised over K with loop parameter δ.)
--
--   Boolean / idempotent semirings
--     For calx relations: K = 𝔹 with 1+1=1.  The representation ā of a
--     partition is a Boolean matrix (Section 2 of arXiv:2605.04630v1).
--
--   Tropical semiring
--     K = (ℤ ∪ {∞}, min, +).  Useful for degree/complexity lower bounds.
--
--   Characteristic-p fields
--     K = GF(p) or GF(p^n).  Needed for Open Question 8.3 of the paper.
--
-- Design
-- ------
-- • kan.enrichment records the ground ring meta-data per category.
-- • kan.linear_element stores a formal K-linear combination of kan.element rows.
-- • kan.lc_term is the term table (one row per (linear_element, basis_element)).
-- • kan.linearise_functor wraps an existing set-functor as a K-linear one by
--   extending the object map and recording that hom-sets are now K-modules.
--
-- Idempotent: CREATE ... IF NOT EXISTS; functions are CREATE OR REPLACE.

CREATE TABLE IF NOT EXISTS kan.enrichment (
    category           TEXT PRIMARY KEY REFERENCES kan.category(name) ON DELETE CASCADE,
    ground_ring        TEXT NOT NULL,            -- 'Z', 'Q', 'GF(2)', 'Bool', 'Tropical', etc.
    characteristic     INT  NOT NULL DEFAULT 0,  -- 0 = char 0; p > 0 = char p
    idempotent_add     BOOLEAN NOT NULL DEFAULT FALSE,  -- TRUE when 1+1=1 (Bool, Tropical)
    loop_parameter     TEXT,    -- δ value if category is a linearised diagram cat
    description        TEXT
);

-- A formal K-linear combination: Σ_i  coeff_i · element_i
CREATE TABLE IF NOT EXISTS kan.linear_element (
    id         BIGSERIAL PRIMARY KEY,
    category   TEXT NOT NULL REFERENCES kan.category(name) ON DELETE CASCADE,
    name       TEXT NOT NULL,    -- human label
    domain     TEXT NOT NULL,
    codomain   TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (category, name)
);

-- Each term of the linear combination.
-- coefficient is stored as JSONB so it can represent integers, rationals,
-- GF(p) elements, or symbolic expressions uniformly.
CREATE TABLE IF NOT EXISTS kan.lc_term (
    id                BIGSERIAL PRIMARY KEY,
    linear_element_id BIGINT NOT NULL REFERENCES kan.linear_element(id) ON DELETE CASCADE,
    basis_element     TEXT   NOT NULL,   -- element name in kan.element
    coefficient       JSONB  NOT NULL DEFAULT '1',  -- e.g. 1, {"p":3,"q":4}, "2+3i"
    UNIQUE (linear_element_id, basis_element)
);

-- ────────────────────────────────────────────────────────────────────────────
-- Functions
-- ────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION kan.register_enrichment(
    p_category       TEXT,
    p_ground_ring    TEXT,
    p_characteristic INT     DEFAULT 0,
    p_idempotent_add BOOLEAN DEFAULT FALSE,
    p_loop_parameter TEXT    DEFAULT NULL,
    p_description    TEXT    DEFAULT NULL
)
RETURNS VOID
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO kan.enrichment
        (category, ground_ring, characteristic, idempotent_add, loop_parameter, description)
    VALUES (p_category, p_ground_ring, p_characteristic, p_idempotent_add,
            p_loop_parameter, p_description)
    ON CONFLICT (category)
    DO UPDATE SET ground_ring    = EXCLUDED.ground_ring,
                  characteristic  = EXCLUDED.characteristic,
                  idempotent_add  = EXCLUDED.idempotent_add,
                  loop_parameter  = EXCLUDED.loop_parameter,
                  description     = EXCLUDED.description;
END
$$;

-- Create a named linear combination from a coefficient map.
-- p_terms: JSON object mapping element_name → coefficient, e.g.
--   '{"id_0": 1, "a_1_2": 3}'::JSONB
-- Returns the new linear_element id.
CREATE OR REPLACE FUNCTION kan.linear_combine(
    p_category TEXT,
    p_name     TEXT,
    p_domain   TEXT,
    p_codomain TEXT,
    p_terms    JSONB   -- {element_name: coefficient, ...}
)
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE
    v_id     BIGINT;
    v_elem   TEXT;
    v_coeff  JSONB;
BEGIN
    INSERT INTO kan.linear_element (category, name, domain, codomain)
    VALUES (p_category, p_name, p_domain, p_codomain)
    ON CONFLICT (category, name)
    DO UPDATE SET domain   = EXCLUDED.domain,
                  codomain = EXCLUDED.codomain
    RETURNING id INTO v_id;

    -- Clear and reinsert terms for idempotency.
    DELETE FROM kan.lc_term WHERE linear_element_id = v_id;

    FOR v_elem, v_coeff IN
        SELECT key, value FROM jsonb_each(p_terms)
    LOOP
        INSERT INTO kan.lc_term (linear_element_id, basis_element, coefficient)
        VALUES (v_id, v_elem, v_coeff);
    END LOOP;

    RETURN v_id;
END
$$;

-- Declare that an existing functor acts K-linearly between enriched categories.
-- This adds a kan.enrichment row for the target category if not present, and
-- sets the functor's description to note the linear extension.
CREATE OR REPLACE FUNCTION kan.linearise_functor(
    p_functor_name TEXT,
    p_ground_ring  TEXT,
    p_characteristic INT DEFAULT 0,
    p_idempotent_add BOOLEAN DEFAULT FALSE
)
RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE
    v_src TEXT;
    v_tgt TEXT;
BEGIN
    SELECT src_category, tgt_category
    INTO v_src, v_tgt
    FROM kan.functor
    WHERE name = p_functor_name;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Functor % not found', p_functor_name;
    END IF;

    PERFORM kan.register_enrichment(v_src, p_ground_ring, p_characteristic, p_idempotent_add);
    PERFORM kan.register_enrichment(v_tgt, p_ground_ring, p_characteristic, p_idempotent_add);

    UPDATE kan.functor
    SET description = coalesce(description, '') ||
        ' [K-linear over ' || p_ground_ring || ']'
    WHERE name = p_functor_name;
END
$$;

-- Evaluate whether a category's enrichment is compatible with the paper's
-- requirement for functoriality: K must be additively idempotent (1+1=1).
-- Returns TRUE if the category's enrichment has idempotent_add = TRUE.
CREATE OR REPLACE FUNCTION kan.is_idempotent_semiring(p_category TEXT)
RETURNS BOOLEAN
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(idempotent_add, FALSE)
    FROM kan.enrichment
    WHERE category = p_category;
$$;

-- ────────────────────────────────────────────────────────────────────────────
-- Views
-- ────────────────────────────────────────────────────────────────────────────

-- Summary of enriched categories with hom-set cardinalities.
CREATE OR REPLACE VIEW kan.enrichment_summary AS
SELECT
    e.category,
    e.ground_ring,
    e.characteristic,
    e.idempotent_add,
    e.loop_parameter,
    count(DISTINCT le.id)   AS linear_elements_recorded,
    count(DISTINCT t.id)    AS total_terms
FROM kan.enrichment e
LEFT JOIN kan.linear_element le ON le.category = e.category
LEFT JOIN kan.lc_term t ON t.linear_element_id = le.id
GROUP BY e.category, e.ground_ring, e.characteristic, e.idempotent_add, e.loop_parameter;

-- Expose the full expansion of each linear element.
CREATE OR REPLACE VIEW kan.linear_element_expansion AS
SELECT
    le.category,
    le.name      AS linear_element,
    le.domain,
    le.codomain,
    t.basis_element,
    t.coefficient,
    e.payload    AS basis_payload
FROM kan.linear_element le
JOIN kan.lc_term t ON t.linear_element_id = le.id
JOIN kan.element e ON e.category = le.category AND e.name = t.basis_element
ORDER BY le.category, le.name, t.basis_element;
