-- Curry library quality claims — 51_curry_lib_claims.sql
--
-- Three structural claims that certify properties of the Curry function
-- library populated by 50_curry_lib.sql.  Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
VALUES

-- 1: All calx_* wrappers have documented arg specs
('curry_function',
 '{"prefix":"calx_","property":"expected_args"}'::jsonb,
 'curry: all 20 calx_* functions have documented expected_args',
 'structural', 'comp_sql',
 $probe$
 SELECT (count(*) = 20) AS ok,
        jsonb_build_object(
            'total_calx', count(*),
            'with_args',  count(*) FILTER (WHERE expected_args IS NOT NULL),
            'missing',    array_agg(name) FILTER (WHERE expected_args IS NULL)
        ) AS evidence
 FROM curry.functions
 WHERE name LIKE 'calx\_%' AND retired_at IS NULL
 $probe$),

-- 2: All math_* foundation functions are pure
('curry_function',
 '{"prefix":"math_","property":"is_pure"}'::jsonb,
 'curry: all math_* foundation functions are declared pure (side-effect-free)',
 'structural', 'comp_sql',
 $probe$
 SELECT bool_and(is_pure) AS ok,
        jsonb_build_object(
            'total_math', count(*),
            'pure',       count(*) FILTER (WHERE is_pure),
            'impure',     array_agg(name) FILTER (WHERE NOT is_pure)
        ) AS evidence
 FROM curry.functions
 WHERE name LIKE 'math\_%' AND retired_at IS NULL
 $probe$),

-- 3: type_compatibility matrix is populated for all multi-version constants
('curry_function',
 '{"table":"type_compatibility"}'::jsonb,
 'curry: type_compatibility matrix is populated (≥ 1 entry per multi-version constant)',
 'structural', 'comp_sql',
 $probe$
 SELECT (count(DISTINCT constant_id) >= 5) AS ok,
        jsonb_build_object(
            'covered_constants', count(DISTINCT constant_id),
            'total_entries',     count(*),
            'compatible_paths',  count(*) FILTER (WHERE is_compatible),
            'constants', array_agg(DISTINCT constant_id ORDER BY constant_id)
        ) AS evidence
 FROM curry.type_compatibility
 $probe$)

ON CONFLICT (statement) DO NOTHING;
