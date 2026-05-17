-- =============================================================================
--  OEIS orbit prefix matching — cache candidates per dynamical orbit trace
-- =============================================================================

CREATE TABLE IF NOT EXISTS oeis_match_candidates (
    orbit_id     BIGINT  NOT NULL,
    candidate_id INTEGER NOT NULL,
    oeis_id      TEXT,                   -- NULL when no match (cached negative)
    oeis_name    TEXT    NOT NULL DEFAULT '',
    prefix_len   INTEGER NOT NULL,
    confidence   DOUBLE PRECISION NOT NULL DEFAULT 0,
    raw_payload  JSONB,
    fetched_at   TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (orbit_id, candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_oeis_match_oeis_id ON oeis_match_candidates(oeis_id);
CREATE INDEX IF NOT EXISTS idx_oeis_match_prefix_hash
    ON oeis_match_candidates ((raw_payload->>'prefix_hash'));

COMMENT ON TABLE oeis_match_candidates IS
    'OEIS search hits for the leading prefix of each orbit trace. Confidence is
     leading-position agreement / query length. A row with oeis_id NULL and
     confidence 0 caches a no-match prefix to avoid re-fetching.';


-- Extend characterize_relation with SHARED_ORBIT_ID when both integers share
-- a traced orbit that has a high-confidence OEIS identification.
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

    -- ── CRT class agreement ────────────────────────────────────────────────
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

    -- ── Shared sequence membership ─────────────────────────────────────────
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

    -- ── Shared dynamical orbit (+ best OEIS identification if any) ─────────
    RETURN QUERY
    SELECT
        'SHARED_ORBIT_ID'::TEXT,
        jsonb_build_object(
            'orbit_id', o1.orbit_id,
            'rel_type', o1.rel_type,
            'oeis_match', m.oeis_id,
            'oeis_name', m.oeis_name,
            'confidence', m.confidence
        ),
        'Both in orbit ' || o1.orbit_id || ' (' || o1.rel_type || ')' ||
        COALESCE(' = ' || m.oeis_id || ' [' || m.oeis_name || ']', ' (no OEIS match)')
    FROM orbits o1
    JOIN orbits o2
      ON o2.orbit_id = o1.orbit_id
     AND o2.n        = m_val
    LEFT JOIN LATERAL (
        SELECT c.oeis_id, c.oeis_name, c.confidence
        FROM oeis_match_candidates c
        WHERE c.orbit_id = o1.orbit_id
          AND c.oeis_id IS NOT NULL
          AND c.confidence >= 0.8
          AND COALESCE(c.raw_payload->'scoring'->>'match_kind', '') <> 'tautology'
        ORDER BY c.confidence DESC, c.candidate_id
        LIMIT 1
    ) m ON TRUE
    WHERE o1.n = n_val;

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
