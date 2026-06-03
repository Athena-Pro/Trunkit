-- Curry trusted function library — 50_curry_lib.sql
--
-- Loads three things into the Curry layer:
--
--  1. Enriched calx_* wrappers  — expected_args + arg_descriptions for all
--     20 calx SQL routine wrappers so callers know how to invoke them.
--
--  2. Foundation math functions — a set of pure, cross-domain primitives
--     (math_gcd, math_lcm, math_is_prime, math_divisors, math_prime_factors,
--     math_totient, cert_claim_status) not previously in Curry, documented
--     and registered with dependency edges.
--
--  3. Type compatibility matrix — version-upgrade paths for the 5 HIR
--     constants that have two live versions (Bond, FoldType, HodgeAtom,
--     SNFForm, snf_equivalent_via_snf).
--
-- Idempotent: ON CONFLICT DO NOTHING / DO UPDATE throughout.
-- -------------------------------------------------------------------------

-- =========================================================================
-- 1. ENRICH CALX_* WRAPPERS WITH ARG SPECS
-- =========================================================================

UPDATE curry.functions SET
    expected_args    = '{"n": {"type":"bigint","description":"positive integer"}}'::jsonb,
    arg_descriptions = '{"n":"integer n to compute the aliquot step for; result is sigma(n)-n"}'::jsonb
WHERE name = 'calx_aliquot_step'           AND version = 1;

UPDATE curry.functions SET
    expected_args    = '{"n": {"type":"bigint","description":"positive integer ≥ 1"}}'::jsonb,
    arg_descriptions = '{"n":"integer n; arithmetic derivative D(n) = n·Σ e_i/p_i over prime factorization"}'::jsonb
WHERE name = 'calx_arithmetic_derivative'  AND version = 1;

UPDATE curry.functions SET
    expected_args    = '{"n": {"type":"bigint","description":"positive integer"}}'::jsonb,
    arg_descriptions = '{"n":"integer n; returns full arithmetic profile (omega, sigma, tau, derivative, …)"}'::jsonb
WHERE name = 'calx_arithmetic_facts'       AND version = 1;

UPDATE curry.functions SET
    expected_args    = '{"n": {"type":"bigint","description":"first integer"},
                         "m": {"type":"bigint","description":"second integer"}}'::jsonb,
    arg_descriptions = '{"n":"first operand","m":"second operand; classifies arithmetic relation between n and m"}'::jsonb
WHERE name = 'calx_characterize_relation'  AND version = 1;

UPDATE curry.functions SET
    expected_args    = '{"remainders": {"type":"bigint[]","description":"residue array"},
                         "moduli":     {"type":"bigint[]","description":"pairwise-coprime modulus array"}}'::jsonb,
    arg_descriptions = '{"remainders":"r_i values","moduli":"m_i values; returns x ≡ r_i (mod m_i) by CRT"}'::jsonb
WHERE name = 'calx_crt'                    AND version = 1;

UPDATE curry.functions SET
    expected_args    = '{"n":     {"type":"bigint","description":"integer in the CRT class"},
                         "depth": {"type":"integer","description":"neighbourhood radius"}}'::jsonb,
    arg_descriptions = '{"n":"centre of the class","depth":"how many neighbours to return (distance ≤ depth)"}'::jsonb
WHERE name = 'calx_crt_class_neighbors'    AND version = 1;

UPDATE curry.functions SET
    expected_args    = '{"a": {"type":"bigint","description":"residue of first class"},
                         "m": {"type":"bigint","description":"modulus of first class"},
                         "b": {"type":"bigint","description":"residue of second class"},
                         "n": {"type":"bigint","description":"modulus of second class"}}'::jsonb,
    arg_descriptions = '{"a":"r_1","m":"m_1","b":"r_2","n":"m_2; returns x≡a(mod m)∩x≡b(mod n) if gcd(m,n)|a-b"}'::jsonb
