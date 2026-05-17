-- KAN expansion, layer 1: monoidal structure and involution (dagger).
--
-- Adds tensor products and the * (dagger / anti-involution) operation to
-- KAN-registered categories.  These are the two structural features shared by
-- the diagram categories P, B, TL and the matrix category M(K):
--
--   Tensor / monoidal structure
--   ---------------------------
--   A strict monoidal structure on C is (⊗: C×C→C, I, α=id, λ=id, ρ=id).
--   Objects compose additively (m⊕n = m+n for diagram categories) or
--   multiplicatively (m⊗n = m·n for Kronecker in M(K)).
--   Morphisms: (a⊗b) ∈ P_{m+p, n+q} for a∈P_{m,n}, b∈P_{p,q} (horizontal stacking).
--
--   Involution / dagger
--   -------------------
--   A *-category or dagger category has an involutive functor *: C^op → C
--   satisfying (a*)* = a and (b∘a)* = a*∘b*.
--   For partitions: a* = vertical reflection relabelling i↔i'.
--   For matrices:   a* = transpose (or conjugate-transpose over ℂ).
--
-- Tables
-- ------
--   kan.monoidal_structure  – one row per (category, tensor_op_name)
--   kan.tensor_product      – recorded result of a⊗b
--   kan.involution_result   – recorded result of a*
--
-- Functions
-- ---------
--   kan.register_monoidal(cat, op_name, unit_obj, strict)
--   kan.record_tensor(cat, op, a, b, result_name)
--   kan.record_involution(cat, element_name, dual_name)
--   kan.is_dagger_functor(functor_name) → boolean
--
-- Idempotent: CREATE ... IF NOT EXISTS; functions are CREATE OR REPLACE.

CREATE TABLE IF NOT EXISTS kan.monoidal_structure (
    category    TEXT NOT NULL REFERENCES kan.category(name) ON DELETE CASCADE,
    op_name     TEXT NOT NULL,   -- e.g. 'oplus' (additive) or 'otimes' (Kronecker)
    unit_object TEXT NOT NULL,   -- e.g. '0' or '1'
    is_strict   BOOLEAN NOT NULL DEFAULT TRUE,
    description TEXT,
    PRIMARY KEY (category, op_name)
);

-- Recorded tensor product results: a ⊗ b = result (all within the same category).
-- result_name must be a name in kan.element.
CREATE TABLE IF NOT EXISTS kan.tensor_product (
    id           BIGSERIAL   PRIMARY KEY,
    category     TEXT        NOT NULL REFERENCES kan.category(name) ON DELETE CASCADE,
    op_name      TEXT        NOT NULL,
    left_name    TEXT        NOT NULL,   -- element name for a
    right_name   TEXT        NOT NULL,  -- element name for b
    result_name  TEXT        NOT NULL,  -- element name for a⊗b
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (category, op_name, left_name, right_name),
    FOREIGN KEY (category, op_name)    REFERENCES kan.monoidal_structure(category, op_name) ON DELETE CASCADE,
    FOREIGN KEY (category, left_name)  REFERENCES kan.element(category, name) ON DELETE CASCADE,
    FOREIGN KEY (category, right_name) REFERENCES kan.element(category, name) ON DELETE CASCADE,
    FOREIGN KEY (category, result_name)REFERENCES kan.element(category, name) ON DELETE CASCADE
);

-- Recorded involution (dagger) results: a* = dual.
CREATE TABLE IF NOT EXISTS kan.involution_result (
    category     TEXT NOT NULL REFERENCES kan.category(name) ON DELETE CASCADE,
    element_name TEXT NOT NULL,
    dual_name    TEXT NOT NULL,
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (category, element_name),
    FOREIGN KEY (category, element_name) REFERENCES kan.element(category, name) ON DELETE CASCADE,
    FOREIGN KEY (category, dual_name)    REFERENCES kan.element(category, name) ON DELETE CASCADE
);

