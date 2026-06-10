-- Seed curry.functions with calx_* wrappers and populate the curry→calx functor.
--
-- The populate_curry_calx_functor() function (21_kan_functors.sql) needs:
--   1. kan.category rows for 'curry' and 'calx'
--   2. curry.functions rows whose names begin 'calx_'
--
-- This file provides both, then calls the population function.
-- Idempotent: ON CONFLICT DO NOTHING throughout.

-- 1. Register categories
INSERT INTO kan.category (name, db_schema, description) VALUES
    ('curry', 'curry', 'Curry schema: typed wrappers around SQL inference functions'),
    ('calx',  'calx',  'Calx schema: number-theoretic SQL routines on integers 1..N')
ON CONFLICT (name) DO NOTHING;

-- 2. Seed curry.functions with calx_* wrappers for every public calx pg_proc routine
--    (excludes private _* functions).
INSERT INTO curry.functions (name, version, body, is_pure, description)
SELECT
    'calx_' || p.proname,
    1,
    'calx.' || p.proname,
    true,
    'Curry wrapper for calx.' || p.proname
        || ' (SQL routine, ' || p.pronargs || ' arg(s))'
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'calx'
  AND p.proname NOT LIKE '\_%'
ORDER BY p.proname
ON CONFLICT (name, version) DO NOTHING;

-- 3. Populate the functor row and object map
SELECT objects_mapped, morphisms_mapped
  FROM kan.populate_curry_calx_functor();
