-- =============================================================================
--  calx — Dynamical Integer Relation System
--
--  Depends on: 01_schema.sql (integers, primes, factorizations)
--              02_views.sql  (divisor_sum)
--              04_crt.sql    (ext_gcd)
--
--  This layer treats the integer database as a dynamical system:
--    States      = integers with their factorization profiles
--    Transitions = typed, parameterized mathematical relations
--    Orbits      = trajectories under iterated relation application
--
--  Contents:
--    1. Sequence catalog and membership schema
--    2. Relation graph schema
--    3. Orbit trace schema
--    4. Relation characterizer (full relation vector between two integers)
--    5. CRT class membership (p-adic neighborhood)
--    6. Dynamical relation functions
--    7. Orbit tracer procedure
--    8. Sequence co-membership analyzer
-- =============================================================================


-- ─────────────────────────────────────────────────────────────────────────────
-- 1. SEQUENCE CATALOG AND MEMBERSHIP
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sequences (
    seq_id    TEXT    PRIMARY KEY,         -- 'A000040', 'primes_3mod4', etc.
    name      TEXT    NOT NULL,
    seq_type  TEXT    NOT NULL,            -- 'arithmetic', 'multiplicative',
                                           -- 'recursive', 'congruence', 'derived'
    formula   TEXT,                        -- human-readable definition
    modulus   BIGINT,                      -- if congruence-type: the modulus
    residue   BIGINT,                      -- if congruence-type: the residue class
    base_seq  TEXT REFERENCES sequences(seq_id),
    family    TEXT                         -- algebraic family label (primality,
                                           -- aliquot_class, almost_prime, smooth,
                                           -- congruence, recursive, figurate,
                                           -- orbit, signature_class, highly_composite)
);

-- Idempotent add for existing deployments that pre-date the family column.
ALTER TABLE sequences ADD COLUMN IF NOT EXISTS family TEXT;

COMMENT ON TABLE sequences IS
    'Catalog of named integer sequences. Congruence-type sequences store their
     CRT parameters directly, enabling algebraic characterization of co-membership.
     family groups sequences by algebraic structure so characterize_relation
     can annotate shared-sequence edges with category labels.';


CREATE TABLE IF NOT EXISTS sequence_membership (
    seq_id  TEXT    NOT NULL REFERENCES sequences(seq_id),
    n       BIGINT  NOT NULL REFERENCES integers(n),
    idx     BIGINT  NOT NULL,              -- 1-indexed position in the sequence
    PRIMARY KEY (seq_id, n)
);

CREATE INDEX IF NOT EXISTS idx_membership_n
    ON sequence_membership(n, seq_id);

CREATE INDEX IF NOT EXISTS idx_membership_seq
    ON sequence_membership(seq_id, idx);


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. RELATION GRAPH
--    Typed, parameterized edges between integers. rel_type is first-class;
--    rel_params carries instance-specific data.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS integer_relations (
    n           BIGINT  NOT NULL REFERENCES integers(n),
    m           BIGINT  NOT NULL REFERENCES integers(n),
    rel_type    TEXT    NOT NULL,
    -- Relation types:
    --   DIVISOR          m | n  (m divides n)
    --   MULTIPLE         n | m
    --   ALIQUOT          m = σ(n) - n
    --   ALIQUOT_INV      n = σ(m) - m
    --   CRT_CLASS        same residue class at a given prime depth
    --   SIGNATURE_TWIN   identical prime exponent multiset
    --   SHARED_SEQUENCE  co-appear in a named sequence
    --   PRIME_COUSIN     |n - m| = prime gap at their positions
    --   ARITH_DERIV      m = D(n) (arithmetic derivative)
    --   SMOOTH_NEIGHBOR  nearest k-smooth number
    rel_params  JSONB,
    PRIMARY KEY (n, m, rel_type)
);

CREATE INDEX IF NOT EXISTS idx_rel_m       ON integer_relations(m, rel_type);
CREATE INDEX IF NOT EXISTS idx_rel_type    ON integer_relations(rel_type, n, m);
CREATE INDEX IF NOT EXISTS idx_rel_params  ON integer_relations USING gin(rel_params);