WHERE name = 'calx_crt_combine'            AND version = 1;

UPDATE curry.functions SET
    expected_args    = '{"target": {"type":"bigint","description":"integer to decompose into prime-power residues"}}'::jsonb,
    arg_descriptions = '{"target":"n; rows are (prime p, prime_power p^e, residue n mod p^e)"}'::jsonb
WHERE name = 'calx_crt_decompose'          AND version = 1;

UPDATE curry.functions SET
    expected_args    = '{"n":     {"type":"bigint","description":"starting integer"},
                         "depth": {"type":"integer","description":"lifting depth"}}'::jsonb,
    arg_descriptions = '{"n":"start","depth":"levels to lift through Hensel-style CRT tower"}'::jsonb
WHERE name = 'calx_crt_lift_step'          AND version = 1;

UPDATE curry.functions SET
    expected_args    = '{"residues":     {"type":"bigint[]","description":"r_i residue array"},
                         "prime_powers":  {"type":"bigint[]","description":"p_i^e_i modulus array"}}'::jsonb,
    arg_descriptions = '{"residues":"r_i from decompose","prime_powers":"matching prime powers; reconstructs n"}'::jsonb
WHERE name = 'calx_crt_reconstruct'        AND version = 1;

UPDATE curry.functions SET
    expected_args    = '{"a": {"type":"bigint","description":"first integer"},
                         "b": {"type":"bigint","description":"second integer"}}'::jsonb,
    arg_descriptions = '{"a":"a","b":"b; returns (g=gcd, s, t) where a·s + b·t = g (Bézout coefficients)"}'::jsonb
WHERE name = 'calx_ext_gcd'               AND version = 1;

UPDATE curry.functions SET
    is_pure          = false,
    expected_args    = '{"lim": {"type":"bigint","description":"upper bound; populates calx.factorizations for n ≤ lim"}}'::jsonb,
    arg_descriptions = '{"lim":"limit; side-effect: writes factorization rows — no return value"}'::jsonb
WHERE name = 'calx_generate_factorizations_only' AND version = 1;

UPDATE curry.functions SET
    is_pure          = false,
    expected_args    = '{"lim": {"type":"bigint","description":"upper bound; populates all calx integer tables for n ≤ lim"}}'::jsonb,
    arg_descriptions = '{"lim":"limit; side-effect: writes integers, factorizations, primes, sequences — no return value"}'::jsonb
WHERE name = 'calx_generate_integer_database' AND version = 1;

UPDATE curry.functions SET
    expected_args    = '{"a": {"type":"bigint","description":"integer whose inverse is sought"},
                         "m": {"type":"bigint","description":"modulus (must be coprime to a)"}}'::jsonb,
    arg_descriptions = '{"a":"a","m":"m; returns a⁻¹ mod m (NULL if gcd(a,m)≠1)"}'::jsonb
WHERE name = 'calx_mod_inverse'            AND version = 1;

UPDATE curry.functions SET
    expected_args    = '{"r1": {"type":"bigint","description":"residue of first progression"},
                         "m1": {"type":"bigint","description":"modulus of first progression"},
                         "r2": {"type":"bigint","description":"residue of second progression"},
                         "m2": {"type":"bigint","description":"modulus of second progression"}}'::jsonb,
    arg_descriptions = '{"r1,m1":"first class r1 mod m1","r2,m2":"second class; returns CRT intersection info"}'::jsonb
WHERE name = 'calx_progression_intersect'  AND version = 1;

UPDATE curry.functions SET
    expected_args    = '{"n": {"type":"bigint","description":"positive integer"}}'::jsonb,
    arg_descriptions = '{"n":"n; returns rad(n) = product of distinct prime factors"}'::jsonb
WHERE name = 'calx_radical_step'           AND version = 1;

