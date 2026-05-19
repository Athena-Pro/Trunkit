-- =============================================================================
--  nerode — Step 04: nerode.product() and nerode.equivalent()
--
--  product(id1, id2, op):  intersection / union via synchronous product DFA
--  equivalent(id1, id2):   symmetric-difference emptiness; bisimulation witness
-- =============================================================================

-- ---------------------------------------------------------------------------
-- nerode.product(id1, id2, op)
-- op ∈ {'intersection', 'union'}
-- State space Q₁ × Q₂, encoded as (q1 * OFFSET + q2) for OFFSET > max(|Q2|).
-- Returns new DFA id.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.product(
    p_id1 BIGINT,
    p_id2 BIGINT,
    p_op  TEXT     -- 'intersection' or 'union'
)
RETURNS BIGINT AS $$
DECLARE
    v_a1      nerode.automata%ROWTYPE;
    v_a2      nerode.automata%ROWTYPE;
    v_symbols TEXT[];
    v_new_id  BIGINT;

    v_offset  INTEGER;   -- encode (q1,q2) → q1*offset + q2

    -- Initial states
    v_init1   INTEGER;
    v_init2   INTEGER;

    -- Work queue: unprocessed product state ids (encoded)
    v_todo    INTEGER[];
    v_cur     INTEGER;
    v_q1      INTEGER;
    v_q2      INTEGER;
    v_q1n     INTEGER;
    v_q2n     INTEGER;
    v_qn      INTEGER;
    v_sym     TEXT;

    v_seen    INTEGER[];   -- visited encoded states
    v_init_enc INTEGER;
    v_acc1    BOOLEAN;
    v_acc2    BOOLEAN;
    v_acc     BOOLEAN;

    v_sink1   INTEGER := -1;   -- dead states for completion
    v_sink2   INTEGER := -1;
    v_state_count INTEGER;