COMMENT ON TABLE integer_relations IS
    'Typed relation graph over integers. Edges carry the algebraic mechanism
     (rel_params) explaining why n and m are related, not just that they are.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. ORBIT TRACES
--    A cycle is detected when n recurs within the same orbit_id.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS orbits (
    orbit_id    BIGINT  NOT NULL,
    step        INTEGER NOT NULL,
    n           BIGINT  NOT NULL REFERENCES integers(n),
    rel_type    TEXT    NOT NULL,
    rel_params  JSONB,
    cycle_close BOOLEAN DEFAULT FALSE,     -- TRUE iff this step closes a cycle
    PRIMARY KEY (orbit_id, step)
);

CREATE INDEX IF NOT EXISTS idx_orbit_n ON orbits(n, orbit_id);

CREATE SEQUENCE IF NOT EXISTS orbit_id_seq;

COMMENT ON TABLE orbits IS
    'Orbit traces for dynamical systems over integers.
     Each row is one step in a trajectory: (orbit_id, step) → n.
     cycle_close=TRUE marks where an orbit returns to a previously visited state.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 4. RELATION CHARACTERIZER
--    Full relation vector between two integers. Every algebraic and
--    sequence-theoretic relationship, with CRT structure.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION characterize_relation(n_val BIGINT, m_val BIGINT)
RETURNS TABLE (
    rel_type    TEXT,
    rel_params  JSONB,
    description TEXT
)
LANGUAGE plpgsql STABLE
AS $$
DECLARE
    g          BIGINT;
    lcm_val    BIGINT;
    sig_n      INTEGER[];
    sig_m      INTEGER[];
    depth      INTEGER;
    prim_val   BIGINT;
    omega_n    INTEGER;
    omega_m    INTEGER;
    bigomega_n INTEGER;
    bigomega_m INTEGER;
    sqf_n      BOOLEAN;
    sqf_m      BOOLEAN;
    sigma_n    BIGINT;
    sigma_m    BIGINT;
