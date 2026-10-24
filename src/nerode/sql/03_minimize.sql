-- =============================================================================
--  nerode — Step 03: nerode.minimize()
--  Hopcroft worklist refinement, with an implicit rejecting sink.
--  Refinement runs in indexed PL/pgSQL arrays; only the result is persisted.
-- =============================================================================

CREATE OR REPLACE FUNCTION nerode.minimize(p_automaton_id BIGINT)
RETURNS BIGINT AS $$
DECLARE
    v_auto nerode.automata%ROWTYPE;
    v_symbols TEXT[];
    v_k INTEGER;
    v_n INTEGER;
    v_total INTEGER;
    v_original INTEGER;
    v_initial INTEGER;
    v_initial_count INTEGER;
    v_sink INTEGER := 0;
    v_m INTEGER := 0;
    v_edge RECORD;
    v_i INTEGER;
    v_j INTEGER;
    v_q INTEGER;
    v_p INTEGER;
    v_b INTEGER;
    v_new_b INTEGER;
    v_a INTEGER;
    v_c INTEGER;
    v_slot INTEGER;
    v_e INTEGER;
    v_blocks INTEGER := 2;
    v_small INTEGER;
    v_accept BOOLEAN[];

    -- Zero forward entries mean missing transitions, not stored sink edges.
    -- The reverse adjacency lists contain explicit input edges only.
    v_dest INTEGER[];
    v_reverse INTEGER[];
    v_edge_from INTEGER[] := '{}';
    v_edge_next INTEGER[] := '{}';

    -- Intrusive block lists let a split move only the smaller half.
    v_block INTEGER[];
    v_first INTEGER[];
    v_size INTEGER[];
    v_next INTEGER[];
    v_prev INTEGER[];
    v_work INTEGER[] := '{}';
    v_work_count INTEGER := 0;

    -- Mark predecessors and group them by their current block in linear time.
    v_stamp INTEGER := 0;
    v_mark INTEGER[];
    v_hit_first INTEGER[];
    v_hit_next INTEGER[];
    v_hit_count INTEGER[];
    v_touched INTEGER[] := '{}';
    v_touched_count INTEGER;
    v_move INTEGER;

    -- BFS numbering makes q0 initial and the output independent of input ids.
    v_number INTEGER[];
    v_order INTEGER[] := '{}';
    v_output_count INTEGER;
    v_output_sink INTEGER;
    v_partition JSONB;
    v_new_id BIGINT;
