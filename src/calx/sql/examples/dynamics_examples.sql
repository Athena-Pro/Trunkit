-- =============================================================================
--  Dynamical-layer example queries — run after sql/05_dynamics.sql is loaded
-- =============================================================================


-- ── Relation characterization ────────────────────────────────────────────────

-- Full relation vector between 12 and 18 (both have signature shape [2,1])
SELECT * FROM characterize_relation(12, 18);

-- Full relation vector between 360 and 720 (divisor + many shared properties)
SELECT * FROM characterize_relation(360, 720);


-- ── Orbit tracing ────────────────────────────────────────────────────────────

-- Aliquot orbit from 220 — amicable pair with 284 (2-cycle)
CALL trace_orbit(220, 'ALIQUOT', 20);
SELECT step, n, rel_params FROM orbits
WHERE orbit_id = (SELECT MAX(orbit_id) FROM orbits)
ORDER BY step;

-- Aliquot orbit from 6 — perfect number, fixed point
CALL trace_orbit(6, 'ALIQUOT', 5);

-- Arithmetic derivative orbit from 12
CALL trace_orbit(12, 'ARITH_DERIV', 30);

-- Signature step orbit from 12 (shape [2,1])
CALL trace_orbit(12, 'SIGNATURE', 20);

-- Radical step from 72 = 2³·3² — converges to squarefree kernel
CALL trace_orbit(72, 'RADICAL', 5);


-- ── CRT neighborhood analysis ────────────────────────────────────────────────

-- p-adic neighbors of 1000 at depth 2 (mod 6)
SELECT m, distance FROM crt_class_neighbors(1000, 2)
WHERE distance <= 30
ORDER BY distance;

-- How the neighborhood shrinks as depth grows
SELECT
    depth,
    COUNT(*)      AS neighbor_count,
    MIN(distance) AS closest
FROM (
    SELECT 1 AS depth, * FROM crt_class_neighbors(1000, 1) WHERE distance <= 1000
    UNION ALL
    SELECT 2, * FROM crt_class_neighbors(1000, 2) WHERE distance <= 1000
    UNION ALL
    SELECT 3, * FROM crt_class_neighbors(1000, 3) WHERE distance <= 1000
    UNION ALL
    SELECT 4, * FROM crt_class_neighbors(1000, 4) WHERE distance <= 1000
) sub
GROUP BY depth ORDER BY depth;


-- ── Aliquot cycle discovery across all n ≤ 1000 ──────────────────────────────

DO $$
DECLARE n BIGINT;
BEGIN
    FOR n IN SELECT i.n FROM integers i WHERE i.n BETWEEN 2 AND 1000 LOOP
        CALL trace_orbit(n, 'ALIQUOT', 50);
    END LOOP;
END $$;

-- Distinct cycles detected
SELECT DISTINCT o1.n AS cycle_entry, o2.n AS cycle_return,
       o2.step - o1.step AS cycle_length
FROM orbits o1
JOIN orbits o2 ON o1.orbit_id = o2.orbit_id
               AND o1.n = (o2.rel_params->>'next')::BIGINT
               AND o2.cycle_close = TRUE
ORDER BY cycle_length, cycle_entry;