-- ────────────────────────────────────────────────────────────────────────────
-- Functions
-- ────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION kan.register_monoidal(
    p_category    TEXT,
    p_op_name     TEXT,
    p_unit_object TEXT,
    p_strict      BOOLEAN DEFAULT TRUE,
    p_description TEXT    DEFAULT NULL
)
RETURNS VOID
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO kan.monoidal_structure (category, op_name, unit_object, is_strict, description)
    VALUES (p_category, p_op_name, p_unit_object, p_strict, p_description)
    ON CONFLICT (category, op_name)
    DO UPDATE SET unit_object = EXCLUDED.unit_object,
                  is_strict   = EXCLUDED.is_strict,
                  description = EXCLUDED.description;
END
$$;

-- Record a ⊗ b = result for a given monoidal operation.
-- Idempotent: updates result_name if the (category, op, left, right) key exists.
CREATE OR REPLACE FUNCTION kan.record_tensor(
    p_category    TEXT,
    p_op_name     TEXT,
    p_left_name   TEXT,
    p_right_name  TEXT,
    p_result_name TEXT
)
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE v_id BIGINT;
BEGIN
    INSERT INTO kan.tensor_product (category, op_name, left_name, right_name, result_name)
    VALUES (p_category, p_op_name, p_left_name, p_right_name, p_result_name)
    ON CONFLICT (category, op_name, left_name, right_name)
    DO UPDATE SET result_name = EXCLUDED.result_name
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

-- Record the dual of an element under the category's involution.
-- Idempotent: updates dual_name if the element row exists.
CREATE OR REPLACE FUNCTION kan.record_involution(
    p_category     TEXT,
    p_element_name TEXT,
    p_dual_name    TEXT
)
RETURNS VOID
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO kan.involution_result (category, element_name, dual_name)
    VALUES (p_category, p_element_name, p_dual_name)
    ON CONFLICT (category, element_name)
    DO UPDATE SET dual_name = EXCLUDED.dual_name;
END
$$;

-- Check whether a functor F preserves the involution: F(a*) = F(a)* for all
-- elements where both a* (in source) and F(a)* (in target) are recorded.
-- Returns rows where the condition FAILS, or an empty result if verified.
CREATE OR REPLACE FUNCTION kan.check_dagger_functor(p_functor TEXT)
RETURNS TABLE (
    element_name    TEXT,
    expected_dual   TEXT,   -- F(a*) — image of the source dual
    actual_dual     TEXT,   -- F(a)* — involution of the image
    match           BOOLEAN
)
LANGUAGE sql STABLE AS $$
    -- For each source element e with a recorded involution e*,
    -- check that F(e*) = F(e)*.
    -- We approximate F's action via kan.functor_object_map (objects only for now;
    -- element-level functor action will be added with 24_kan_natural_transformations).
    SELECT
        ir_src.element_name,
        ir_src.dual_name    AS expected_dual,
        ir_tgt.dual_name    AS actual_dual,
        ir_src.dual_name = ir_tgt.dual_name AS match
    FROM kan.functor f
    JOIN kan.involution_result ir_src
        ON ir_src.category = f.src_category
    -- The target element is the image of the source element under the functor.
    -- Element-level functor maps are stored as kan.element rows whose name follows
    -- the convention "<functor>:<source_element_name>".  Adjust the join if you
    -- record functor element maps differently.
    JOIN kan.involution_result ir_tgt
        ON ir_tgt.category    = f.tgt_category
       AND ir_tgt.element_name = f.name || ':' || ir_src.element_name
    WHERE f.name = p_functor;
$$;

-- ────────────────────────────────────────────────────────────────────────────
-- Views
-- ────────────────────────────────────────────────────────────────────────────

-- All self-dual elements (a* = a, i.e. a is hermitian / symmetric).
CREATE OR REPLACE VIEW kan.self_dual_elements AS
SELECT category, element_name
FROM kan.involution_result
WHERE element_name = dual_name;

-- Summary of monoidal structure per category.
CREATE OR REPLACE VIEW kan.monoidal_summary AS
SELECT
    ms.category,
    ms.op_name,
    ms.unit_object,
    ms.is_strict,
    count(tp.id) AS recorded_tensor_products
FROM kan.monoidal_structure ms
LEFT JOIN kan.tensor_product tp
    ON tp.category = ms.category AND tp.op_name = ms.op_name
GROUP BY ms.category, ms.op_name, ms.unit_object, ms.is_strict;
