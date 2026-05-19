-- Unified model, step 3: inter-schema functors.
--
-- Defines kan.populate_curry_calx_functor(), which seeds the natural
-- curry → calx functor:
--
--   objects:   every curry.functions row whose name begins 'calx_' maps to
--              the calx SQL routine obtained by stripping that prefix, verified
--              against pg_proc so phantom wrappers are never recorded.
--
--   morphisms: every curry.function_dependencies edge between two calx-prefixed
--              functions records the dependency as a morphism whose target path
--              is {} (identity).  calx call-graph edges are implicit in the SQL
--              source and are not expressed as FK morphisms, so there is no
--              calx-side morphism to compose.
--
-- Design notes
-- ------------
-- The function (not a bare INSERT) is the right unit here: the functor row
-- references kan.category, which is only populated after kan.sync_category
-- runs.  Defining a function is safe at DDL time; calling it is deferred to
-- after sync (apply_unified handles the ordering automatically).
--
-- Idempotent: ON CONFLICT DO NOTHING throughout.  Re-running after
-- port_curry_sqlite_to_pg.py adds new rows will insert only the delta.

CREATE OR REPLACE FUNCTION kan.populate_curry_calx_functor()
RETURNS TABLE (objects_mapped INT, morphisms_mapped INT)
LANGUAGE plpgsql AS $$
DECLARE
    n_obj INT;
    n_mor INT;
BEGIN
    -- Declare the functor (requires kan.category rows for 'curry' and 'calx',
    -- which sync_category must have already created).
    INSERT INTO kan.functor (name, src_category, tgt_category, description)
    VALUES (
        'curry_to_calx',
        'curry',
        'calx',
        'Maps each Curry-wrapped calx function to its backing SQL routine. '
        'Object map: curry.functions.name (calx_* prefix) → calx routine name '
        '(strip calx_ prefix, existence verified against pg_proc). '
        'Morphism map: curry.function_dependencies edges between calx-prefixed '
        'functions → identity path ({}) because calx call-graph edges are not '
        'expressed as FK morphisms.'
    )
    ON CONFLICT (name) DO NOTHING;

    -- Object map ---------------------------------------------------------
    -- curry.functions.name  →  calx routine name
    -- Only rows whose stripped name actually resolves in pg_proc are included,
    -- so the map stays honest even when Curry wrappers outpace the SQL schema.
    INSERT INTO kan.functor_object_map (functor, src_object, tgt_object)
    SELECT
        'curry_to_calx',
        f.name,
        substring(f.name FROM 6)    -- strip 'calx_' (positions 1–5)
    FROM curry.functions f
    WHERE f.name LIKE 'calx_%'
      AND EXISTS (
            SELECT 1
              FROM pg_proc p
              JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = 'calx'
               AND p.proname = substring(f.name FROM 6)
          )
    ON CONFLICT (functor, src_object) DO NOTHING;
    GET DIAGNOSTICS n_obj = ROW_COUNT;

    -- Morphism map --------------------------------------------------------
    -- curry.function_dependencies edges between two calx-prefixed functions
    -- Synthesised morphism name pattern:
    --   <caller>_v<version>_uses_<callee>
    -- tgt_path = {} means "identity on the target object" — i.e., the
    -- dependency is acknowledged but has no FK-level realisation in calx.
    INSERT INTO kan.functor_morphism_path (functor, src_morphism, tgt_path)
    SELECT
        'curry_to_calx',
        fd.function_name
            || '_v' || fd.function_version
            || '_uses_' || fd.depends_on_function_name,
        '{}'::TEXT[]
    FROM curry.function_dependencies fd
    WHERE fd.depends_on_function_name IS NOT NULL
      AND fd.function_name          LIKE 'calx_%'
      AND fd.depends_on_function_name LIKE 'calx_%'
    ON CONFLICT (functor, src_morphism) DO NOTHING;
    GET DIAGNOSTICS n_mor = ROW_COUNT;

    RETURN QUERY SELECT n_obj, n_mor;
END
$$;