BEGIN
    SELECT * INTO v_a1 FROM nerode.automata WHERE id = p_id1;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'nerode.product: automaton % not found', p_id1;
    END IF;
    SELECT * INTO v_a2 FROM nerode.automata WHERE id = p_id2;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'nerode.product: automaton % not found', p_id2;
    END IF;
    IF v_a1.type != 'DFA' OR v_a2.type != 'DFA' THEN
        RAISE EXCEPTION 'nerode.product: both automata must be DFA';
    END IF;
    IF v_a1.alphabet_id != v_a2.alphabet_id THEN
        RAISE EXCEPTION 'nerode.product: alphabet mismatch (%  vs %)',
            v_a1.alphabet_id, v_a2.alphabet_id;
    END IF;
    IF p_op NOT IN ('intersection', 'union') THEN
        RAISE EXCEPTION 'nerode.product: op must be intersection or union, got %', p_op;
    END IF;

    SELECT symbols INTO v_symbols
    FROM nerode.alphabets WHERE id = v_a1.alphabet_id;

    -- Complete both DFAs so product is total
    PERFORM nerode.complete_dfa(p_id1);
    PERFORM nerode.complete_dfa(p_id2);

    -- offset = max(state_id in M2) + 1, to avoid encoding collisions
    SELECT max(state_id) + 1 INTO v_offset FROM nerode.states WHERE automaton_id = p_id2;
    IF v_offset IS NULL OR v_offset < 1 THEN v_offset := 1; END IF;

    -- Initial states
    SELECT state_id INTO v_init1 FROM nerode.states
    WHERE automaton_id = p_id1 AND is_initial = TRUE LIMIT 1;
    SELECT state_id INTO v_init2 FROM nerode.states
    WHERE automaton_id = p_id2 AND is_initial = TRUE LIMIT 1;

    v_init_enc := v_init1 * v_offset + v_init2;

    -- Create result automaton (state_count will be updated at end)
    INSERT INTO nerode.automata (name, type, alphabet_id, state_count, provenance)
    VALUES (
        coalesce(v_a1.name,'a1') || '_' || p_op || '_' || coalesce(v_a2.name,'a2'),
        'DFA',
        v_a1.alphabet_id,
        0,
        jsonb_build_object(
            'operation', 'product',
            'op',        p_op,
            'input_id1', p_id1,
            'input_id2', p_id2,
            'created_at', now()
        )
    )
    RETURNING id INTO v_new_id;

    -- Pre-insert the initial product state (FK requires state to exist before transitions)
    SELECT is_accepting INTO v_acc1 FROM nerode.states WHERE automaton_id = p_id1 AND state_id = v_init1;
    SELECT is_accepting INTO v_acc2 FROM nerode.states WHERE automaton_id = p_id2 AND state_id = v_init2;
    IF p_op = 'intersection' THEN
        v_acc := COALESCE(v_acc1, FALSE) AND COALESCE(v_acc2, FALSE);
    ELSE
        v_acc := COALESCE(v_acc1, FALSE) OR COALESCE(v_acc2, FALSE);
    END IF;
    INSERT INTO nerode.states (automaton_id, state_id, label, is_initial, is_accepting)
    VALUES (v_new_id, v_init_enc, format('(%s,%s)', v_init1, v_init2), TRUE, v_acc);

    -- BFS over product states
    v_todo := ARRAY[v_init_enc];
    v_seen := ARRAY[]::INTEGER[];

    WHILE array_length(v_todo, 1) IS NOT NULL AND array_length(v_todo, 1) > 0 LOOP
        v_cur  := v_todo[1];
        v_todo := v_todo[2:];

        IF v_cur = ANY(v_seen) THEN CONTINUE; END IF;
        v_seen := v_seen || v_cur;

        v_q1 := v_cur / v_offset;
        v_q2 := v_cur % v_offset;

        FOREACH v_sym IN ARRAY v_symbols LOOP
            SELECT to_state INTO v_q1n FROM nerode.transitions WHERE automaton_id = p_id1 AND from_state = v_q1 AND symbol = v_sym;
            SELECT to_state INTO v_q2n FROM nerode.transitions WHERE automaton_id = p_id2 AND from_state = v_q2 AND symbol = v_sym;
            IF v_q1n IS NULL OR v_q2n IS NULL THEN CONTINUE; END IF;

            v_qn := v_q1n * v_offset + v_q2n;

            -- Insert target state before the transition that references it (FK constraint)
            IF NOT (v_qn = ANY(v_seen)) THEN
                SELECT is_accepting INTO v_acc1 FROM nerode.states WHERE automaton_id = p_id1 AND state_id = v_q1n;
                SELECT is_accepting INTO v_acc2 FROM nerode.states WHERE automaton_id = p_id2 AND state_id = v_q2n;
                IF p_op = 'intersection' THEN
                    v_acc := COALESCE(v_acc1, FALSE) AND COALESCE(v_acc2, FALSE);
                ELSE
                    v_acc := COALESCE(v_acc1, FALSE) OR COALESCE(v_acc2, FALSE);
                END IF;
                INSERT INTO nerode.states (automaton_id, state_id, label, is_initial, is_accepting)
                VALUES (v_new_id, v_qn, format('(%s,%s)', v_q1n, v_q2n), v_qn = v_init_enc, v_acc)
                ON CONFLICT DO NOTHING;
                IF NOT (v_qn = ANY(v_todo)) THEN
                    v_todo := v_todo || v_qn;
                END IF;
            END IF;

            INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
            VALUES (v_new_id, v_cur, v_sym, v_qn)
            ON CONFLICT DO NOTHING;
        END LOOP;
    END LOOP;

    -- Update state count
    SELECT count(*) INTO v_state_count
    FROM nerode.states WHERE automaton_id = v_new_id;

    UPDATE nerode.automata SET state_count = v_state_count WHERE id = v_new_id;

    -- Log
    INSERT INTO nerode.construction_log (automaton_id, operation, inputs, result)
    VALUES (
        v_new_id, 'product',
        jsonb_build_object('id1', p_id1, 'id2', p_id2, 'op', p_op),
        jsonb_build_object('output_id', v_new_id, 'states', v_state_count)
    );

    RETURN v_new_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.product(BIGINT, BIGINT, TEXT) IS
    'Build the synchronous product DFA of two DFAs under op ∈ {intersection, union}. '
    'Both automata must share the same alphabet. Returns the new DFA id.';