BEGIN
    SELECT * INTO v_auto FROM nerode.automata WHERE id = p_automaton_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'nerode.minimize: automaton % not found', p_automaton_id;
    END IF;
    IF v_auto.type != 'DFA' THEN
        RAISE EXCEPTION 'nerode.minimize: automaton % is type %, expected DFA',
            p_automaton_id, v_auto.type;
    END IF;

    SELECT ARRAY(SELECT DISTINCT s COLLATE "C" FROM unnest(a.symbols) s ORDER BY 1)
    INTO v_symbols FROM nerode.alphabets a WHERE a.id = v_auto.alphabet_id;
    IF array_position(v_symbols, NULL) IS NOT NULL THEN
        RAISE EXCEPTION 'nerode.minimize: alphabet contains a NULL symbol';
    END IF;
    v_k := cardinality(v_symbols);

    SELECT count(*), count(*) FILTER (WHERE is_initial)
    INTO v_original, v_initial_count
    FROM nerode.states WHERE automaton_id = p_automaton_id;
    IF v_initial_count != 1 THEN
        RAISE EXCEPTION 'nerode.minimize: expected exactly one initial state, found %',
            v_initial_count;
    END IF;
    IF EXISTS (
        SELECT 1 FROM nerode.transitions
        WHERE automaton_id = p_automaton_id
          AND (symbol IS NULL OR NOT (symbol = ANY(v_symbols)))
    ) THEN
        RAISE EXCEPTION 'nerode.minimize: DFA has epsilon or out-of-alphabet transitions';
    END IF;
    IF EXISTS (
        SELECT 1 FROM nerode.transitions WHERE automaton_id = p_automaton_id
        GROUP BY from_state, symbol HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'nerode.minimize: nondeterministic DFA';
    END IF;

    -- Dense internal indices also support arbitrary signed input state ids.
    -- Unreachable states must not survive language minimization.
    CREATE TEMP TABLE _min_states (
        state_id INTEGER PRIMARY KEY,
        idx INTEGER UNIQUE NOT NULL,
        is_accepting BOOLEAN NOT NULL
    ) ON COMMIT DROP;
    INSERT INTO _min_states
    WITH RECURSIVE reachable(state_id) AS (
        SELECT state_id FROM nerode.states
        WHERE automaton_id = p_automaton_id AND is_initial
        UNION
        SELECT t.to_state FROM reachable r JOIN nerode.transitions t
          ON t.automaton_id = p_automaton_id AND t.from_state = r.state_id
    )
    SELECT r.state_id, row_number() OVER (ORDER BY r.state_id)::INTEGER, s.is_accepting
    FROM reachable r JOIN nerode.states s
      ON s.automaton_id = p_automaton_id AND s.state_id = r.state_id;
    GET DIAGNOSTICS v_n = ROW_COUNT;
    ANALYZE _min_states;
    SELECT ms.idx INTO v_initial FROM _min_states ms JOIN nerode.states s
      ON s.automaton_id = p_automaton_id AND s.state_id = ms.state_id
    WHERE s.is_initial;
    SELECT array_agg(is_accepting ORDER BY idx) INTO v_accept FROM _min_states;

    v_dest := array_fill(0, ARRAY[(v_n + 1) * v_k]);
    v_reverse := array_fill(0, ARRAY[(v_n + 1) * v_k]);
    FOR v_edge IN
        SELECT src.idx AS src, dst.idx AS dst, sym.n::INTEGER AS sym
        FROM nerode.transitions t
        JOIN _min_states src ON src.state_id = t.from_state
        JOIN _min_states dst ON dst.state_id = t.to_state
        JOIN unnest(v_symbols) WITH ORDINALITY sym(symbol, n) ON sym.symbol = t.symbol
        WHERE t.automaton_id = p_automaton_id
    LOOP
        v_m := v_m + 1;
        v_dest[(v_edge.src - 1) * v_k + v_edge.sym] := v_edge.dst;
        v_slot := (v_edge.dst - 1) * v_k + v_edge.sym;
        v_edge_from[v_m] := v_edge.src;
        v_edge_next[v_m] := v_reverse[v_slot];
        v_reverse[v_slot] := v_m;
    END LOOP;

    v_total := v_n;
    IF v_m < v_n::BIGINT * v_k THEN
        v_sink := v_n + 1;
        v_total := v_sink;
        v_accept[v_sink] := FALSE;
    END IF;

    v_block := array_fill(0, ARRAY[v_total]);
    v_next := array_fill(0, ARRAY[v_total]);
    v_prev := array_fill(0, ARRAY[v_total]);
    v_mark := array_fill(0, ARRAY[v_total]);
    v_hit_next := array_fill(0, ARRAY[v_total]);
    v_first := array_fill(0, ARRAY[v_total + 2]);
    v_size := array_fill(0, ARRAY[v_total + 2]);
    v_hit_first := array_fill(0, ARRAY[v_total + 2]);
    v_hit_count := array_fill(0, ARRAY[v_total + 2]);
    FOR v_q IN 1..v_total LOOP
        v_b := CASE WHEN v_accept[v_q] THEN 2 ELSE 1 END;
        v_block[v_q] := v_b;
        v_next[v_q] := v_first[v_b];
        IF v_first[v_b] != 0 THEN
            v_prev[v_first[v_b]] := v_q;
        END IF;
        v_first[v_b] := v_q;
        v_size[v_b] := v_size[v_b] + 1;
    END LOOP;

    -- Initial splitters: the smaller of F and Q\F, for every symbol.
    IF v_size[1] > 0 AND v_size[2] > 0 THEN
        v_small := CASE WHEN v_size[1] <= v_size[2] THEN 1 ELSE 2 END;
        FOR v_c IN 1..v_k LOOP
            v_work_count := v_work_count + 1;
            v_work[v_work_count] := (v_small - 1) * v_k + v_c;
        END LOOP;
    END IF;

    WHILE v_work_count > 0 LOOP
        v_slot := v_work[v_work_count];
        v_work_count := v_work_count - 1;
        v_a := (v_slot - 1) / v_k + 1;
        v_c := (v_slot - 1) % v_k + 1;
        v_stamp := v_stamp + 1;
        v_touched_count := 0;

        -- X = predecessors of splitter A on symbol c. Freeze all of X before
        -- moving any states: A itself may be among the blocks that split.
        v_q := v_first[v_a];
        WHILE v_q != 0 LOOP
            v_e := v_reverse[(v_q - 1) * v_k + v_c];
            WHILE v_e != 0 LOOP
                v_p := v_edge_from[v_e];
                IF v_mark[v_p] != v_stamp THEN
                    v_mark[v_p] := v_stamp;
                    v_b := v_block[v_p];
                    IF v_hit_count[v_b] = 0 THEN
                        v_touched_count := v_touched_count + 1;
                        v_touched[v_touched_count] := v_b;
                    END IF;
                    v_hit_next[v_p] := v_hit_first[v_b];
                    v_hit_first[v_b] := v_p;
                    v_hit_count[v_b] := v_hit_count[v_b] + 1;
                END IF;
                v_e := v_edge_next[v_e];
            END LOOP;
            v_q := v_next[v_q];
        END LOOP;

        -- Missing edges lead to the virtual sink. Only a splitter containing
        -- that sink needs this scan; no completion rows are ever inserted.
        IF v_sink != 0 AND v_block[v_sink] = v_a THEN
            FOR v_p IN 1..v_total LOOP
                IF v_dest[(v_p - 1) * v_k + v_c] = 0 AND v_mark[v_p] != v_stamp THEN
                    v_mark[v_p] := v_stamp;
                    v_b := v_block[v_p];
                    IF v_hit_count[v_b] = 0 THEN
                        v_touched_count := v_touched_count + 1;
                        v_touched[v_touched_count] := v_b;
                    END IF;
                    v_hit_next[v_p] := v_hit_first[v_b];
                    v_hit_first[v_b] := v_p;
                    v_hit_count[v_b] := v_hit_count[v_b] + 1;
                END IF;
            END LOOP;
        END IF;

        FOR v_i IN 1..v_touched_count LOOP
            v_b := v_touched[v_i];
            IF v_hit_count[v_b] < v_size[v_b] THEN
                -- New block always gets the smaller half. A queued old block
                -- stays queued (now its larger half); enqueue the new half on
                -- each symbol. If old wasn't queued, this is exactly Hopcroft's
                -- smaller-half rule. No all-block/all-symbol rescan is needed.
                v_blocks := v_blocks + 1;
                v_new_b := v_blocks;
                IF v_hit_count[v_b] <= v_size[v_b] / 2 THEN
                    v_move := v_hit_first[v_b];
                ELSE
                    v_move := 0;
                    v_q := v_first[v_b];
                    WHILE v_q != 0 LOOP
                        IF v_mark[v_q] != v_stamp THEN
                            v_hit_next[v_q] := v_move;
                            v_move := v_q;
                        END IF;
                        v_q := v_next[v_q];
                    END LOOP;
                END IF;
                WHILE v_move != 0 LOOP
                    v_q := v_move;
                    v_move := v_hit_next[v_q];
                    IF v_prev[v_q] = 0 THEN
                        v_first[v_b] := v_next[v_q];
                    ELSE
                        v_next[v_prev[v_q]] := v_next[v_q];
                    END IF;
                    IF v_next[v_q] != 0 THEN
                        v_prev[v_next[v_q]] := v_prev[v_q];
                    END IF;
                    v_prev[v_q] := 0;
                    v_next[v_q] := v_first[v_new_b];
                    IF v_first[v_new_b] != 0 THEN
                        v_prev[v_first[v_new_b]] := v_q;
                    END IF;
                    v_first[v_new_b] := v_q;
                    v_block[v_q] := v_new_b;
                    v_size[v_b] := v_size[v_b] - 1;
                    v_size[v_new_b] := v_size[v_new_b] + 1;
                END LOOP;
                FOR v_j IN 1..v_k LOOP
                    v_work_count := v_work_count + 1;
                    v_work[v_work_count] := (v_new_b - 1) * v_k + v_j;
                END LOOP;
            END IF;
            v_hit_first[v_b] := 0;
            v_hit_count[v_b] := 0;
        END LOOP;
    END LOOP;

    v_number := array_fill(0, ARRAY[v_blocks]);
    v_b := v_block[v_initial];
    v_order[1] := v_b;
    v_number[v_b] := 1;
    v_output_count := 1;
    v_i := 1;
    WHILE v_i <= v_output_count LOOP
        v_b := v_order[v_i];
        v_q := v_first[v_b];
        FOR v_c IN 1..v_k LOOP
            v_p := v_dest[(v_q - 1) * v_k + v_c];
            IF v_p = 0 THEN v_p := v_sink; END IF;
            v_a := v_block[v_p];
            IF v_number[v_a] = 0 THEN
                v_output_count := v_output_count + 1;
                v_order[v_output_count] := v_a;
                v_number[v_a] := v_output_count;
            END IF;
        END LOOP;
        v_i := v_i + 1;
    END LOOP;
    IF v_sink != 0 THEN
        v_output_sink := v_number[v_block[v_sink]] - 1;
    END IF;

    -- Partition members retain original ids. A virtual-only sink class has
    -- [] members and is identified separately by implicit_sink (an output id).
    SELECT jsonb_object_agg(output_id::TEXT, members) INTO v_partition
    FROM (
        SELECT o.n - 1 AS output_id,
               coalesce(jsonb_agg(ms.state_id ORDER BY ms.state_id)
                        FILTER (WHERE ms.idx IS NOT NULL), '[]'::JSONB) AS members
        FROM unnest(v_order) WITH ORDINALITY o(block_id, n)
        LEFT JOIN _min_states ms ON v_block[ms.idx] = o.block_id
        GROUP BY o.n
    ) classes;

    INSERT INTO nerode.automata (name, type, alphabet_id, state_count, source_regex, provenance)
    VALUES (
        coalesce(v_auto.name, 'auto') || '_min', 'DFA', v_auto.alphabet_id,
        v_output_count, v_auto.source_regex,
        jsonb_build_object('operation', 'minimize', 'algorithm', 'hopcroft',
            'input_id', p_automaton_id, 'source_regex', v_auto.source_regex,
            'original_states', v_original, 'minimal_states', v_output_count,
            'implicit_sink', v_output_sink, 'created_at', now())
    ) RETURNING id INTO v_new_id;

    INSERT INTO nerode.states (automaton_id, state_id, label, is_initial, is_accepting)
    SELECT v_new_id, (o.n - 1)::INTEGER, 'q' || (o.n - 1), o.n = 1,
           v_accept[v_first[o.block_id]]
    FROM unnest(v_order) WITH ORDINALITY o(block_id, n);

    -- Complete only the final minimal DFA, in one bulk insert. The source
    -- stays sparse and untouched, including when the caller requests a cert.
    INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
    SELECT v_new_id, (o.n - 1)::INTEGER, v_symbols[c],
           v_number[v_block[coalesce(nullif(
               v_dest[(v_first[o.block_id] - 1) * v_k + c], 0), v_sink)]] - 1
    FROM unnest(v_order) WITH ORDINALITY o(block_id, n)
    CROSS JOIN generate_series(1, v_k) c;

    INSERT INTO nerode.construction_log (automaton_id, operation, inputs, result)
    VALUES (v_new_id, 'minimize', jsonb_build_object('input_id', p_automaton_id),
        jsonb_build_object('output_id', v_new_id, 'algorithm', 'hopcroft',
            'original_states', v_original, 'minimal_states', v_output_count,
            'reachable_states', v_n, 'unreachable_states', v_original - v_n,
            'implicit_sink', v_output_sink, 'partition', v_partition));

    DROP TABLE _min_states;
    RETURN v_new_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.minimize(BIGINT) IS
    'Hopcroft worklist refinement with smaller-half splitting and an implicit sink. '
    'Prunes unreachable states without changing the input DFA; returns a complete minimal DFA '
    'with canonical BFS numbering. Original-state partition classes and the implicit sink output '
    'id are stored in construction_log.result for nerode_partition witnesses.';

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
    v_implicit_sink INTEGER;
    v_reachable INTEGER;
    v_unreachable INTEGER;
    v_seq       INTEGER;
    v_cert_id   BIGINT;
    v_wit_id    BIGINT;
    v_stmt      TEXT;
BEGIN
    v_new_id := nerode.minimize(p_automaton_id);

    -- Fetch minimization details from log
    SELECT (cl.result->>'original_states')::INTEGER,
           (cl.result->>'minimal_states')::INTEGER,
           cl.result->'partition',
           (cl.result->>'implicit_sink')::INTEGER,
           (cl.result->>'reachable_states')::INTEGER,
           (cl.result->>'unreachable_states')::INTEGER
    INTO v_orig_sc, v_min_sc, v_partition, v_implicit_sink, v_reachable, v_unreachable
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
            'partition',       v_partition,
            'implicit_sink',   v_implicit_sink,
            'reachable_states', v_reachable,
            'unreachable_states', v_unreachable
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
