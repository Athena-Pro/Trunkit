-- KAN expansion, layer 5: profunctors and the Yoneda embedding.
--
-- A profunctor (distributor) H : C ↛ D is a functor  D^op × C → Set.
-- It generalises both functors (every functor F: C → D gives a profunctor
-- Hom_D(−, F(−))) and relations (every relation gives a Boolean profunctor).
--
-- Profunctors compose via coends:
--
--   (K ● H)(d, c) = ∫^e H(d, e) × K(e, c)
--
-- For finite categories with full element tables this is:
--
--   ∐_{e ∈ ob(C)} H(d, e) × K(e, c)  /  ~
--
-- where ~ coequates (H(f,id)(h), k) with (h, K(id,f)(k)) for each morphism
-- f in the intermediate category.
--
-- The Yoneda embedding  y: C → Set^{C^op}  sends c ↦ Hom_C(−, c).
-- It is always faithful and full; every faithful representation factors
-- through it.  In the calx context this gives:
--   • A canonical "universal" representation of any category we register.
--   • A reference point for comparing non-universal faithful representations.
--   • The lower-bound argument: dim of any faithful rep ≥ dim of the
--     smallest summand of the Yoneda decomposition.
--
-- Tables
-- ------
--   kan.profunctor       – (name, src_cat C, tgt_cat D, description)
--   kan.profunctor_cell  – one entry H(d, c) per (d ∈ D, c ∈ C)
--
-- Functions
-- ---------
--   kan.register_profunctor(name, src_cat, tgt_cat)
--   kan.set_profunctor_cell(prof_name, tgt_obj d, src_obj c, element_name)
--   kan.hom_profunctor(cat)        – populates Hom_C as a profunctor C ↛ C
--   kan.yoneda_embed(cat)          – for each object c, creates Hom(−,c) as
--                                     a separate profunctor
--   kan.profunctor_compose(H, K, result_name)
--                                  – records K ● H; finite cats only
--
-- Idempotent: CREATE ... IF NOT EXISTS; functions are CREATE OR REPLACE.

