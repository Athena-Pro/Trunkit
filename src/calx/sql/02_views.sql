-- =============================================================================
--  calx — Views
--  Derived multiplicative functions: σ, τ, ω, Ω, prime signatures
-- =============================================================================


-- Human-readable factorization:  360 → '2^3 · 3^2 · 5'
CREATE OR REPLACE VIEW prime_signatures AS
    SELECT
        n,
        string_agg(
            prime::text || CASE WHEN exponent > 1 THEN '^' || exponent ELSE '' END,
            ' · '
            ORDER BY prime
        ) AS signature
    FROM factorizations
    GROUP BY n;

COMMENT ON VIEW prime_signatures IS
    'Formatted factorization string for every composite/prime in the database';


-- τ(n): number of divisors  =  Π (eₖ + 1)
--   log-sum trick avoids overflow on large products
CREATE OR REPLACE VIEW divisor_count AS
    SELECT
        n,
        ROUND(EXP(SUM(LN(exponent + 1))))::BIGINT AS tau
    FROM factorizations
    GROUP BY n;

COMMENT ON VIEW divisor_count IS
    'τ(n): total number of positive divisors of n';


-- σ(n): sum of all divisors  =  Π ( (pᵉ⁺¹ − 1) / (p − 1) )
CREATE OR REPLACE VIEW divisor_sum AS
    SELECT
        n,
        ROUND(EXP(SUM(LN(
            (POWER(prime, exponent + 1) - 1.0) / (prime - 1.0)
        ))))::BIGINT AS sigma
    FROM factorizations
    GROUP BY n;

COMMENT ON VIEW divisor_sum IS
    'σ(n): sum of all positive divisors of n (multiplicative function)';


-- Largest prime factor of each n — used to classify smooth numbers
CREATE OR REPLACE VIEW smooth_numbers AS
    SELECT
        n,
        MAX(prime) AS largest_prime_factor
    FROM factorizations
    GROUP BY n;

COMMENT ON VIEW smooth_numbers IS
    'Largest prime factor of n; filter on largest_prime_factor <= k for k-smooth numbers';


-- Perfect numbers: σ(n) = 2n
CREATE OR REPLACE VIEW perfect_numbers AS
    SELECT ds.n
    FROM divisor_sum ds
    WHERE ds.sigma = 2 * ds.n;

-- Abundant numbers: σ(n) > 2n
CREATE OR REPLACE VIEW abundant_numbers AS
    SELECT ds.n
    FROM divisor_sum ds
    WHERE ds.sigma > 2 * ds.n;

-- Deficient numbers: σ(n) < 2n
CREATE OR REPLACE VIEW deficient_numbers AS
    SELECT ds.n
    FROM divisor_sum ds
    WHERE ds.sigma < 2 * ds.n;