UPDATE curry.functions SET
    expected_args    = '{"n": {"type":"bigint","description":"first integer"},
                         "m": {"type":"bigint","description":"second integer"}}'::jsonb,
    arg_descriptions = '{"n":"n","m":"m; lists OEIS sequences that contain both n and m"}'::jsonb
WHERE name = 'calx_shared_sequences'       AND version = 1;

UPDATE curry.functions SET
    expected_args    = '{"n": {"type":"bigint","description":"positive integer"}}'::jsonb,
    arg_descriptions = '{"n":"n; returns sorted exponents of prime factorization as bigint (prime signature)"}'::jsonb
WHERE name = 'calx_signature_step'         AND version = 1;

UPDATE curry.functions SET
    is_pure          = false,
    expected_args    = '{"start_n":  {"type":"bigint","description":"orbit seed"},
                         "rel_type": {"type":"text","description":"ALIQUOT | COLLATZ | RADICAL"},
                         "max_steps":{"type":"integer","description":"step cap (default 200)"},
                         "lim":      {"type":"bigint","description":"value ceiling (default 10 000 000)"}}'::jsonb,
    arg_descriptions = '{"start_n":"seed","rel_type":"orbit kind","max_steps":"cap","lim":"ceiling; records orbit into calx.orbits"}'::jsonb
WHERE name = 'calx_trace_orbit'            AND version = 1;

UPDATE curry.functions SET
    expected_args    = '{"k": {"type":"integer","description":"wheel order (k=1→mod 6, k=2→mod 30, …)"}}'::jsonb,
    arg_descriptions = '{"k":"wheel order; returns coprime residues (spokes) of the k-th primorial wheel"}'::jsonb
WHERE name = 'calx_wheel_spokes'           AND version = 1;


-- =========================================================================
-- 2. FOUNDATION MATH FUNCTIONS (new pure cross-domain primitives)
-- =========================================================================

-- math_gcd: greatest common divisor via calx.ext_gcd
INSERT INTO curry.functions
    (name, version, body, is_pure, description, expected_args, arg_descriptions, function_bindings)
VALUES (
    'math_gcd', 1,
    'SELECT g FROM calx.ext_gcd($1, $2)',
    true,
    'Greatest common divisor gcd(a,b) via the extended Euclidean algorithm. '
    'Pure; delegates to calx.ext_gcd. Commutative, associative; gcd(a,0)=a.',
    '{"a":{"type":"bigint","description":"first integer"},
      "b":{"type":"bigint","description":"second integer"}}'::jsonb,
    '{"a":"a","b":"b; returns gcd(a,b) — always ≥ 1 for positive inputs"}'::jsonb,
    '{"calx_ext_gcd": "calx_ext_gcd@v1"}'::jsonb
)
ON CONFLICT (name, version) DO NOTHING;

-- math_lcm: least common multiple (a*b / gcd)
INSERT INTO curry.functions
    (name, version, body, is_pure, description, expected_args, arg_descriptions, function_bindings)
VALUES (
    'math_lcm', 1,
    'SELECT (a / (SELECT g FROM calx.ext_gcd(a, b))) * b FROM (VALUES ($1::bigint,$2::bigint)) t(a,b)',
    true,
    'Least common multiple lcm(a,b) = a/gcd(a,b)·b. Pure; uses calx.ext_gcd. '
    'lcm(a,0) = 0 by convention.',
    '{"a":{"type":"bigint","description":"first integer"},
      "b":{"type":"bigint","description":"second integer"}}'::jsonb,
    '{"a":"a","b":"b; returns lcm(a,b)"}'::jsonb,
    '{"math_gcd": "math_gcd@v1"}'::jsonb
)
ON CONFLICT (name, version) DO NOTHING;

-- math_is_prime: primality from calx.integers
INSERT INTO curry.functions
    (name, version, body, is_pure, description, expected_args, arg_descriptions, function_bindings)
