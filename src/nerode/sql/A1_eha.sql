-- =============================================================================
--  nerode — Step A1: Event History Automata (EHA)  [Phase 4]
--
--  An EHA (arXiv:2606.11223, Def. 2) is a deterministic Moore machine whose
--  state output is an admissible interval ρ: Q → I(D). We store it as an
--  ordinary nerode.automata row (type='EHA') — so it inherits states /
--  transitions / export_json / complete_dfa — plus a per-state interval in the
--  side table nerode.eha_output (no change to nerode.states).
--
--  nerode.compile_eha(system) compiles a pattern-based scenario constraint
--  system (Def. 1) into an EHA, implementing Theorem 1(a): each pattern → a
--  deterministic DFA (via the existing from_regex), then the m-ary synchronous
--  product, then the resolver assigns each product state its interval.
--
--  Depends on: A0_interval.sql, 05_from_regex.sql, 01_schema.sql.
--  Idempotent. NOTE: starter file — not yet executed against a live database.
-- =============================================================================

-- Extend the automata.type CHECK to admit EHA (and WFFA, used by A2). Idempotent.
DO $$
BEGIN
    ALTER TABLE nerode.automata DROP CONSTRAINT IF EXISTS automata_type_check;
    ALTER TABLE nerode.automata
        ADD CONSTRAINT automata_type_check
        CHECK (type IN ('DFA','NFA','NFA_E','PDA','EHA','WFFA'));
END
$$;

-- ---------------------------------------------------------------------------
-- nerode.eha_output — Moore output ρ: per-state admissible interval
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nerode.eha_output (
    automaton_id BIGINT          NOT NULL REFERENCES nerode.automata(id) ON DELETE CASCADE,
    state_id     INTEGER         NOT NULL,
    interval     nerode.interval NOT NULL,
    PRIMARY KEY (automaton_id, state_id),
    FOREIGN KEY (automaton_id, state_id)
        REFERENCES nerode.states(automaton_id, state_id) ON DELETE CASCADE
);

COMMENT ON TABLE nerode.eha_output IS
    'Moore output of an EHA: admissible interval ρ(q) for each state (arXiv:2606.11223).';

-- ---------------------------------------------------------------------------
-- Internal: expand the Σ shorthand in a pattern to an explicit union over the
-- alphabet, so callers can write the paper''s Σ* patterns directly.
--   'Σ' (or ASCII alias '#')  →  '(s1|s2|...)'
-- The rest of the pattern uses nerode.from_regex syntax (| * + ? (), implicit
-- concatenation — do NOT include '.' or '·').
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode._eha_expand_sigma(p_pattern TEXT, p_symbols TEXT[])
RETURNS TEXT LANGUAGE sql IMMUTABLE AS
$$
    SELECT replace(
             replace(p_pattern, 'Σ', '(' || array_to_string(p_symbols, '|') || ')'),
             '#',                '(' || array_to_string(p_symbols, '|') || ')'
           );
$$;

-- ---------------------------------------------------------------------------
-- nerode.compile_eha(p_system JSONB) → BIGINT  (new EHA automaton id)
--
-- p_system = {
--   "name":     "scenario_S1",                 -- optional
--   "symbols":  ["f","u","d"],                 -- the alphabet Σ (required)
--   "resolver": "priority",                    -- "priority" (default) | "intersection"
--   "default":  {"lo":99,"hi":101},            -- J0 used when no constraint active
--   "constraints": [                            -- priority order: earliest wins
--       {"pattern":"Σ*uuΣ*", "interval":{"lo":180,"hi":220}},
--       {"pattern":"Σ*ddΣ*", "interval":{"lo":40, "hi":90}}
--   ]
-- }
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.compile_eha(p_system JSONB)
RETURNS BIGINT AS $$
DECLARE
    v_symbols   TEXT[];
    v_resolver  TEXT;
    v_default   nerode.interval;
    v_cons      JSONB;
    v_m         INTEGER;
    v_comp      BIGINT[];          -- component DFA ids, 1..m
    v_comp_iv   nerode.interval[]; -- component intervals, 1..m
    v_alph_id   BIGINT;
    v_new_id    BIGINT;

    v_k         INTEGER;
    v_pat       TEXT;

    -- m-ary product BFS
    v_init      INTEGER[];         -- initial component-state tuple
    v_dict      JSONB := '{}';     -- tuple-text → new state_id
    v_queue     TEXT[];            -- tuples (comma-joined) to process
    v_next_id   INTEGER := 0;
    v_cur_txt   TEXT;
    v_cur       INTEGER[];
    v_cur_id    INTEGER;
    v_sym       TEXT;
    v_succ      INTEGER[];
    v_succ_txt  TEXT;
    v_succ_id   INTEGER;
    v_active    INTEGER[];
    v_iv        nerode.interval;
    v_min_k     INTEGER;
    v_acc       BOOLEAN;
    v_state_count INTEGER;
