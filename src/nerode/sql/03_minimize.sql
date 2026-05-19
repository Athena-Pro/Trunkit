-- =============================================================================
--  nerode — Step 03: nerode.minimize()
--  Hopcroft partition refinement → canonical minimal DFA.
--  Returns the id of the new minimized automaton.
--  The Myhill-Nerode partition certificate is returned as a JSONB witness.
-- =============================================================================

CREATE OR REPLACE FUNCTION nerode.minimize(p_automaton_id BIGINT)
RETURNS BIGINT AS $$
DECLARE
    v_auto       nerode.automata%ROWTYPE;
    v_symbols    TEXT[];
    v_sym        TEXT;
    v_new_id     BIGINT;

    -- Partition state: each state maps to a block id
    -- Stored in temp table _min_part(state_id INT, block_id INT)
    v_next_block INTEGER := 2;   -- 0 = non-accepting, 1 = accepting
    v_changed    BOOLEAN;

    -- For split detection
    v_block_id   INTEGER;
    v_ref_block  INTEGER;
    v_split_ids  INTEGER[];

    -- For building the result automaton
    v_init_state INTEGER;
    v_init_block INTEGER;
    v_sink_added INTEGER;
    v_partition  JSONB;   -- Nerode partition certificate

    -- Helpers
    v_state_count INTEGER;
    v_max_block   INTEGER;
