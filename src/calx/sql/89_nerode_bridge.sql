-- =============================================================================
--  calx — Step 89: Nerode bridge function
--
--  calx.arithmetic_facts(p_n)
--    Returns a single-row result set with multiplicative arithmetic data for
--    integer p_n, drawing from the pre-populated calx.integers / factorizations
--    / view stack. Consumed by nerode.calx_state_facts() on co-deployed DBs.
--
--  Idempotent: CREATE OR REPLACE.
-- =============================================================================

CREATE OR REPLACE FUNCTION calx.arithmetic_facts(p_n BIGINT)
RETURNS TABLE (
    n             BIGINT,
    is_prime      BOOLEAN,
    omega         INTEGER,          -- ω(n): distinct prime factors
    big_omega     INTEGER,          -- Ω(n): total prime factors with multiplicity
    is_squarefree BOOLEAN,
    signature     TEXT,             -- formatted: '2^3 · 3'
    tau           BIGINT,           -- τ(n): divisor count
    sigma         BIGINT,           -- σ(n): divisor sum
    derivative    BIGINT            -- n' = n · Σ(eᵢ/pᵢ)
)
LANGUAGE sql STABLE AS $$
    SELECT
        i.n,
        i.is_prime,
        i.omega,
        i.big_omega,
        i.is_squarefree,
        ps.signature,
        COALESCE(dc.tau,  1)::BIGINT       AS tau,
        COALESCE(ds.sigma, p_n)::BIGINT    AS sigma,
        ROUND(
            p_n * COALESCE(
                (SELECT SUM(f.exponent::DOUBLE PRECISION / f.prime::DOUBLE PRECISION)
                   FROM calx.factorizations f
                  WHERE f.n = p_n),
                0.0
            )
        )::BIGINT                           AS derivative
    FROM  calx.integers i
    LEFT JOIN calx.prime_signatures ps ON ps.n = i.n
    LEFT JOIN calx.divisor_count    dc ON dc.n = i.n
    LEFT JOIN calx.divisor_sum      ds ON ds.n = i.n
    WHERE i.n = p_n;
$$;

COMMENT ON FUNCTION calx.arithmetic_facts(BIGINT) IS
    'Return multiplicative arithmetic facts for n: primality, factorization '
    'signature, τ (divisor count), σ (divisor sum), and arithmetic derivative '
    'n'' = n · Σ(eᵢ/pᵢ). Used by nerode.calx_state_facts() on co-deployed DBs. '
    'Returns empty result set if n > calx universe (graceful degradation).';