VALUES (
    'math_is_prime', 1,
    'SELECT is_prime FROM calx.integers WHERE n = $1',
    true,
    'Returns TRUE iff n is prime, by lookup in calx.integers. Pure read-only. '
    'Returns NULL if n has not been generated (n > current calx limit).',
    '{"n":{"type":"bigint","description":"positive integer to test"}}'::jsonb,
    '{"n":"n; returns boolean or NULL if n is beyond the generated range"}'::jsonb,
    '{"calx_arithmetic_facts": "calx_arithmetic_facts@v1"}'::jsonb
)
ON CONFLICT (name, version) DO NOTHING;

-- math_divisors: list all divisors of n from factorizations
INSERT INTO curry.functions
    (name, version, body, is_pure, description, expected_args, arg_descriptions, function_bindings)
VALUES (
    'math_divisors', 1,
    $body$
SELECT d::bigint
FROM generate_series(1, $1) d
WHERE (SELECT count(*) = 0
       FROM calx.factorizations f
       WHERE f.n = $1 AND ($1 / d) * d != $1
         AND NOT EXISTS (
               SELECT 1 FROM calx.factorizations f2
               WHERE f2.n = d AND f2.prime = f.prime
                 AND f2.exponent >= f.exponent))
  AND $1 % d = 0
ORDER BY d
    $body$,
    true,
    'List all positive divisors of n in ascending order. Pure; uses calx.factorizations. '
    'For large n consider calx_arithmetic_facts which includes tau (divisor count).',
    '{"n":{"type":"bigint","description":"positive integer whose divisors are sought"}}'::jsonb,
    '{"n":"n; returns sorted list of divisors d where 1 ≤ d ≤ n and d|n"}'::jsonb,
    '{"calx_arithmetic_facts": "calx_arithmetic_facts@v1"}'::jsonb
)
ON CONFLICT (name, version) DO NOTHING;

-- math_prime_factors: distinct prime factors
INSERT INTO curry.functions
    (name, version, body, is_pure, description, expected_args, arg_descriptions, function_bindings)
VALUES (
    'math_prime_factors', 1,
    'SELECT prime FROM calx.factorizations WHERE n = $1 ORDER BY prime',
    true,
    'Returns the distinct prime factors of n in ascending order. Pure; reads calx.factorizations. '
    'Returns empty set for n=1 (no prime factors). NULL for n not yet generated.',
    '{"n":{"type":"bigint","description":"positive integer to factor"}}'::jsonb,
    '{"n":"n; returns sorted distinct primes p with p|n"}'::jsonb,
    '{"calx_arithmetic_facts": "calx_arithmetic_facts@v1"}'::jsonb
)
ON CONFLICT (name, version) DO NOTHING;

-- math_totient: Euler totient φ(n) = n·Π(1-1/p) over prime factors
INSERT INTO curry.functions
    (name, version, body, is_pure, description, expected_args, arg_descriptions, function_bindings)
VALUES (
    'math_totient', 1,
    $body$
SELECT ($1 * round(exp(sum(ln(1.0 - 1.0/prime::numeric)))))::bigint
FROM calx.factorizations WHERE n = $1
    $body$,
    true,
    'Euler totient φ(n): count of integers in [1,n] coprime to n. '
    'Computed as n·Π_{p|n}(1-1/p) over distinct prime factors from calx.factorizations. '
    'φ(1)=1; φ(prime p)=p-1. Pure.',
    '{"n":{"type":"bigint","description":"positive integer ≥ 1"}}'::jsonb,
    '{"n":"n; returns φ(n) — count of k in [1,n] with gcd(k,n)=1"}'::jsonb,
    '{"math_prime_factors": "math_prime_factors@v1"}'::jsonb
)
ON CONFLICT (name, version) DO NOTHING;

-- cert_claim_status: impure meta-function — query the cert ledger
INSERT INTO curry.functions
    (name, version, body, is_pure, description, expected_args, arg_descriptions)
