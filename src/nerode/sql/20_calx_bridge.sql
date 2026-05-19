-- =============================================================================
--  nerode — Step 07: calx bridge
--
--  nerode.calx_state_facts()   — arithmetic facts about automaton state counts
--                                using calx schema if available, else pure SQL
--  nerode.state_count_report() — tabular report of all automata with arithmetic
-- =============================================================================

-- ---------------------------------------------------------------------------
-- nerode.calx_state_facts(p_automaton_id)
-- Returns JSONB with arithmetic facts about the automaton's state count.
-- If the calx schema is installed (Trunkit co-deployment), delegates to
-- calx.factorize() and calx.prime_factors(); otherwise computes locally.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.calx_state_facts(p_automaton_id BIGINT)
RETURNS JSONB AS $$
DECLARE
    v_n         INTEGER;
    v_name      TEXT;
    v_calx_ok   BOOLEAN := FALSE;
    v_facts     JSONB;
    v_factors   INTEGER[];
    v_i         INTEGER;
    v_rem       INTEGER;
    v_p         INTEGER;
    v_is_prime  BOOLEAN;
    v_primes    INTEGER[];
    v_smallest  INTEGER;
BEGIN
    -- Fetch state count
    SELECT state_count, name INTO v_n, v_name
    FROM nerode.automata WHERE id = p_automaton_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'nerode.calx_state_facts: automaton % not found', p_automaton_id;
    END IF;

    -- Check whether calx schema is available
    SELECT EXISTS (
        SELECT 1 FROM information_schema.schemata WHERE schema_name = 'calx'
    ) INTO v_calx_ok;

    -- -----------------------------------------------------------------------
    -- Core arithmetic: trial-division factorization (self-contained fallback)
    -- -----------------------------------------------------------------------
    v_factors := ARRAY[]::INTEGER[];
    v_rem     := v_n;

    IF v_rem > 1 THEN
        -- Factor out 2
        WHILE v_rem % 2 = 0 LOOP
            v_factors := v_factors || 2;
            v_rem     := v_rem / 2;
        END LOOP;
        -- Odd factors from 3 upward
        v_p := 3;
        WHILE v_p * v_p <= v_rem LOOP
            WHILE v_rem % v_p = 0 LOOP
                v_factors := v_factors || v_p;
                v_rem     := v_rem / v_p;
            END LOOP;
            v_p := v_p + 2;
        END LOOP;
        IF v_rem > 1 THEN
            v_factors := v_factors || v_rem;
        END IF;
    END IF;

    v_is_prime := (array_length(v_factors, 1) = 1 AND v_factors[1] = v_n AND v_n > 1)
                  OR (v_n = 1 AND array_length(v_factors, 1) IS NULL);

    v_smallest := CASE WHEN array_length(v_factors, 1) > 0 THEN v_factors[1] ELSE NULL END;

    -- Unique prime factors
    SELECT array_agg(DISTINCT f ORDER BY f) INTO v_primes
    FROM unnest(v_factors) AS f;

    -- -----------------------------------------------------------------------
    -- Build result
    -- -----------------------------------------------------------------------
    v_facts := jsonb_build_object(
        'automaton_id',      p_automaton_id,
        'name',              v_name,
        'state_count',       v_n,
        'factorization',     to_jsonb(v_factors),
        'prime_factors',     to_jsonb(v_primes),
        'is_prime',          v_is_prime,
        'smallest_factor',   v_smallest,
        'pumping_constant',  v_n,       -- |Q| is the pumping lemma constant for this DFA
        'calx_available',    v_calx_ok
    );

    -- -----------------------------------------------------------------------
    -- If calx schema is present, add extra facts from calx.arithmetic_facts
    -- (soft dependency — ignore any error gracefully)
    -- -----------------------------------------------------------------------
    IF v_calx_ok THEN
        BEGIN
            DECLARE v_calx_row JSONB;
            BEGIN
                EXECUTE format(
                    'SELECT row_to_json(r)::JSONB FROM calx.arithmetic_facts(%L) r',
                    v_n
                ) INTO v_calx_row;

                IF v_calx_row IS NOT NULL THEN
                    v_facts := v_facts || jsonb_build_object('calx_facts', v_calx_row);
                END IF;
            END;
        EXCEPTION WHEN OTHERS THEN
            -- calx schema present but function signature differs; degrade gracefully
            v_facts := v_facts || jsonb_build_object(
                'calx_note', 'calx schema found but calx.arithmetic_facts() unavailable'
            );
        END;
    END IF;

    RETURN v_facts;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.calx_state_facts(BIGINT) IS
    'Return arithmetic facts about the automaton state count: prime factorization, '
    'pumping lemma constant, primality. Delegates to calx schema if installed.';

-- ---------------------------------------------------------------------------
-- nerode.product_state_bound(id1, id2)
-- Returns |Q1 × Q2| and its factorization as a calx-style JSONB fact.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.product_state_bound(p_id1 BIGINT, p_id2 BIGINT)
RETURNS JSONB AS $$
DECLARE
    v_n1    INTEGER;
    v_n2    INTEGER;
    v_prod  INTEGER;
BEGIN
    SELECT state_count INTO v_n1 FROM nerode.automata WHERE id = p_id1;
    SELECT state_count INTO v_n2 FROM nerode.automata WHERE id = p_id2;

    IF v_n1 IS NULL THEN RAISE EXCEPTION 'automaton % not found', p_id1; END IF;
    IF v_n2 IS NULL THEN RAISE EXCEPTION 'automaton % not found', p_id2; END IF;

    v_prod := v_n1 * v_n2;

    RETURN jsonb_build_object(
        'automaton_id1',      p_id1,
        'automaton_id2',      p_id2,
        'state_count_1',      v_n1,
        'state_count_2',      v_n2,
        'product_bound',      v_prod,
        'id1_facts',          nerode.calx_state_facts(p_id1),
        'id2_facts',          nerode.calx_state_facts(p_id2),
        'note', format(
            'Product DFA has at most %s states (%s × %s). '
            'Actual reachable states may be fewer.',
            v_prod, v_n1, v_n2
        )
    );
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.product_state_bound(BIGINT, BIGINT) IS
    'Upper bound |Q1| × |Q2| on product DFA states, with calx arithmetic facts for each factor.';

-- ---------------------------------------------------------------------------
-- nerode.state_count_report()
-- Returns one row per automaton with arithmetic summary.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.state_count_report()
RETURNS TABLE (
    automaton_id  BIGINT,
    name          TEXT,
    type          TEXT,
    state_count   INTEGER,
    is_prime      BOOLEAN,
    prime_factors JSONB,
    certified     BOOLEAN
) LANGUAGE sql AS $$
    SELECT
        au.id,
        au.name,
        au.type,
        au.state_count,
        -- Inline primality: state_count > 1 and has no divisor in 2..sqrt(n)
        (au.state_count > 1 AND NOT EXISTS (
            SELECT 1
            FROM generate_series(2, floor(sqrt(au.state_count::FLOAT))::INTEGER) AS d
            WHERE au.state_count % d = 0
        )) AS is_prime,
        (nerode.calx_state_facts(au.id))->'prime_factors' AS prime_factors,
        au.certified
    FROM nerode.automata au
    ORDER BY au.id;
$$;

COMMENT ON FUNCTION nerode.state_count_report() IS
    'Tabular report of all automata with state count arithmetic metadata.';
