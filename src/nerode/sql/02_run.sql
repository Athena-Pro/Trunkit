-- =============================================================================
--  nerode — Step 02: nerode.run()
--  DFA simulation with certified computation trace.
--  Returns (accept BOOLEAN, evidence JSONB, cert_witness JSONB).
-- =============================================================================

-- Replace any existing 2-arg overload so callers with (BIGINT, TEXT) route here.
DROP FUNCTION IF EXISTS nerode.run(BIGINT, TEXT);

CREATE OR REPLACE FUNCTION nerode.run(
    p_automaton_id BIGINT,
    p_input        TEXT,
    p_trace        BOOLEAN DEFAULT TRUE   -- FALSE = fast path: no JSON built at all
)
RETURNS TABLE (accept BOOLEAN, evidence JSONB, cert_witness JSONB)
AS $$
DECLARE
    v_type   TEXT;
    v_state  INTEGER;
    v_next   INTEGER;
    v_sym    TEXT;
    v_i      INTEGER;
    v_accept BOOLEAN;
    v_parts  TEXT[];   -- one JSON text per step; parsed into JSONB once at the end
    v_steps  JSONB;
BEGIN
    -- Validate
    SELECT type INTO v_type FROM nerode.automata WHERE id = p_automaton_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'nerode.run: automaton % not found', p_automaton_id;
    END IF;
    IF v_type != 'DFA' THEN
        RAISE EXCEPTION 'nerode.run: automaton % has type %, expected DFA',
            p_automaton_id, v_type;
    END IF;

    SELECT state_id INTO v_state FROM nerode.states
    WHERE automaton_id = p_automaton_id AND is_initial = TRUE LIMIT 1;
    IF v_state IS NULL THEN
        RAISE EXCEPTION 'nerode.run: automaton % has no initial state', p_automaton_id;
    END IF;

    IF NOT p_trace THEN
        -- ── Fast path: pure DFA walk, zero JSON ──────────────────────────
        FOR v_i IN 1..length(p_input) LOOP
            SELECT to_state INTO v_next FROM nerode.transitions
            WHERE automaton_id = p_automaton_id
              AND from_state   = v_state
              AND symbol       = substring(p_input, v_i, 1);
            IF v_next IS NULL THEN
                RETURN QUERY SELECT FALSE, NULL::JSONB, NULL::JSONB;
                RETURN;
            END IF;
            v_state := v_next;
        END LOOP;
        SELECT is_accepting INTO v_accept FROM nerode.states
        WHERE automaton_id = p_automaton_id AND state_id = v_state;
        RETURN QUERY SELECT COALESCE(v_accept, FALSE), NULL::JSONB, NULL::JSONB;
        RETURN;
    END IF;

    -- ── Traced path: loop + TEXT[] accumulation, one JSONB parse at the end ─
    -- Each indexed slot write is O(1); avoids the O(k) JSONB structural copy
    -- that v_steps || jsonb_build_array(...) would incur at every step k.
    v_parts[1] := jsonb_build_object('step', 0, 'state', v_state, 'sym', NULL)::TEXT;

    FOR v_i IN 1..length(p_input) LOOP
        v_sym := substring(p_input, v_i, 1);

        SELECT to_state INTO v_next FROM nerode.transitions
        WHERE automaton_id = p_automaton_id
          AND from_state   = v_state
          AND symbol       = v_sym;

        v_parts[v_i + 1] := jsonb_build_object(
            'step', v_i, 'state', v_state, 'sym', v_sym, 'next', v_next
        )::TEXT;

        IF v_next IS NULL THEN
            v_steps := ('[' || array_to_string(v_parts[1:v_i + 1], ',') || ']')::JSONB;
            RETURN QUERY SELECT
                FALSE,
                jsonb_build_object('input', p_input, 'final_state', v_state,
                                   'accept', FALSE,
                                   'reason', format('no transition from state %s on %L',
                                                    v_state, v_sym)),
                jsonb_build_object('kind', 'computation_trace',
                                   'automaton_id', p_automaton_id,
                                   'input', p_input, 'final_state', v_state,
                                   'accept', FALSE, 'steps', v_steps);
            RETURN;
        END IF;

        v_state := v_next;
    END LOOP;

    v_steps := ('[' || array_to_string(v_parts, ',') || ']')::JSONB;

    SELECT is_accepting INTO v_accept FROM nerode.states
    WHERE automaton_id = p_automaton_id AND state_id = v_state;
    v_accept := COALESCE(v_accept, FALSE);

    RETURN QUERY SELECT
        v_accept,
        jsonb_build_object('input', p_input, 'final_state', v_state,
                           'accept', v_accept),
        jsonb_build_object('kind', 'computation_trace',
                           'automaton_id', p_automaton_id,
                           'input', p_input, 'final_state', v_state,
                           'accept', v_accept, 'steps', v_steps);
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.run(BIGINT, TEXT, BOOLEAN) IS
    'Simulate DFA p_automaton_id on p_input. '
    'p_trace=TRUE (default): returns full computation-trace JSONB for cert.witness. '
    'p_trace=FALSE (speed mode): returns (accept, NULL, NULL) with no JSON built — '
    '~2x faster for high-throughput membership queries that do not need certification. '
    'Returns (accept BOOLEAN, evidence JSONB, cert_witness JSONB).';


