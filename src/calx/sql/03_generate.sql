-- =============================================================================
--  calx — Generation procedures
--
--  Two entry points:
--    1. generate_integer_database(lim)        — pure PL/pgSQL, self-contained
--    2. generate_factorizations_only(lim)     — assumes primes table is pre-seeded
--                                               (e.g. by the primesieve harness)
--
--  Both share Phases 1, 4, 5; the primesieve path skips Phases 2–3.
-- =============================================================================


-- ─────────────────────────────────────────────────────────────────────────────
--  Algorithm outline (from spec)
--  ─────────────────────────────
--  Phase 1 — Seed integers 1..N, all marked prime optimistically.
--
--  Phase 2 — Sieve of Eratosthenes.
--            For each p from 2 to floor(√N):
--              If p is still prime, strike multiples starting at p² using
--              generate_series(p², N, p) — a pure arithmetic progression,
--              no modular arithmetic needed.
--
--  Phase 3 — Populate the primes table from surviving is_prime=TRUE rows.
--
--  Phase 4 — Build factorizations via layered arithmetic progressions.
--            For each prime p:
--              Layer k=1: INSERT (s, p, 1) for s in generate_series(p, N, p)
--                         Every multiple of p gets an initial exponent of 1.
--              Layer k=2: UPDATE exponent += 1 for s in generate_series(p², N, p²)
--                         Multiples of p² already have exponent 1; this raises
--                         them to 2.
--              Layer k=3: UPDATE for generate_series(p³, N, p³) — raises to 3
--              ...
--              Continue while p^k ≤ N.
--
--            Correctness: the exponent of prime p in n equals the number of
--            layers k such that p^k divides n — i.e., exactly ν_p(n).
--
--  Phase 5 — Compute ω(n), Ω(n), and squarefreeness from the factorization
--            table via a single aggregate UPDATE.
-- ─────────────────────────────────────────────────────────────────────────────


CREATE OR REPLACE PROCEDURE generate_integer_database(lim BIGINT)
LANGUAGE plpgsql
AS $$
DECLARE
    p          BIGINT;
    pk         BIGINT;
    prime_cnt  BIGINT;
    t_start    TIMESTAMPTZ;
BEGIN
    t_start := clock_timestamp();


    -- ── Phase 1: Seed ────────────────────────────────────────────────────────

    RAISE NOTICE '[1/5] Seeding integers 1..%  (%)', lim, clock_timestamp();

    INSERT INTO integers (n, is_prime, omega, big_omega, is_squarefree)
    VALUES (1, FALSE, 0, 0, TRUE)
    ON CONFLICT DO NOTHING;

    INSERT INTO integers (n, is_prime)
    SELECT gs, TRUE
    FROM generate_series(2, lim) AS gs
    ON CONFLICT DO NOTHING;


    -- ── Phase 2: Sieve of Eratosthenes ──────────────────────────────────────

    RAISE NOTICE '[2/5] Sieve of Eratosthenes up to sqrt(%) = %  (%)',
        lim, FLOOR(SQRT(lim::FLOAT))::BIGINT, clock_timestamp();

    FOR p IN 2 .. FLOOR(SQRT(lim::FLOAT))::BIGINT LOOP
        IF (SELECT is_prime FROM integers WHERE n = p) THEN
            UPDATE integers
            SET    is_prime = FALSE
            FROM   generate_series(p * p, lim, p) AS s
            WHERE  integers.n = s;
        END IF;
    END LOOP;


    -- ── Phase 3: Populate primes ─────────────────────────────────────────────

    RAISE NOTICE '[3/5] Populating primes table  (%)', clock_timestamp();

    INSERT INTO primes (p, discovered_order)
    SELECT n, ROW_NUMBER() OVER (ORDER BY n)
    FROM   integers
    WHERE  is_prime = TRUE;

    GET DIAGNOSTICS prime_cnt = ROW_COUNT;
    RAISE NOTICE '      % primes found under %', prime_cnt, lim;


    -- ── Phases 4 & 5 delegated to shared procedure ───────────────────────────

    CALL _build_factorizations_and_derived(lim);

    RAISE NOTICE 'Generation complete.  Total wall time: %',
        age(clock_timestamp(), t_start);

    RAISE NOTICE '─────────────────────────────────────────────';
    RAISE NOTICE 'Rows in integers:       %', (SELECT COUNT(*) FROM integers);
    RAISE NOTICE 'Rows in primes:         %', (SELECT COUNT(*) FROM primes);
    RAISE NOTICE 'Rows in factorizations: %', (SELECT COUNT(*) FROM factorizations);
    RAISE NOTICE '─────────────────────────────────────────────';
END $$;


-- ─────────────────────────────────────────────────────────────────────────────
--  Variant entry point used by the primesieve harness.
--  Caller is responsible for:
--    - Inserting (1, FALSE, 0, 0, TRUE) and (2..N, TRUE) into integers
--    - Marking is_prime correctly on integers
--    - COPYing the primes table from an external prime list
--  This procedure then runs Phases 4–5 only.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE PROCEDURE generate_factorizations_only(lim BIGINT)
LANGUAGE plpgsql
AS $$
DECLARE
    t_start TIMESTAMPTZ := clock_timestamp();
BEGIN
    RAISE NOTICE 'Skipping Phases 1-3 (primes pre-seeded by harness)';
    CALL _build_factorizations_and_derived(lim);

    RAISE NOTICE 'Generation complete.  Total wall time: %',
        age(clock_timestamp(), t_start);
END $$;


-- ─────────────────────────────────────────────────────────────────────────────
--  Internal: Phases 4 & 5, shared by both entry points.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE PROCEDURE _build_factorizations_and_derived(lim BIGINT)
LANGUAGE plpgsql
AS $$
DECLARE
    p   BIGINT;
    pk  BIGINT;
BEGIN
    -- ── Phase 4: Factorizations ──────────────────────────────────────────────

    RAISE NOTICE '[4/5] Building factorizations via layered progressions  (%)',
        clock_timestamp();

    FOR p IN SELECT pr.p FROM primes pr WHERE pr.p <= lim ORDER BY pr.p LOOP

        -- Layer k = 1: every multiple of p gets exponent 1
        INSERT INTO factorizations (n, prime, exponent)
        SELECT s, p, 1
        FROM   generate_series(p, lim, p) AS s;

        -- Layers k = 2, 3, ...: each multiple of p^k bumps exponent by 1.
        -- Stride multiplies by p each pass, so this runs at most log_p(N) times.
        --
        -- Diagonalization: intersection of stride-p and stride-p² is exactly
        -- stride-p² — every p-th member of the p-multiples lives in the p²
        -- family. The UPDATE exploits that diagonal identity.
        pk := p * p;
        WHILE pk <= lim LOOP
            UPDATE factorizations
            SET    exponent = exponent + 1
            FROM   generate_series(pk, lim, pk) AS s
            WHERE  factorizations.prime = p
              AND  factorizations.n     = s;

            pk := pk * p;
        END LOOP;

    END LOOP;


    -- ── Phase 5: Derived columns ─────────────────────────────────────────────

    RAISE NOTICE '[5/5] Computing ω(n), Ω(n), squarefree flag  (%)',
        clock_timestamp();

    UPDATE integers i
    SET
        omega         = agg.omega,
        big_omega     = agg.big_omega,
        is_squarefree = (agg.omega = agg.big_omega)
    FROM (
        SELECT
            n,
            COUNT(*)::INTEGER      AS omega,
            SUM(exponent)::INTEGER AS big_omega
        FROM factorizations
        GROUP BY n
    ) agg
    WHERE i.n = agg.n;
END $$;