CREATE TABLE IF NOT EXISTS kan.profunctor (
    name        TEXT PRIMARY KEY,
    src_cat     TEXT NOT NULL REFERENCES kan.category(name) ON DELETE CASCADE,
    tgt_cat     TEXT NOT NULL REFERENCES kan.category(name) ON DELETE CASCADE,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One cell per (target-category object d, source-category object c).
-- element_name references a kan.element (or kan.linear_element) that represents
-- the set / module H(d, c).  For Boolean profunctors this is a single element.
-- For K-linear profunctors this is a kan.linear_element name.
CREATE TABLE IF NOT EXISTS kan.profunctor_cell (
    profunctor_name TEXT NOT NULL REFERENCES kan.profunctor(name) ON DELETE CASCADE,
    tgt_object      TEXT NOT NULL,   -- d ∈ tgt_cat (contravariant slot)
    src_object      TEXT NOT NULL,   -- c ∈ src_cat (covariant slot)
    element_name    TEXT NOT NULL,   -- value H(d, c) as a kan.element name
    PRIMARY KEY (profunctor_name, tgt_object, src_object)
);

-- ────────────────────────────────────────────────────────────────────────────
-- Functions
-- ────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION kan.register_profunctor(
    p_name      TEXT,
    p_src_cat   TEXT,
    p_tgt_cat   TEXT,
    p_description TEXT DEFAULT NULL
)
RETURNS VOID
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO kan.profunctor (name, src_cat, tgt_cat, description)
    VALUES (p_name, p_src_cat, p_tgt_cat, p_description)
    ON CONFLICT (name)
    DO UPDATE SET src_cat     = EXCLUDED.src_cat,
                  tgt_cat     = EXCLUDED.tgt_cat,
                  description = EXCLUDED.description;
END
$$;

CREATE OR REPLACE FUNCTION kan.set_profunctor_cell(
    p_prof_name  TEXT,
    p_tgt_object TEXT,
    p_src_object TEXT,
    p_element    TEXT
)
RETURNS VOID
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO kan.profunctor_cell (profunctor_name, tgt_object, src_object, element_name)
    VALUES (p_prof_name, p_tgt_object, p_src_object, p_element)
    ON CONFLICT (profunctor_name, tgt_object, src_object)
    DO UPDATE SET element_name = EXCLUDED.element_name;
END
$$;

-- Populate the hom profunctor Hom_C: C ↛ C where Hom_C(d,c) = all elements
-- in the hom-set from d to c.  One cell per (d, c) pair that has at least
-- one recorded element.  Uses the *first* recorded element as the cell value
-- (for Boolean/single-element hom-sets).  For K-linear hom-sets populate
-- cells manually or extend this function with kan.linear_element logic.
CREATE OR REPLACE FUNCTION kan.hom_profunctor(p_cat TEXT)
RETURNS TABLE (cells_created INT)
LANGUAGE plpgsql AS $$
DECLARE v_n INT := 0;
BEGIN
    PERFORM kan.register_profunctor(
        'Hom_' || p_cat,
        p_cat,
        p_cat,
        'The representable hom-profunctor Hom_C: C ↛ C  (identity profunctor)'
    );

    INSERT INTO kan.profunctor_cell
        (profunctor_name, tgt_object, src_object, element_name)
    SELECT DISTINCT ON (e.codomain, e.domain)
        'Hom_' || p_cat,
        e.codomain,   -- d (contravariant)
        e.domain,     -- c (covariant)
        e.name
    FROM kan.element e
    WHERE e.category = p_cat
    ON CONFLICT (profunctor_name, tgt_object, src_object) DO NOTHING;
    GET DIAGNOSTICS v_n = ROW_COUNT;

    RETURN QUERY SELECT v_n;
END
$$;

-- For each object c of p_cat, register the representable profunctor
-- y(c) = Hom_C(−, c): C ↛ {*}.  The cells y(c)(d) = elements from d to c.
-- One profunctor per object; named 'yoneda_<cat>_<object>'.
CREATE OR REPLACE FUNCTION kan.yoneda_embed(p_cat TEXT)
RETURNS TABLE (profunctors_created INT, cells_created INT)
LANGUAGE plpgsql AS $$
DECLARE
    v_prof_n INT := 0;
    v_cell_n INT := 0;
    v_obj    TEXT;
    v_name   TEXT;
    v_delta  INT;
BEGIN
    FOR v_obj IN SELECT DISTINCT name FROM kan.object WHERE category = p_cat LOOP
        v_name := 'yoneda_' || p_cat || '_' || v_obj;

        INSERT INTO kan.profunctor (name, src_cat, tgt_cat, description)
        VALUES (
            v_name, p_cat, p_cat,
            'Yoneda representable at ' || v_obj || ': Hom_C(−, ' || v_obj || ')'
        )
        ON CONFLICT (name) DO NOTHING;

        IF FOUND THEN v_prof_n := v_prof_n + 1; END IF;

        INSERT INTO kan.profunctor_cell
            (profunctor_name, tgt_object, src_object, element_name)
        SELECT DISTINCT ON (e.domain)
            v_name,
            v_obj,       -- all cells have codomain = v_obj (contravariant slot)
            e.domain,
            e.name
        FROM kan.element e
        WHERE e.category = p_cat
          AND e.codomain = v_obj
        ON CONFLICT (profunctor_name, tgt_object, src_object) DO NOTHING;
        GET DIAGNOSTICS v_delta = ROW_COUNT;
        v_cell_n := v_cell_n + v_delta;
    END LOOP;

    RETURN QUERY SELECT v_prof_n, v_cell_n;
END
$$;

-- Compose two profunctors K ● H where H: C ↛ E and K: E ↛ D.
-- Result profunctor: C ↛ D.
-- For each (d, c) pair: (K ● H)(d, c) = ∐_{e} H(e, c) × K(d, e) / ~
-- This implementation records one cell per (d, c) pair that has at least
-- one contributing (e, h, k) triple.  The element stored is a concatenated
-- name for traceability; full coend quotienting requires Python-side logic.
CREATE OR REPLACE FUNCTION kan.profunctor_compose(
    p_H_name      TEXT,    -- inner profunctor H: C ↛ E
    p_K_name      TEXT,    -- outer profunctor K: E ↛ D
    p_result_name TEXT
)
RETURNS TABLE (cells_created INT)
LANGUAGE plpgsql AS $$
DECLARE
    v_n   INT := 0;
    v_delta INT;
BEGIN
    -- Validate that the intermediate categories match.
    IF (SELECT tgt_cat FROM kan.profunctor WHERE name = p_H_name) <>
       (SELECT src_cat FROM kan.profunctor WHERE name = p_K_name) THEN
        RAISE EXCEPTION
            'Profunctor composition type error: tgt_cat(H) ≠ src_cat(K)';
    END IF;

    PERFORM kan.register_profunctor(
        p_result_name,
        (SELECT src_cat FROM kan.profunctor WHERE name = p_H_name),
        (SELECT tgt_cat FROM kan.profunctor WHERE name = p_K_name),
        '(' || p_K_name || ') ● (' || p_H_name || ')'
    );

    INSERT INTO kan.profunctor_cell
        (profunctor_name, tgt_object, src_object, element_name)
    SELECT DISTINCT ON (k.tgt_object, h.src_object)
        p_result_name,
        k.tgt_object,
        h.src_object,
        h.element_name || '__x__' || k.element_name  -- coend witness label
    FROM kan.profunctor_cell h
    JOIN kan.profunctor_cell k
        ON k.src_object = h.tgt_object   -- intermediate object e
    WHERE h.profunctor_name = p_H_name
      AND k.profunctor_name = p_K_name
    ON CONFLICT (profunctor_name, tgt_object, src_object) DO NOTHING;
    GET DIAGNOSTICS v_n = ROW_COUNT;

    RETURN QUERY SELECT v_n;
END
$$;

-- ────────────────────────────────────────────────────────────────────────────
-- Views
-- ────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW kan.profunctor_summary AS
SELECT
    p.name,
    p.src_cat,
    p.tgt_cat,
    count(pc.profunctor_name) AS cells_recorded,
    p.description
FROM kan.profunctor p
LEFT JOIN kan.profunctor_cell pc ON pc.profunctor_name = p.name
GROUP BY p.name, p.src_cat, p.tgt_cat, p.description;
