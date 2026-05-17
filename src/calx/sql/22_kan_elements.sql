-- KAN expansion, layer 0b: first-class element (morphism datum) storage.
--
-- kan.morphism records the *generating* morphisms of a Postgres schema derived
-- from FK constraints — it captures the schema graph, not mathematical content.
-- kan.element stores actual mathematical objects: partition block-sets, matrix
-- entries, integer tuples, or any other datum that inhabits a hom-set.  This is
-- the prerequisite data layer for all higher KAN tools (layers 1–6).
--
-- Design decisions
-- ----------------
-- • domain / codomain are free TEXT references to category objects.  They are
--   intentionally not FK-constrained so that virtual objects (e.g. "2" meaning
--   Z_2) can be referenced before the full object table is populated.
-- • payload is JSONB.  Every category defines its own schema within the JSONB.
--   Examples:
--     partition in P_{2,3}: {"blocks": [[1,3],[2],[4,5,6]]}
--     matrix in M_2(K):     {"rows": [[1,0],[0,1]]}
--     integer in calx:      {"n": 42}
-- • composition is recorded in kan.composition so every computed product is
--   stored and re-usable; the function returns the resulting element name.
-- • identity elements are tracked in kan.element_identity (one per object per
--   category).
--
-- Idempotent: all CREATE ... IF NOT EXISTS; functions are CREATE OR REPLACE.

CREATE TABLE IF NOT EXISTS kan.element (
    id          BIGSERIAL   PRIMARY KEY,
    category    TEXT        NOT NULL REFERENCES kan.category(name) ON DELETE CASCADE,
    name        TEXT        NOT NULL,                -- human label, unique within category
    domain      TEXT        NOT NULL,                -- domain object name
    codomain    TEXT        NOT NULL,                -- codomain object name
    payload     JSONB       NOT NULL DEFAULT '{}',   -- the datum
    meta        JSONB       NOT NULL DEFAULT '{}',   -- provenance / tags
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (category, name)
);

CREATE INDEX IF NOT EXISTS kan_element_cat_dom_cod
    ON kan.element (category, domain, codomain);

-- Stores the result of composing element a (domain→mid) with element b (mid→cod).
-- Both source and result must be recorded in kan.element.
CREATE TABLE IF NOT EXISTS kan.composition (
    id          BIGSERIAL   PRIMARY KEY,
    category    TEXT        NOT NULL REFERENCES kan.category(name) ON DELETE CASCADE,
    left_name   TEXT        NOT NULL,                -- element name for a (applied first)
    right_name  TEXT        NOT NULL,                -- element name for b (applied second)
    result_name TEXT        NOT NULL,                -- element name for b∘a
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (category, left_name, right_name),
    FOREIGN KEY (category, left_name)   REFERENCES kan.element(category, name) ON DELETE CASCADE,
    FOREIGN KEY (category, right_name)  REFERENCES kan.element(category, name) ON DELETE CASCADE,
    FOREIGN KEY (category, result_name) REFERENCES kan.element(category, name) ON DELETE CASCADE
);

-- One identity element per (category, object).
CREATE TABLE IF NOT EXISTS kan.element_identity (
    category     TEXT NOT NULL REFERENCES kan.category(name) ON DELETE CASCADE,
    object_name  TEXT NOT NULL,
    element_name TEXT NOT NULL,
    PRIMARY KEY (category, object_name),
    FOREIGN KEY (category, element_name) REFERENCES kan.element(category, name) ON DELETE CASCADE
);

-- ────────────────────────────────────────────────────────────────────────────
-- Functions
-- ────────────────────────────────────────────────────────────────────────────

-- Record a single element.  Returns the assigned id.
-- Idempotent on (category, name): updates payload/meta if the row exists.
CREATE OR REPLACE FUNCTION kan.upsert_element(
    p_category TEXT,
    p_name     TEXT,
    p_domain   TEXT,
    p_codomain TEXT,
    p_payload  JSONB DEFAULT '{}',
    p_meta     JSONB DEFAULT '{}'
)
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE v_id BIGINT;
BEGIN
    INSERT INTO kan.element (category, name, domain, codomain, payload, meta)
    VALUES (p_category, p_name, p_domain, p_codomain, p_payload, p_meta)
    ON CONFLICT (category, name)
    DO UPDATE SET payload = EXCLUDED.payload,
                  meta    = EXCLUDED.meta,
                  domain  = EXCLUDED.domain,
                  codomain= EXCLUDED.codomain
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

-- Record the identity element for a given object.
CREATE OR REPLACE FUNCTION kan.set_identity(
    p_category    TEXT,
    p_object_name TEXT,
    p_element_name TEXT
)
RETURNS VOID
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO kan.element_identity (category, object_name, element_name)
    VALUES (p_category, p_object_name, p_element_name)
    ON CONFLICT (category, object_name)
    DO UPDATE SET element_name = EXCLUDED.element_name;
END
$$;

-- Look up the identity element name for a given object, or NULL if not set.
CREATE OR REPLACE FUNCTION kan.identity_for(
    p_category   TEXT,
    p_object_name TEXT
)
RETURNS TEXT
LANGUAGE sql STABLE AS $$
    SELECT element_name
      FROM kan.element_identity
     WHERE category    = p_category
       AND object_name = p_object_name;
$$;

-- Record a composition result.  All three elements must already exist.
-- Returns the result element id.  Idempotent on (category, left_name, right_name).
CREATE OR REPLACE FUNCTION kan.record_composition(
    p_category    TEXT,
    p_left_name   TEXT,
    p_right_name  TEXT,
    p_result_name TEXT
)
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE v_id BIGINT;
BEGIN
    INSERT INTO kan.composition (category, left_name, right_name, result_name)
    VALUES (p_category, p_left_name, p_right_name, p_result_name)
    ON CONFLICT (category, left_name, right_name)
    DO UPDATE SET result_name = EXCLUDED.result_name
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

-- Retrieve the result of a composition, or NULL if not yet computed.
CREATE OR REPLACE FUNCTION kan.lookup_composition(
    p_category   TEXT,
    p_left_name  TEXT,
    p_right_name TEXT
)
RETURNS TEXT
LANGUAGE sql STABLE AS $$
    SELECT result_name
      FROM kan.composition
     WHERE category   = p_category
       AND left_name  = p_left_name
       AND right_name = p_right_name;
$$;

-- ────────────────────────────────────────────────────────────────────────────
-- Views
-- ────────────────────────────────────────────────────────────────────────────

-- All hom-sets as labelled lists of element names.
CREATE OR REPLACE VIEW kan.hom_set AS
SELECT
    category,
    domain,
    codomain,
    count(*)                AS element_count,
    array_agg(name ORDER BY name) AS elements
FROM kan.element
GROUP BY category, domain, codomain;

-- All composition results with full element payloads.
CREATE OR REPLACE VIEW kan.composition_table AS
SELECT
    c.category,
    c.left_name,
    la.payload  AS left_payload,
    c.right_name,
    ra.payload  AS right_payload,
    c.result_name,
    re.payload  AS result_payload
FROM kan.composition c
JOIN kan.element la ON la.category = c.category AND la.name = c.left_name
JOIN kan.element ra ON ra.category = c.category AND ra.name = c.right_name
JOIN kan.element re ON re.category = c.category AND re.name = c.result_name;