BEGIN
    -- ---- parse system ----------------------------------------------------
    SELECT array_agg(value::text ORDER BY ord)
      INTO v_symbols
    FROM jsonb_array_elements_text(p_system->'symbols') WITH ORDINALITY AS t(value, ord);
    IF v_symbols IS NULL OR array_length(v_symbols,1) = 0 THEN
        RAISE EXCEPTION 'compile_eha: "symbols" (alphabet Σ) is required';
    END IF;

    v_resolver := COALESCE(p_system->>'resolver', 'priority');
    IF v_resolver NOT IN ('priority','intersection') THEN
        RAISE EXCEPTION 'compile_eha: resolver must be priority or intersection, got %', v_resolver;
    END IF;
    v_default := nerode.iv_from_json(p_system->'default');

    v_cons := COALESCE(p_system->'constraints', '[]'::jsonb);
    v_m    := jsonb_array_length(v_cons);
    IF v_m = 0 THEN
        RAISE EXCEPTION 'compile_eha: at least one constraint is required';
    END IF;

    -- ---- build the m component DFAs over the shared alphabet --------------
    -- Passing identical p_symbols to from_regex yields a shared alphabet row
    -- (alphabet name = 'auto_'||md5(symbols)), so product states share δ.
    v_comp    := ARRAY[]::BIGINT[];
    v_comp_iv := ARRAY[]::nerode.interval[];
    FOR v_k IN 0 .. v_m-1 LOOP
        v_pat := nerode._eha_expand_sigma(v_cons->v_k->>'pattern', v_symbols);
        v_comp := v_comp || nerode.from_regex(
                                v_pat,
                                format('__eha_comp_%s__', v_k),
                                v_symbols);
        PERFORM nerode.complete_dfa(v_comp[v_k+1]);
        v_comp_iv := v_comp_iv || nerode.iv_from_json(v_cons->v_k->'interval');
    END LOOP;

    SELECT alphabet_id INTO v_alph_id FROM nerode.automata WHERE id = v_comp[1];

    -- ---- create the result EHA -------------------------------------------
    INSERT INTO nerode.automata (name, type, alphabet_id, state_count, provenance)
    VALUES (
        COALESCE(p_system->>'name', 'eha'),
        'EHA', v_alph_id, 0,
        jsonb_build_object('operation','compile_eha','system',p_system,'created_at',now())
    )
    RETURNING id INTO v_new_id;

    -- ---- initial tuple ----------------------------------------------------
    v_init := ARRAY[]::INTEGER[];
    FOR v_k IN 1 .. v_m LOOP
        v_init := v_init || (SELECT state_id FROM nerode.states
                             WHERE automaton_id = v_comp[v_k] AND is_initial LIMIT 1);
    END LOOP;

    v_queue := ARRAY[ array_to_string(v_init, ',') ];

    -- ---- BFS over the m-ary product --------------------------------------
    WHILE array_length(v_queue,1) IS NOT NULL AND array_length(v_queue,1) > 0 LOOP
        v_cur_txt := v_queue[1];
        v_queue   := v_queue[2:];

        -- assign / fetch id for the current tuple
        IF v_dict ? v_cur_txt THEN
            CONTINUE;  -- already materialised (id assigned + state inserted below on first sight)
        END IF;
        v_cur    := string_to_array(v_cur_txt, ',')::INTEGER[];
        v_cur_id := v_next_id;
        v_next_id := v_next_id + 1;
        v_dict := jsonb_set(v_dict, ARRAY[v_cur_txt], to_jsonb(v_cur_id));

        -- active set + resolver → interval
        v_active := ARRAY(
            SELECT v_k FROM generate_series(1, v_m) AS v_k
            WHERE (SELECT is_accepting FROM nerode.states
                   WHERE automaton_id = v_comp[v_k] AND state_id = v_cur[v_k]) );

        IF array_length(v_active,1) IS NULL THEN
            v_iv := CASE WHEN v_resolver = 'intersection' THEN nerode.iv_full() ELSE v_default END;
        ELSIF v_resolver = 'priority' THEN
            v_min_k := (SELECT min(x) FROM unnest(v_active) AS x);
            v_iv    := v_comp_iv[v_min_k];
        ELSE  -- intersection
            v_iv := nerode.iv_full();
            FOR v_k IN 1 .. v_m LOOP
                IF v_k = ANY(v_active) THEN v_iv := nerode.iv_meet(v_iv, v_comp_iv[v_k]); END IF;
            END LOOP;
        END IF;

        INSERT INTO nerode.states (automaton_id, state_id, label, is_initial, is_accepting)
        VALUES (v_new_id, v_cur_id, '(' || v_cur_txt || ')', v_cur_id = 0, FALSE);
        INSERT INTO nerode.eha_output (automaton_id, state_id, interval)
        VALUES (v_new_id, v_cur_id, v_iv);

        -- successors
        FOREACH v_sym IN ARRAY v_symbols LOOP
            v_succ := ARRAY[]::INTEGER[];
            FOR v_k IN 1 .. v_m LOOP
                v_succ := v_succ || (SELECT to_state FROM nerode.transitions
                                     WHERE automaton_id = v_comp[v_k]
                                       AND from_state = v_cur[v_k]
                                       AND symbol = v_sym LIMIT 1);
            END LOOP;
            v_succ_txt := array_to_string(v_succ, ',');
            IF NOT (v_dict ? v_succ_txt) AND NOT (v_succ_txt = ANY(v_queue)) THEN
                v_queue := v_queue || v_succ_txt;
            END IF;
        END LOOP;
    END LOOP;

    -- ---- second pass: transitions (now every tuple has an id) ------------
    -- Re-walk reachable tuples deterministically from the dict.
    FOR v_cur_txt, v_cur_id IN
        SELECT key, value::text::integer FROM jsonb_each(v_dict)
    LOOP
        v_cur := string_to_array(v_cur_txt, ',')::INTEGER[];
        FOREACH v_sym IN ARRAY v_symbols LOOP
            v_succ := ARRAY[]::INTEGER[];
            FOR v_k IN 1 .. v_m LOOP
                v_succ := v_succ || (SELECT to_state FROM nerode.transitions
                                     WHERE automaton_id = v_comp[v_k]
                                       AND from_state = v_cur[v_k]
                                       AND symbol = v_sym LIMIT 1);
            END LOOP;
            v_succ_id := (v_dict ->> array_to_string(v_succ, ','))::INTEGER;
            IF v_succ_id IS NOT NULL THEN
                INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
                VALUES (v_new_id, v_cur_id, v_sym, v_succ_id)
                ON CONFLICT DO NOTHING;
            END IF;
        END LOOP;
    END LOOP;

    -- ---- finalise ---------------------------------------------------------
    SELECT count(*) INTO v_state_count FROM nerode.states WHERE automaton_id = v_new_id;
    UPDATE nerode.automata SET state_count = v_state_count WHERE id = v_new_id;

    INSERT INTO nerode.construction_log (automaton_id, operation, inputs, result)
    VALUES (v_new_id, 'compile_eha',
            jsonb_build_object('symbols', to_jsonb(v_symbols), 'm', v_m, 'resolver', v_resolver),
            jsonb_build_object('output_id', v_new_id, 'states', v_state_count));

    -- drop intermediate component DFAs (states/transitions cascade)
    DELETE FROM nerode.automata WHERE id = ANY(v_comp);

    RETURN v_new_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.compile_eha(JSONB) IS
    'Compile a pattern-based scenario constraint system (arXiv:2606.11223 Def. 1) '
    'into an EHA (Theorem 1a): per-pattern DFA via from_regex, m-ary synchronous '
    'product, resolver-assigned interval per state. Returns the new EHA id.';