-- ---------------------------------------------------------------------------
-- nerode.equivalent(id1, id2)
-- Returns (equivalent BOOLEAN, witness JSONB).
-- Algorithm: L(M1) = L(M2)  iff  L(M1 △ M2) = ∅
--   where M1 △ M2 accepts exactly the strings where M1 and M2 disagree.
-- Witness is either a bisimulation (if equivalent) or a distinguishing string
-- (the shortest accepted word of the symmetric-difference DFA).
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.equivalent(p_id1 BIGINT, p_id2 BIGINT)
RETURNS TABLE (equivalent BOOLEAN, witness JSONB)
AS $$
DECLARE
    v_a1        nerode.automata%ROWTYPE;
    v_a2        nerode.automata%ROWTYPE;
    v_symbols   TEXT[];
    v_offset    INTEGER;

    -- XOR product BFS
    v_init1     INTEGER;
    v_init2     INTEGER;
    v_init_enc  INTEGER;

    v_todo      INTEGER[];
    v_cur       INTEGER;
    v_q1        INTEGER;
    v_q2        INTEGER;
    v_q1n       INTEGER;
    v_q2n       INTEGER;
    v_qn        INTEGER;
    v_sym       TEXT;
    v_seen      INTEGER[];

    -- BFS for shortest distinguishing string
    v_pred      JSONB := '{}'::JSONB;   -- enc → {from: enc, sym: text}
    v_found_enc INTEGER := NULL;
    v_acc1      BOOLEAN;
    v_acc2      BOOLEAN;
    v_is_xor    BOOLEAN;

    -- Witness construction
    v_bisim     JSONB;
    v_diststr   TEXT;
    v_path      TEXT[];
    v_node      INTEGER;
    v_entry     JSONB;