VALUES (
    'cert_claim_status', 1,
    'SELECT status, checked_at FROM cert.standing WHERE claim_id = $1',
    false,
    'Returns the current attestation status and check timestamp for a cert claim. '
    'Impure (reads live ledger state). Useful for cross-model provenance checks.',
    '{"claim_id":{"type":"bigint","description":"claim id from cert.claim"}}'::jsonb,
    '{"claim_id":"id; returns (status text, checked_at timestamptz) or empty if claim not found"}'::jsonb
)
ON CONFLICT (name, version) DO NOTHING;


-- =========================================================================
-- 3. FUNCTION DEPENDENCY EDGES (calx_* inter-dependencies + foundations)
-- =========================================================================

INSERT INTO curry.function_dependencies (function_name, function_version, depends_on_function_name)
VALUES
    -- math_lcm depends on math_gcd
    ('math_lcm',            1, 'math_gcd'),
    -- math_totient depends on math_prime_factors
    ('math_totient',        1, 'math_prime_factors'),
    -- calx_crt_reconstruct is the inverse of calx_crt_decompose
    ('calx_crt_reconstruct',1, 'calx_crt_decompose'),
    -- calx_crt_combine depends on calx_ext_gcd (CRT uses Bézout)
    ('calx_crt_combine',    1, 'calx_ext_gcd'),
    -- calx_mod_inverse uses ext_gcd internally
    ('calx_mod_inverse',    1, 'calx_ext_gcd'),
    -- calx_radical_step reads factorizations (same as arithmetic_facts)
    ('calx_radical_step',   1, 'calx_arithmetic_facts'),
    -- calx_signature_step reads factorizations
    ('calx_signature_step', 1, 'calx_arithmetic_facts')
ON CONFLICT DO NOTHING;


-- =========================================================================
-- 4. TYPE COMPATIBILITY MATRIX (multi-version HIR constants)
-- =========================================================================
-- For each constant with two versions, record v1→v2 compatibility.
-- is_compatible=true means code written against v1 can consume v2 values.

INSERT INTO curry.type_compatibility
    (constant_id, from_version, to_version, is_compatible, conversion_function)
VALUES
    -- Bond_definition: v2 adds pub qualifier — backward compatible read
    ('Bond_definition',              1, 2, true,  'identity'),
    ('Bond_definition',              2, 1, false, NULL),   -- v2 caller needs pub fields
    -- FoldType_definition: v2 adds Chroma(usize) — structural extension
    ('FoldType_definition',          1, 2, false, NULL),   -- Chroma variant added
    ('FoldType_definition',          2, 1, false, NULL),
    -- HodgeAtom_definition: v2 adds pub qualifier — compatible
    ('HodgeAtom_definition',         1, 2, true,  'identity'),
    ('HodgeAtom_definition',         2, 1, false, NULL),
    -- SNFForm_definition: v2 adds pub — compatible
    ('SNFForm_definition',           1, 2, true,  'identity'),
    ('SNFForm_definition',           2, 1, false, NULL),
    -- snf_equivalent_via_snf: same logic, v2 is drop-in replacement
    ('snf_equivalent_via_snf_signature', 1, 2, true,  'identity'),
    ('snf_equivalent_via_snf_signature', 2, 1, true,  'identity')
ON CONFLICT (constant_id, from_version, to_version) DO NOTHING;


-- =========================================================================
-- 5. FIX CLAIM 4 — probe the live KAN engine coverage, don't use a constant
-- =========================================================================
-- Claim 4 was unverified because curry.constants has no 'kan_self_report'.
-- Better: probe cert.kan_engines_all_true() directly and check all_true.

UPDATE cert.claim
SET
    subject_kind = 'curry_function',
    statement    = 'every kan engine law-view is all-true in the live DB: cert.kan_engines_all_true()',
    probe_sql    = $probe$
        SELECT ok, evidence
        FROM cert.kan_engines_all_true()
    $probe$
WHERE id = 4;