-- ---------------------------------------------------------------------------
-- nerode.eha_interval(p_automaton_id, p_history) → nerode.interval
-- Run the unique path for an event history (symbols concatenated, e.g. 'udf')
-- and return ρ(last state) — the EHA membership/behaviour query [[H]](u).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.eha_interval(p_automaton_id BIGINT, p_history TEXT)
RETURNS nerode.interval AS $$
DECLARE
    v_state INTEGER;
    v_i     INTEGER;
    v_sym   TEXT;
    v_iv    nerode.interval;
BEGIN
    SELECT state_id INTO v_state FROM nerode.states
    WHERE automaton_id = p_automaton_id AND is_initial LIMIT 1;
    IF v_state IS NULL THEN
        RAISE EXCEPTION 'eha_interval: automaton % has no initial state', p_automaton_id;
    END IF;

    FOR v_i IN 1 .. length(COALESCE(p_history,'')) LOOP
        v_sym := substring(p_history, v_i, 1);
        SELECT to_state INTO v_state FROM nerode.transitions
        WHERE automaton_id = p_automaton_id AND from_state = v_state AND symbol = v_sym LIMIT 1;
        IF v_state IS NULL THEN
            RAISE EXCEPTION 'eha_interval: no transition on symbol % at step %', v_sym, v_i;
        END IF;
    END LOOP;

    SELECT interval INTO v_iv FROM nerode.eha_output
    WHERE automaton_id = p_automaton_id AND state_id = v_state;
    RETURN v_iv;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION nerode.eha_interval(BIGINT, TEXT) IS
    'Behaviour query [[H]](u): run the event history u and return the admissible '
    'interval ρ(last_H(u)). History is the symbols concatenated, e.g. ''udf''.';