BEGIN
    -- ── Divisibility relations ─────────────────────────────────────────────
    IF n_val % m_val = 0 THEN
        rel_type    := 'DIVISOR';
        rel_params  := jsonb_build_object('quotient', n_val / m_val);
        description := m_val || ' divides ' || n_val ||
                       ' (quotient ' || (n_val / m_val) || ')';
        RETURN NEXT;
    END IF;

    IF m_val % n_val = 0 THEN
        rel_type    := 'MULTIPLE';
        rel_params  := jsonb_build_object('quotient', m_val / n_val);
        description := n_val || ' divides ' || m_val ||
                       ' (quotient ' || (m_val / n_val) || ')';
        RETURN NEXT;
    END IF;

    -- ── GCD / coprimality ──────────────────────────────────────────────────
    SELECT eg.g INTO g FROM ext_gcd(n_val, m_val) eg;
    IF g = 1 THEN
        rel_type    := 'COPRIME';
        rel_params  := jsonb_build_object('gcd', 1);
        description := 'gcd(' || n_val || ', ' || m_val || ') = 1';
        RETURN NEXT;
    ELSE
        lcm_val := (n_val / g) * m_val;
        rel_type    := 'COMMON_FACTOR';
        rel_params  := jsonb_build_object('gcd', g, 'lcm', lcm_val);
        description := 'gcd = ' || g || ', lcm = ' || lcm_val;
        RETURN NEXT;
    END IF;

    -- ── Prime signature (exponent multiset) ───────────────────────────────
    SELECT array_agg(exponent ORDER BY exponent DESC) INTO sig_n
    FROM factorizations WHERE n = n_val;

    SELECT array_agg(exponent ORDER BY exponent DESC) INTO sig_m
    FROM factorizations WHERE n = m_val;

    IF sig_n IS NOT NULL AND sig_n = sig_m THEN
        rel_type    := 'SIGNATURE_TWIN';
        rel_params  := jsonb_build_object('shape', sig_n);
        description := 'Identical prime exponent multiset: ' || sig_n::TEXT;
        RETURN NEXT;
    END IF;

    -- ── ω and Ω equality ───────────────────────────────────────────────────
    SELECT i.omega, i.big_omega, i.is_squarefree
      INTO omega_n, bigomega_n, sqf_n
      FROM integers i WHERE i.n = n_val;
    SELECT i.omega, i.big_omega, i.is_squarefree
      INTO omega_m, bigomega_m, sqf_m
      FROM integers i WHERE i.n = m_val;

    IF omega_n IS NOT NULL AND omega_n = omega_m THEN
        rel_type    := 'OMEGA_EQUAL';
        rel_params  := jsonb_build_object('omega', omega_n);
        description := 'Same ω(n): ' || omega_n || ' distinct prime factors each';
        RETURN NEXT;
    END IF;

    IF bigomega_n IS NOT NULL AND bigomega_n = bigomega_m THEN
        rel_type    := 'BIG_OMEGA_EQUAL';
        rel_params  := jsonb_build_object('big_omega', bigomega_n);
        description := 'Same Ω(n): ' || bigomega_n || ' total prime factors each';
        RETURN NEXT;
    END IF;

    IF sqf_n AND sqf_m THEN
        rel_type    := 'BOTH_SQUAREFREE';
        rel_params  := '{}'::jsonb;
        description := 'Both squarefree';
        RETURN NEXT;
    END IF;

    -- ── CRT class agreement: at which prime depths do they agree? ─────────
    -- Two integers agree at depth k iff n ≡ m (mod primorial(k)). As depth
    -- grows the class shrinks — this is the p-adic ball around n.
    FOR depth IN 1 .. 6 LOOP
        SELECT EXP(SUM(LN(p)))::BIGINT INTO prim_val
        FROM (SELECT p FROM primes ORDER BY discovered_order LIMIT depth) sub;

        EXIT WHEN prim_val IS NULL OR prim_val > GREATEST(n_val, m_val);

        IF n_val % prim_val = m_val % prim_val THEN
            rel_type   := 'CRT_CLASS';
            rel_params := jsonb_build_object(
                'depth',   depth,
                'modulus', prim_val,
                'residue', n_val % prim_val
            );
            description := 'Same residue class mod ' || prim_val ||
                           ' (first ' || depth || ' primes): both ≡ ' ||
                           (n_val % prim_val) || ' (mod ' || prim_val || ')';
            RETURN NEXT;
        END IF;
    END LOOP;

    -- ── Shared sequence membership (annotated with family if known) ───────
    RETURN QUERY
    SELECT
        'SHARED_SEQUENCE'::TEXT,
        jsonb_build_object(
            'seq_id', sm1.seq_id,
            'family', s.family,
            'idx_n',  sm1.idx,
            'idx_m',  sm2.idx,
            'gap',    ABS(sm1.idx - sm2.idx)
        ),
        'Both in ' || sm1.seq_id ||
        COALESCE(' [' || s.family || ']', '') ||
        ' at positions ' || sm1.idx || ' and ' || sm2.idx
    FROM sequence_membership sm1
    JOIN sequence_membership sm2
      ON sm1.seq_id = sm2.seq_id
     AND sm2.n      = m_val
    JOIN sequences s ON s.seq_id = sm1.seq_id
    WHERE sm1.n = n_val;

    -- ── Aliquot relations ──────────────────────────────────────────────────
    SELECT ds.sigma INTO sigma_n FROM divisor_sum ds WHERE ds.n = n_val;
    SELECT ds.sigma INTO sigma_m FROM divisor_sum ds WHERE ds.n = m_val;

    IF sigma_n IS NOT NULL AND sigma_n - n_val = m_val THEN
        rel_type    := 'ALIQUOT';
        rel_params  := jsonb_build_object('sigma_n', sigma_n);
        description := m_val || ' = s(' || n_val || '): aliquot successor';
        RETURN NEXT;
    END IF;

    IF sigma_m IS NOT NULL AND sigma_m - m_val = n_val THEN
        rel_type    := 'ALIQUOT_INV';
        rel_params  := jsonb_build_object('sigma_m', sigma_m);
        description := n_val || ' = s(' || m_val || '): aliquot predecessor';
        RETURN NEXT;
    END IF;
END $$;

