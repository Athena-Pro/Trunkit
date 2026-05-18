-- =============================================================================
--  CRT example queries — run after sql/04_crt.sql is loaded
-- =============================================================================


-- ── Basic CRT solve ──────────────────────────────────────────────────────────

-- x ≡ 3 (mod 7), x ≡ 1 (mod 11), x ≡ 6 (mod 5)
SELECT crt(ARRAY[3,1,6]::BIGINT[], ARRAY[7,11,5]::BIGINT[]);


-- ── Decompose and round-trip ─────────────────────────────────────────────────

-- Decompose 360 = 2³·3²·5 into its CRT coordinates
SELECT * FROM crt_decompose(360);

-- Reconstruct from coordinates
SELECT crt_reconstruct(ARRAY[0,0,0]::BIGINT[], ARRAY[8,9,5]::BIGINT[]);


-- ── Intersection of arithmetic progressions ──────────────────────────────────

-- Multiples of 2 ∩ multiples of 3 → multiples of 6
SELECT * FROM progression_intersect(0, 2, 0, 3);

-- Multiples of 5 offset by 2 ∩ multiples of 7 offset by 4
SELECT * FROM progression_intersect(2, 5, 4, 7);


-- ── Wheel sieve spokes ───────────────────────────────────────────────────────

-- First 3 primes (2,3,5): wheel mod 30 — 8 spokes (φ(30)=8)
SELECT * FROM wheel_spokes(3) ORDER BY spoke;

-- First 4 primes (2,3,5,7): wheel mod 210 — 48 spokes (φ(210)=48)
SELECT COUNT(*) FROM wheel_spokes(4);

-- Generate all prime candidates under 1000 using the 30-wheel
SELECT s.spoke + 30 * k AS candidate
FROM wheel_spokes(3) s,
     generate_series(0, 33) k
WHERE s.spoke + 30 * k BETWEEN 2 AND 1000
ORDER BY candidate;


-- ── Congruence query without table scan ──────────────────────────────────────

-- "Find all n ≤ 10000 where n ≡ 3 (mod 7) AND n ≡ 1 (mod 11) AND n ≡ 0 (mod 5)"
WITH solution AS (
    SELECT crt(ARRAY[3,1,0]::BIGINT[], ARRAY[7,11,5]::BIGINT[]) AS x
)
SELECT generate_series(solution.x, 10000, 7*11*5) AS n
FROM solution
WHERE solution.x <= 10000;


-- ── Verify factorizations via CRT roundtrip ──────────────────────────────────

WITH decomp AS (
    SELECT
        array_agg(residue     ORDER BY prime) AS residues,
        array_agg(prime_power ORDER BY prime) AS powers
    FROM crt_decompose(360)
)
SELECT crt_reconstruct(residues, powers) AS reconstructed
FROM decomp;