-- ---------------------------------------------------------------------------
-- nerode.run_to_state
--
-- Walk DFA p_automaton_id on p_input; return the final state_id.
-- Fast path only — no JSON, no accept flag.
-- Returns NULL if any transition is missing (dead/incomplete automaton).
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.run_to_state(
    p_automaton_id BIGINT,
    p_input        TEXT
)
RETURNS INT
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_state  INTEGER;
    v_next   INTEGER;
    v_i      INTEGER;
BEGIN
    SELECT state_id INTO v_state
    FROM   nerode.states
    WHERE  automaton_id = p_automaton_id AND is_initial = TRUE
    LIMIT  1;

    IF v_state IS NULL THEN
        RETURN NULL;
    END IF;

    FOR v_i IN 1..length(p_input) LOOP
        SELECT to_state INTO v_next
        FROM   nerode.transitions
        WHERE  automaton_id = p_automaton_id
          AND  from_state   = v_state
          AND  symbol       = substring(p_input, v_i, 1);

        IF v_next IS NULL THEN
            RETURN NULL;
        END IF;

        v_state := v_next;
    END LOOP;

    RETURN v_state;
END;
$$;

COMMENT ON FUNCTION nerode.run_to_state(BIGINT, TEXT) IS
    'Walk DFA p_automaton_id on p_input and return the final state_id. '
    'No JSON, no accept flag — pure state pointer for handoff envelopes. '
    'Returns NULL on any missing transition.';


-- ---------------------------------------------------------------------------
-- nerode.complement()
-- Complement of a DFA: complete the automaton then flip accepting ↔ non-accepting.
-- Returns new automaton id.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.complement(p_automaton_id BIGINT)
RETURNS BIGINT AS $$
DECLARE
    v_auto    nerode.automata%ROWTYPE;
    v_new_id  BIGINT;
BEGIN
    SELECT * INTO v_auto FROM nerode.automata WHERE id = p_automaton_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'nerode.complement: automaton % not found', p_automaton_id;
    END IF;
    IF v_auto.type != 'DFA' THEN
        RAISE EXCEPTION 'nerode.complement: automaton % is type %, expected DFA',
            p_automaton_id, v_auto.type;
    END IF;

    -- Clone automaton metadata
    INSERT INTO nerode.automata (name, type, alphabet_id, state_count, source_regex, provenance)
    VALUES (
        coalesce(v_auto.name, 'auto') || '_complement',
        'DFA',
        v_auto.alphabet_id,
        v_auto.state_count,
        NULL,
        jsonb_build_object(
            'operation',  'complement',
            'input_id',   p_automaton_id,
            'created_at', now()
        )
    )
    RETURNING id INTO v_new_id;

    -- Clone states, flipping is_accepting
    INSERT INTO nerode.states (automaton_id, state_id, label, is_initial, is_accepting)
    SELECT v_new_id, state_id, label, is_initial, NOT is_accepting
    FROM nerode.states
    WHERE automaton_id = p_automaton_id;

    -- Copy all transitions from original
    INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
    SELECT v_new_id, from_state, symbol, to_state
    FROM nerode.transitions
    WHERE automaton_id = p_automaton_id
    ON CONFLICT DO NOTHING;

    -- Complete the clone (adds sink state + missing transitions if any)
    PERFORM nerode.complete_dfa(v_new_id);

    -- Log construction
    INSERT INTO nerode.construction_log (automaton_id, operation, inputs, result)
    VALUES (v_new_id, 'complement',
        jsonb_build_object('input_id', p_automaton_id),
        jsonb_build_object('output_id', v_new_id));

    RETURN v_new_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.complement(BIGINT) IS
    'Return the complement DFA of automaton p_automaton_id. '
    'Completes the DFA (adds sink state if needed) then flips all accepting flags. '
    'Returns the new automaton id.';