COMMENT ON FUNCTION characterize_relation IS
    'Full relation vector between two integers. Returns every algebraic,
     factorization-theoretic, CRT-class, and sequence relation connecting them,
     with the mechanism (rel_params) explaining each.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 5. CRT CLASS MEMBERSHIP — p-adic neighborhood
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION crt_class_neighbors(n_val BIGINT, depth INTEGER)
RETURNS TABLE (m BIGINT, distance BIGINT)
LANGUAGE sql STABLE
AS $$
    WITH primorial AS (
        SELECT EXP(SUM(LN(p)))::BIGINT AS prim
        FROM (SELECT p FROM primes ORDER BY discovered_order LIMIT depth) sub
    )
    SELECT
        i.n,
        ABS(i.n - n_val) AS distance
    FROM integers i, primorial
    WHERE i.n % primorial.prim = n_val % primorial.prim
      AND i.n != n_val
    ORDER BY distance;
$$;

COMMENT ON FUNCTION crt_class_neighbors IS
    'All integers in the same CRT class as n at prime depth k.
     Depth k means agreement mod primorial(k) = 2·3·5·...·pₖ.
     As depth increases the neighborhood shrinks — this tower is the p-adic
     topology on ℤ.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 6. DYNAMICAL RELATION FUNCTIONS
--    Transition functions T: ℤ → ℤ whose iteration defines orbits.
-- ─────────────────────────────────────────────────────────────────────────────

-- T(n) = σ(n) - n = sum of proper divisors
CREATE OR REPLACE FUNCTION aliquot_step(n_val BIGINT)
RETURNS BIGINT
LANGUAGE sql STABLE
AS $$
    SELECT sigma - n_val FROM divisor_sum WHERE n = n_val;
$$;


-- D(1) = 0, D(p) = 1, D(p^k) = k·p^(k-1), D(mn) = D(m)n + mD(n)
-- Closed form from factorizations: D(n) = n · Σ νₚ(n)/p
CREATE OR REPLACE FUNCTION arithmetic_derivative(n_val BIGINT)
RETURNS BIGINT
LANGUAGE sql STABLE
AS $$
    SELECT CASE
        WHEN n_val <= 1 THEN 0
        ELSE COALESCE(
            ROUND((n_val::NUMERIC) * SUM(exponent::NUMERIC / prime))::BIGINT,
            0
        )
    END
    FROM factorizations
    WHERE n = n_val;
$$;

COMMENT ON FUNCTION arithmetic_derivative IS
    'D(n) = n · Σ νₚ(n)/p over all prime factors. Satisfies the Leibniz rule
     D(mn) = D(m)n + mD(n). Computable directly from the factorizations table.';


-- Next integer with identical exponent multiset
CREATE OR REPLACE FUNCTION signature_step(n_val BIGINT)
RETURNS BIGINT
LANGUAGE sql STABLE
AS $$
    WITH target_shape AS (
        SELECT array_agg(exponent ORDER BY exponent DESC) AS shape
        FROM factorizations WHERE n = n_val
    )
    SELECT i.n
    FROM integers i, target_shape
    WHERE i.n > n_val
      AND (
          SELECT array_agg(exponent ORDER BY exponent DESC)
          FROM factorizations f WHERE f.n = i.n
      ) = target_shape.shape
    ORDER BY i.n
    LIMIT 1;
$$;

COMMENT ON FUNCTION signature_step IS
    'Next integer > n with the same prime exponent multiset.
     Example: 12 (shape [2,1]) → 18 (= 2·3², also shape [2,1]).';


-- T(n) = n + primorial(k) — preserves residue class mod primorial(k)
CREATE OR REPLACE FUNCTION crt_lift_step(n_val BIGINT, depth INTEGER)
RETURNS BIGINT
LANGUAGE sql STABLE
AS $$
    SELECT n_val + (
        SELECT EXP(SUM(LN(p)))::BIGINT
        FROM (SELECT p FROM primes ORDER BY discovered_order LIMIT depth) sub
    );
$$;


-- rad(n) = product of distinct prime factors (each to first power)
CREATE OR REPLACE FUNCTION radical_step(n_val BIGINT)
RETURNS BIGINT
LANGUAGE sql STABLE
AS $$
    SELECT ROUND(EXP(SUM(LN(prime))))::BIGINT
    FROM factorizations WHERE n = n_val;
$$;

COMMENT ON FUNCTION radical_step IS
    'rad(n) = product of distinct prime factors of n. Fixed points are the
     squarefree numbers; iteration converges in one step.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 7. ORBIT TRACER