BEGIN
    SELECT * INTO v_auto FROM nerode.automata WHERE id = p_automaton_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'nerode.minimize: automaton % not found', p_automaton_id;
    END IF;
    IF v_auto.type != 'DFA' THEN
        RAISE EXCEPTION 'nerode.minimize: automaton % is type %, expected DFA',
            p_automaton_id, v_auto.type;
    END IF;

    -- Fetch alphabet
    SELECT symbols INTO v_symbols
    FROM nerode.alphabets WHERE id = v_auto.alphabet_id;

    -- Complete the automaton (adds sink state if any transition is missing)
    PERFORM nerode.complete_dfa(p_automaton_id);

    -- -----------------------------------------------------------------------
    -- Build partition table
    -- -----------------------------------------------------------------------
    CREATE TEMP TABLE _min_part (
        state_id INTEGER PRIMARY KEY,
        block_id INTEGER NOT NULL
    ) ON COMMIT DROP;

    -- Initial partition: block 0 = non-accepting, block 1 = accepting
    INSERT INTO _min_part (state_id, block_id)
    SELECT state_id, CASE WHEN is_accepting THEN 1 ELSE 0 END
    FROM nerode.states
    WHERE automaton_id = p_automaton_id;

    -- -----------------------------------------------------------------------
    -- Hopcroft refinement loop
    -- Invariant: within each block every state is Nerode-equivalent so far.
    -- We split a block whenever two states in it have transitions on some
    -- symbol leading to different blocks.
    -- -----------------------------------------------------------------------
    v_changed := TRUE;
    WHILE v_changed LOOP
        v_changed := FALSE;

        FOR v_block_id IN
            SELECT DISTINCT block_id FROM _min_part ORDER BY block_id
        LOOP
            FOREACH v_sym IN ARRAY v_symbols LOOP
                -- Find the plurality target block for states in v_block_id
                -- (the one with the most states that transition there)
                SELECT mp2.block_id INTO v_ref_block
                FROM _min_part mp
                LEFT JOIN nerode.transitions t
                    ON  t.automaton_id = p_automaton_id
                    AND t.from_state   = mp.state_id
                    AND t.symbol       = v_sym
                LEFT JOIN _min_part mp2 ON mp2.state_id = t.to_state
                WHERE mp.block_id = v_block_id
                GROUP BY mp2.block_id
                ORDER BY count(*) DESC
                LIMIT 1;

                -- Collect states in this block whose transition on v_sym
                -- does NOT lead to v_ref_block → split candidates
                SELECT array_agg(mp.state_id) INTO v_split_ids
                FROM _min_part mp
                LEFT JOIN nerode.transitions t
                    ON  t.automaton_id = p_automaton_id
                    AND t.from_state   = mp.state_id
                    AND t.symbol       = v_sym
                LEFT JOIN _min_part mp2 ON mp2.state_id = t.to_state
                WHERE mp.block_id = v_block_id
                  AND (mp2.block_id IS DISTINCT FROM v_ref_block);

                IF v_split_ids IS NOT NULL
                   AND array_length(v_split_ids, 1) > 0
                   AND array_length(v_split_ids, 1) < (
                       SELECT count(*) FROM _min_part WHERE block_id = v_block_id
                   )
                THEN
                    -- Split: give split_ids a fresh block
                    UPDATE _min_part
                    SET block_id = v_next_block
                    WHERE state_id = ANY(v_split_ids);

                    v_next_block := v_next_block + 1;
                    v_changed := TRUE;
                END IF;
            END LOOP;
        END LOOP;
    END LOOP;

    -- -----------------------------------------------------------------------
    -- Build partition certificate (Myhill-Nerode witness)
    -- Each block = one equivalence class of Nerode-indistinguishable states
    -- -----------------------------------------------------------------------
    SELECT jsonb_object_agg(
        block_id::text,
        state_list
    ) INTO v_partition
    FROM (
        SELECT block_id,
               jsonb_agg(state_id ORDER BY state_id) AS state_list
        FROM _min_part
        GROUP BY block_id
    ) sub;

    -- -----------------------------------------------------------------------
    -- Construct the minimized DFA
    -- Block ids become the new state ids.
    -- -----------------------------------------------------------------------
    SELECT max(block_id) INTO v_max_block FROM _min_part;
    v_state_count := v_max_block + 1;

    INSERT INTO nerode.automata (name, type, alphabet_id, state_count, source_regex, provenance)
    VALUES (
        coalesce(v_auto.name, 'auto') || '_min',
        'DFA',
        v_auto.alphabet_id,
        v_state_count,
        v_auto.source_regex,
        jsonb_build_object(
            'operation',        'minimize',
            'input_id',         p_automaton_id,
            'source_regex',     v_auto.source_regex,
            'original_states',  v_auto.state_count,
            'minimal_states',   v_state_count,
            'created_at',       now()
        )
    )
    RETURNING id INTO v_new_id;

    -- Find which block contains the initial state
    SELECT mp.block_id INTO v_init_block
    FROM _min_part mp
    JOIN nerode.states s
        ON s.automaton_id = p_automaton_id AND s.state_id = mp.state_id
    WHERE s.is_initial = TRUE
    LIMIT 1;

    -- Insert one state per block
    INSERT INTO nerode.states (automaton_id, state_id, label, is_initial, is_accepting)
    SELECT DISTINCT ON (mp.block_id)
        v_new_id,
        mp.block_id,
        'q' || mp.block_id,
        (mp.block_id = v_init_block),
        s.is_accepting
    FROM _min_part mp
    JOIN nerode.states s
        ON s.automaton_id = p_automaton_id AND s.state_id = mp.state_id
    ORDER BY mp.block_id, s.state_id;

    -- Insert one transition per (block, symbol) — pick any representative state
    INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
    SELECT DISTINCT ON (mp.block_id, t.symbol)
        v_new_id,
        mp.block_id,
        t.symbol,
        mp2.block_id
    FROM _min_part mp
    JOIN nerode.transitions t
        ON  t.automaton_id = p_automaton_id
        AND t.from_state   = mp.state_id
    JOIN _min_part mp2 ON mp2.state_id = t.to_state
    ORDER BY mp.block_id, t.symbol, mp.state_id
    ON CONFLICT DO NOTHING;

    -- -----------------------------------------------------------------------
    -- Log construction
    -- -----------------------------------------------------------------------
    INSERT INTO nerode.construction_log (automaton_id, operation, inputs, result)
    VALUES (
        v_new_id,
        'minimize',
        jsonb_build_object('input_id', p_automaton_id),
        jsonb_build_object(
            'output_id',       v_new_id,
            'original_states', v_auto.state_count,
            'minimal_states',  v_state_count,
            'partition',       v_partition
        )
    );

    DROP TABLE IF EXISTS _min_part;
    RETURN v_new_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.minimize(BIGINT) IS
    'Hopcroft partition refinement: minimizes DFA p_automaton_id. '
    'Returns the id of the new canonical minimal DFA. '
    'The Myhill-Nerode partition (equivalence classes) is stored in construction_log.result.partition '
    'and can be attached as a cert.witness of kind nerode_partition.';