BEGIN
    SELECT * INTO v_a1 FROM nerode.automata WHERE id = p_id1;
    IF NOT FOUND THEN RAISE EXCEPTION 'nerode.equivalent: automaton % not found', p_id1; END IF;
    SELECT * INTO v_a2 FROM nerode.automata WHERE id = p_id2;
    IF NOT FOUND THEN RAISE EXCEPTION 'nerode.equivalent: automaton % not found', p_id2; END IF;
    IF v_a1.alphabet_id != v_a2.alphabet_id THEN
        RAISE EXCEPTION 'nerode.equivalent: alphabet mismatch';
    END IF;

    SELECT symbols INTO v_symbols
    FROM nerode.alphabets WHERE id = v_a1.alphabet_id;

    PERFORM nerode.complete_dfa(p_id1);
    PERFORM nerode.complete_dfa(p_id2);

    SELECT max(state_id) + 1 INTO v_offset FROM nerode.states WHERE automaton_id = p_id2;
    IF v_offset IS NULL OR v_offset < 1 THEN v_offset := 1; END IF;

    SELECT state_id INTO v_init1
    FROM nerode.states WHERE automaton_id = p_id1 AND is_initial = TRUE LIMIT 1;
    SELECT state_id INTO v_init2
    FROM nerode.states WHERE automaton_id = p_id2 AND is_initial = TRUE LIMIT 1;

    v_init_enc := v_init1 * v_offset + v_init2;

    -- BFS over product states to find any accepting XOR state
    v_todo := ARRAY[v_init_enc];
    v_seen := ARRAY[]::INTEGER[];

    WHILE array_length(v_todo, 1) IS NOT NULL
          AND array_length(v_todo, 1) > 0
          AND v_found_enc IS NULL
    LOOP
        v_cur  := v_todo[1];
        v_todo := v_todo[2:];

        IF v_cur = ANY(v_seen) THEN CONTINUE; END IF;
        v_seen := v_seen || v_cur;

        v_q1 := v_cur / v_offset;
        v_q2 := v_cur % v_offset;

        SELECT is_accepting INTO v_acc1
        FROM nerode.states WHERE automaton_id = p_id1 AND state_id = v_q1;
        SELECT is_accepting INTO v_acc2
        FROM nerode.states WHERE automaton_id = p_id2 AND state_id = v_q2;

        -- XOR acceptance: exactly one machine accepts
        v_is_xor := (COALESCE(v_acc1, FALSE) != COALESCE(v_acc2, FALSE));

        IF v_is_xor THEN
            v_found_enc := v_cur;
            EXIT;
        END IF;

        FOREACH v_sym IN ARRAY v_symbols LOOP
            SELECT to_state INTO v_q1n
            FROM nerode.transitions
            WHERE automaton_id = p_id1 AND from_state = v_q1 AND symbol = v_sym;
            SELECT to_state INTO v_q2n
            FROM nerode.transitions
            WHERE automaton_id = p_id2 AND from_state = v_q2 AND symbol = v_sym;

            IF v_q1n IS NULL OR v_q2n IS NULL THEN CONTINUE; END IF;

            v_qn := v_q1n * v_offset + v_q2n;

            IF NOT (v_qn = ANY(v_seen)) THEN
                v_todo := v_todo || v_qn;
                -- Record predecessor for path reconstruction
                IF NOT (v_pred ? v_qn::TEXT) THEN
                    v_pred := jsonb_set(v_pred, ARRAY[v_qn::TEXT],
                        jsonb_build_object('from', v_cur, 'sym', v_sym));
                END IF;
            END IF;
        END LOOP;
    END LOOP;

    IF v_found_enc IS NOT NULL THEN
        -- Non-equivalent: reconstruct distinguishing string via BFS predecessor chain
        v_path  := ARRAY[]::TEXT[];
        v_node  := v_found_enc;

        WHILE v_node != v_init_enc LOOP
            v_entry  := v_pred->v_node::TEXT;
            IF v_entry IS NULL THEN EXIT; END IF;
            v_path   := ARRAY[v_entry->>'sym'] || v_path;
            v_node   := (v_entry->>'from')::INTEGER;
        END LOOP;

        v_diststr := array_to_string(v_path, '');

        RETURN QUERY SELECT
            FALSE,
            jsonb_build_object(
                'kind',               'counterexample',
                'distinguishing_string', v_diststr,
                'path',               to_jsonb(v_path),
                'automaton_id1',      p_id1,
                'automaton_id2',      p_id2,
                'xor_state_enc',      v_found_enc,
                'note', 'The distinguishing string is accepted by exactly one of the two automata.'
            );
    ELSE
        -- Equivalent: the reachable product pairs form a bisimulation
        SELECT jsonb_agg(jsonb_build_object(
            'q1', enc / v_offset,
            'q2', enc % v_offset
        )) INTO v_bisim
        FROM unnest(v_seen) AS enc;

        RETURN QUERY SELECT
            TRUE,
            jsonb_build_object(
                'kind',          'bisimulation',
                'pairs',         v_bisim,
                'automaton_id1', p_id1,
                'automaton_id2', p_id2,
                'note', 'All reachable product pairs are listed; they form a forward bisimulation.'
            );
    END IF;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.equivalent(BIGINT, BIGINT) IS
    'Test language equivalence of two DFAs using symmetric-difference emptiness. '
    'Returns (equivalent BOOLEAN, witness JSONB). '
    'If equivalent: witness.kind = bisimulation (all reachable (q1,q2) pairs). '
    'If not: witness.kind = counterexample with the shortest distinguishing string.';