--    Iterates a named relation function from start_n, terminating on
--    cycle / out-of-range / max_steps. Writes one row per step into orbits.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE PROCEDURE trace_orbit(
    start_n   BIGINT,
    rel_type  TEXT,        -- 'ALIQUOT' | 'ARITH_DERIV' | 'SIGNATURE' | 'RADICAL'
    max_steps INTEGER DEFAULT 200,
    lim       BIGINT  DEFAULT 10000000
)
LANGUAGE plpgsql
AS $$
DECLARE
    current_n     BIGINT := start_n;
    next_n        BIGINT;
    oid           BIGINT;
    step          INTEGER := 0;
    visited       BIGINT[] := ARRAY[start_n];
    effective_lim BIGINT;
BEGIN
    -- Clamp lim to the actual extent of the integers table. Without this,
    -- an orbit that grows past the populated range (e.g. arithmetic-derivative
    -- from 12 escaping a N=1000 database) will FK-fail when recording the step.
    SELECT LEAST(lim, COALESCE(MAX(n), 0)) INTO effective_lim FROM integers;

    oid := nextval('orbit_id_seq');

    LOOP
        EXIT WHEN step >= max_steps;

        next_n := CASE rel_type
            WHEN 'ALIQUOT'     THEN aliquot_step(current_n)
            WHEN 'ARITH_DERIV' THEN arithmetic_derivative(current_n)
            WHEN 'SIGNATURE'   THEN signature_step(current_n)
            WHEN 'RADICAL'     THEN radical_step(current_n)
            ELSE NULL
        END;

        INSERT INTO orbits (orbit_id, step, n, rel_type, rel_params, cycle_close)
        VALUES (
            oid,
            step,
            current_n,
            rel_type,
            jsonb_build_object('next', next_n),
            next_n = ANY(visited)
        );

        EXIT WHEN next_n IS NULL;
        EXIT WHEN next_n > effective_lim;
        EXIT WHEN next_n = ANY(visited);   -- cycle detected

        visited   := array_append(visited, next_n);
        current_n := next_n;
        step      := step + 1;
    END LOOP;

    RAISE NOTICE 'Orbit % from n=% via %: % steps, terminal=%, cycle=%',
        oid, start_n, rel_type, step, current_n,
        (SELECT bool_or(cycle_close) FROM orbits WHERE orbit_id = oid);
END $$;

COMMENT ON PROCEDURE trace_orbit IS
    'Iterates a dynamical relation from start_n, recording the full trajectory.
     Detects cycles and out-of-range termination. Results queryable from orbits.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 8. SEQUENCE CO-MEMBERSHIP ANALYZER
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION shared_sequences(n_val BIGINT, m_val BIGINT)
RETURNS TABLE (
    seq_id      TEXT,
    name        TEXT,
    seq_type    TEXT,
    idx_n       BIGINT,
    idx_m       BIGINT,
    index_gap   BIGINT,
    crt_reason  TEXT
)
LANGUAGE sql STABLE
AS $$
    SELECT
        s.seq_id,
        s.name,
        s.seq_type,
        sm1.idx                                       AS idx_n,
        sm2.idx                                       AS idx_m,
        ABS(sm1.idx - sm2.idx)                        AS index_gap,
        CASE
            WHEN s.seq_type = 'congruence' THEN
                'Both ≡ ' || s.residue || ' (mod ' || s.modulus || ')'
            WHEN s.seq_type = 'multiplicative' THEN
                'Shared multiplicative property: ' || s.formula
            WHEN s.seq_type = 'arithmetic' THEN
                'Both in arithmetic progression with gap ' || ABS(sm1.idx - sm2.idx)
            ELSE
                'Co-members by: ' || COALESCE(s.formula, s.seq_type)
        END                                           AS crt_reason
    FROM sequence_membership sm1
    JOIN sequence_membership sm2
      ON sm1.seq_id = sm2.seq_id
     AND sm2.n      = m_val
    JOIN sequences s ON s.seq_id = sm1.seq_id
    WHERE sm1.n = n_val
    ORDER BY index_gap;
$$;

COMMENT ON FUNCTION shared_sequences IS
    'Sequences shared by n and m, with algebraic reason for co-membership.
     For congruence-type sequences this is a CRT characterization.';