-- ---------------------------------------------------------------------------
-- Convenience: minimize and immediately certify.
-- Returns (new_automaton_id, cert_claim_id) so callers can chain into cert.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.minimize_certified(p_automaton_id BIGINT)
RETURNS TABLE (automaton_id BIGINT, claim_id BIGINT)
AS $$
DECLARE
    v_new_id    BIGINT;
    v_cl_id     BIGINT;
    v_orig_sc   INTEGER;
    v_min_sc    INTEGER;
    v_partition JSONB;
    v_seq       INTEGER;
    v_cert_id   BIGINT;
    v_wit_id    BIGINT;
    v_stmt      TEXT;
BEGIN
    v_new_id := nerode.minimize(p_automaton_id);

    -- Fetch minimization details from log
    SELECT (cl.result->>'original_states')::INTEGER,
           (cl.result->>'minimal_states')::INTEGER,
           cl.result->'partition'
    INTO v_orig_sc, v_min_sc, v_partition
    FROM nerode.construction_log cl
    WHERE cl.automaton_id = v_new_id AND cl.operation = 'minimize'
    ORDER BY cl.id DESC LIMIT 1;

    -- Claim statement
    v_stmt := format(
        'Minimal DFA %s has %s states (reduced from %s), Myhill-Nerode partition certified.',
        v_new_id, v_min_sc, v_orig_sc
    );

    -- Insert claim (idempotent via UNIQUE constraint on statement)
    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method)
    VALUES (
        'nerode_automaton',
        jsonb_build_object('automaton_id', v_new_id, 'source_id', p_automaton_id),
        v_stmt,
        'structural',
        'nerode_minimization'
    )
    ON CONFLICT (statement) DO UPDATE SET subject_ref = EXCLUDED.subject_ref
    RETURNING id INTO v_cl_id;

    -- Certificate sequence
    SELECT COALESCE(max(seq), 0) + 1 INTO v_seq
    FROM cert.certificate AS ce WHERE ce.claim_id = v_cl_id;

    INSERT INTO cert.certificate (claim_id, seq, status, evidence, valid_under)
    VALUES (
        v_cl_id, v_seq, 'valid',
        jsonb_build_object(
            'automaton_id',    v_new_id,
            'minimal_states',  v_min_sc,
            'original_states', v_orig_sc
        ),
        jsonb_build_object('nerode_schema_version', 1)
    )
    RETURNING id INTO v_cert_id;

    -- Attach Myhill-Nerode partition as witness
    INSERT INTO cert.witness (certificate_id, kind, body, schema_version)
    VALUES (
        v_cert_id,
        'nerode_partition',
        jsonb_build_object(
            'automaton_id',    v_new_id,
            'source_id',       p_automaton_id,
            'minimal_states',  v_min_sc,
            'original_states', v_orig_sc,
            'partition',       v_partition
        ),
        jsonb_build_object('nerode_schema_version', 1)
    )
    RETURNING id INTO v_wit_id;

    -- Mark automaton as certified
    UPDATE nerode.automata
    SET certified = TRUE, cert_claim_id = v_cl_id
    WHERE id = v_new_id;

    -- Update construction log with cert reference
    UPDATE nerode.construction_log
    SET cert_cert_id = v_cert_id
    WHERE id = (
        SELECT cl2.id FROM nerode.construction_log AS cl2
        WHERE cl2.automaton_id = v_new_id AND cl2.operation = 'minimize'
        ORDER BY cl2.id DESC LIMIT 1
    );

    RETURN QUERY SELECT v_new_id, v_cl_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.minimize_certified(BIGINT) IS
    'Minimize automaton and immediately issue a cert.certificate with a '
    'Myhill-Nerode partition witness. Returns (automaton_id, claim_id).';
