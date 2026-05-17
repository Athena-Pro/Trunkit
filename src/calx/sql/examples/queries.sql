-- =============================================================================
--  Example queries — run after CALL generate_integer_database(N)
-- =============================================================================


-- ── Basic lookups ────────────────────────────────────────────────────────────

-- Is 997 prime?
SELECT is_prime FROM integers WHERE n = 997;

-- What is the 1000th prime?
SELECT p FROM primes WHERE discovered_order = 1000;

-- Full factorization of 720
SELECT * FROM prime_signatures WHERE n = 720;
-- → '2^4 · 3^2 · 5'

-- All prime factors of 360 with their exponents
SELECT prime, exponent FROM factorizations WHERE n = 360 ORDER BY prime;


-- ── Cross-reference via OEIS sequence definitions ────────────────────────────

-- A001221 ω(n): distinct prime factors
SELECT n, omega FROM integers WHERE omega = 4 LIMIT 20;

-- A001222 Ω(n): prime factors with multiplicity
SELECT n, big_omega FROM integers WHERE big_omega = 7 LIMIT 20;

-- A005117 squarefree numbers
SELECT n FROM integers WHERE is_squarefree = TRUE LIMIT 50;

-- A002182 highly composite numbers: τ(n) > τ(k) for all k < n
WITH tau AS (SELECT * FROM divisor_count)
SELECT t1.n, t1.tau
FROM tau t1
WHERE NOT EXISTS (
    SELECT 1 FROM tau t2 WHERE t2.n < t1.n AND t2.tau >= t1.tau
)
ORDER BY t1.n;

-- A000396 perfect numbers (σ(n) = 2n)
SELECT n FROM perfect_numbers ORDER BY n;


-- ── Structural queries ───────────────────────────────────────────────────────

-- k-smooth numbers: all n whose largest prime factor ≤ 7 (7-smooth = "regular")
SELECT n FROM smooth_numbers WHERE largest_prime_factor <= 7 ORDER BY n LIMIT 30;

-- Twin prime pairs
SELECT p, p + 2 AS twin
FROM primes pr1
WHERE EXISTS (SELECT 1 FROM primes pr2 WHERE pr2.p = pr1.p + 2)
ORDER BY p
LIMIT 20;

-- Prime gaps
SELECT
    p                                  AS prime,
    LEAD(p) OVER (ORDER BY p) - p     AS gap_to_next,
    discovered_order
FROM primes
ORDER BY gap_to_next DESC NULLS LAST
LIMIT 10;

-- Numbers sharing the same prime signature shape (same exponent multiset)
WITH shapes AS (
    SELECT
        n,
        array_agg(exponent ORDER BY exponent DESC) AS exp_shape
    FROM factorizations
    GROUP BY n
)
SELECT exp_shape, array_agg(n ORDER BY n) AS members, COUNT(*) AS count
FROM shapes
GROUP BY exp_shape
HAVING COUNT(*) > 1
ORDER BY exp_shape DESC
LIMIT 20;

-- Semiprimes (Ω = 2)
SELECT n FROM integers WHERE big_omega = 2 ORDER BY n LIMIT 30;

-- 3-almost primes (Ω = 3)
SELECT n, omega, big_omega FROM integers WHERE big_omega = 3 ORDER BY n LIMIT 20;

-- p-adic valuation: all n where ν₂(n) = 5  (divisible by 32 but not 64)
SELECT n FROM factorizations
WHERE prime = 2 AND exponent = 5
ORDER BY n LIMIT 20;
