-- Unified model, step 2: the `kan` schema — the meta-layer.
--
-- Every Postgres schema in this database is a finitely-presented category:
-- objects are its tables, generating morphisms are its foreign keys, identities
-- and composites are implicit. `kan` records that structure as data and the
-- functors between categories, so the categorical view is queryable in SQL.
--
-- Idempotent: CREATE ... IF NOT EXISTS; kan.sync_category() is CREATE OR REPLACE.

-- db_schema / table_name are NULL for *abstract* categories (explicit mode:
-- TL, P, B of arXiv:2605.04630v1) that are not backed by a Postgres schema.
-- Reflection mode (kan.sync_category) always supplies non-null values.
CREATE TABLE IF NOT EXISTS kan.category (
    name        TEXT PRIMARY KEY,         -- 'calx' | 'curry' | 'kan' | 'TL' | ...
    db_schema   TEXT,                     -- backing Postgres schema, or NULL if abstract
    description TEXT
);

CREATE TABLE IF NOT EXISTS kan.object (
    category   TEXT NOT NULL REFERENCES kan.category(name) ON DELETE CASCADE,
    name       TEXT NOT NULL,             -- object name (== table name when reflected)
    table_name TEXT,                      -- NULL when the object is abstract
    PRIMARY KEY (category, name)
);

-- Self-heal databases created before these columns were made nullable
-- (idempotent: DROP NOT NULL on an already-nullable column is a harmless no-op).
ALTER TABLE kan.category ALTER COLUMN db_schema  DROP NOT NULL;
ALTER TABLE kan.object   ALTER COLUMN table_name DROP NOT NULL;

CREATE TABLE IF NOT EXISTS kan.morphism (
    category    TEXT NOT NULL REFERENCES kan.category(name) ON DELETE CASCADE,
    name        TEXT NOT NULL,            -- FK constraint name
    src_object  TEXT NOT NULL,
    tgt_object  TEXT NOT NULL,
    fk_columns  TEXT[] NOT NULL,
    pk_columns  TEXT[] NOT NULL,
    PRIMARY KEY (category, name),
    FOREIGN KEY (category, src_object) REFERENCES kan.object(category, name) ON DELETE CASCADE,
    FOREIGN KEY (category, tgt_object) REFERENCES kan.object(category, name) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS kan.functor (
    name         TEXT PRIMARY KEY,
    src_category TEXT NOT NULL REFERENCES kan.category(name),
    tgt_category TEXT NOT NULL REFERENCES kan.category(name),
    description  TEXT
);

CREATE TABLE IF NOT EXISTS kan.functor_object_map (
    functor    TEXT NOT NULL REFERENCES kan.functor(name) ON DELETE CASCADE,
    src_object TEXT NOT NULL,
    tgt_object TEXT NOT NULL,
    PRIMARY KEY (functor, src_object)
);

CREATE TABLE IF NOT EXISTS kan.functor_morphism_path (
    functor      TEXT NOT NULL REFERENCES kan.functor(name) ON DELETE CASCADE,
    src_morphism TEXT NOT NULL,
    tgt_path     TEXT[] NOT NULL,         -- ordered list of target morphisms; {} = identity
    PRIMARY KEY (functor, src_morphism)
);

-- Reflect a Postgres schema into kan.object / kan.morphism.
-- Re-runnable: clears the category's existing objects/morphisms first.
CREATE OR REPLACE FUNCTION kan.sync_category(p_category TEXT, p_db_schema TEXT)
RETURNS TABLE (objects INT, morphisms INT)
LANGUAGE plpgsql AS $$
DECLARE
    n_obj INT;
    n_mor INT;
BEGIN
    INSERT INTO kan.category (name, db_schema)
    VALUES (p_category, p_db_schema)
    ON CONFLICT (name) DO UPDATE SET db_schema = EXCLUDED.db_schema;

    DELETE FROM kan.morphism WHERE category = p_category;
    DELETE FROM kan.object   WHERE category = p_category;

    INSERT INTO kan.object (category, name, table_name)
    SELECT p_category, c.relname, c.relname
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = p_db_schema
       AND c.relkind = 'r';
    GET DIAGNOSTICS n_obj = ROW_COUNT;

    INSERT INTO kan.morphism
        (category, name, src_object, tgt_object, fk_columns, pk_columns)
    SELECT p_category,
           con.conname,
           src.relname,
           tgt.relname,
           ARRAY(SELECT a.attname
                   FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
                   JOIN pg_attribute a
                     ON a.attrelid = con.conrelid AND a.attnum = k.attnum
                  ORDER BY k.ord),
           ARRAY(SELECT a.attname
                   FROM unnest(con.confkey) WITH ORDINALITY AS k(attnum, ord)
                   JOIN pg_attribute a
                     ON a.attrelid = con.confrelid AND a.attnum = k.attnum
                  ORDER BY k.ord)
      FROM pg_constraint con
      JOIN pg_class src      ON src.oid = con.conrelid
      JOIN pg_class tgt      ON tgt.oid = con.confrelid
      JOIN pg_namespace nsrc ON nsrc.oid = src.relnamespace
      JOIN pg_namespace ntgt ON ntgt.oid = tgt.relnamespace
     WHERE con.contype = 'f'
       AND nsrc.nspname = p_db_schema
       AND ntgt.nspname = p_db_schema;   -- intra-category FKs only
    GET DIAGNOSTICS n_mor = ROW_COUNT;

    RETURN QUERY SELECT n_obj, n_mor;
END
$$;

-- Single-argument convenience overload: schema name == category name.
-- Matches the SKILL.md examples (SELECT kan.sync_category('calx');).
CREATE OR REPLACE FUNCTION kan.sync_category(p_category TEXT)
RETURNS TABLE (objects INT, morphisms INT)
LANGUAGE sql AS $$
    SELECT * FROM kan.sync_category(p_category, p_category);
$$;

-- A readable presentation of any recorded category.
CREATE OR REPLACE VIEW kan.presentation AS
SELECT o.category,
       o.name                                   AS object,
       COALESCE(
           json_agg(json_build_object(
               'morphism', m.name,
               'to',       m.tgt_object,
               'fk',       m.fk_columns
           ) ORDER BY m.name) FILTER (WHERE m.name IS NOT NULL),
           '[]'::json
       )                                        AS outgoing
  FROM kan.object o
  LEFT JOIN kan.morphism m
         ON m.category = o.category AND m.src_object = o.name
 GROUP BY o.category, o.name;
