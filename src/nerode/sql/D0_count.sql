-- Exact accepted-word counts over ALL alphabet symbols. In contrast,
-- run_sequence follows one chosen symbol repeatedly. Missing DFA edges reject.
-- Read-only and bounded: never materialise an exponentially large word list.
CREATE OR REPLACE FUNCTION nerode.dfa_snapshot(p_automaton BIGINT)
RETURNS JSONB LANGUAGE plpgsql STABLE AS $$
DECLARE v_snapshot JSONB;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM nerode.automata WHERE id=p_automaton AND type='DFA') THEN
        RAISE EXCEPTION 'dfa_snapshot: automaton % is not a DFA', p_automaton;
    END IF;
    IF (SELECT count(*) FROM nerode.states WHERE automaton_id=p_automaton)>256
       OR (SELECT count(*) FROM nerode.transitions WHERE automaton_id=p_automaton)>16384
       OR (SELECT cardinality(a.symbols) FROM nerode.alphabets a
            JOIN nerode.automata m ON m.alphabet_id=a.id WHERE m.id=p_automaton)>16384 THEN
        RAISE EXCEPTION 'dfa_snapshot: resource budget exceeded';
    END IF;
    SELECT jsonb_build_object(
        'alphabet', (SELECT to_jsonb(a.symbols) FROM nerode.alphabets a
                     JOIN nerode.automata m ON m.alphabet_id=a.id WHERE m.id=p_automaton),
        'states', (SELECT jsonb_agg(state_id ORDER BY state_id) FROM nerode.states
                   WHERE automaton_id=p_automaton),
        'initial', (SELECT state_id FROM nerode.states
                    WHERE automaton_id=p_automaton AND is_initial ORDER BY state_id LIMIT 1),
        'accepting', (SELECT COALESCE(jsonb_agg(state_id ORDER BY state_id),'[]'::jsonb)
                      FROM nerode.states WHERE automaton_id=p_automaton AND is_accepting),
        'transitions', (SELECT COALESCE(jsonb_agg(jsonb_build_array(from_state,symbol,to_state)
                              ORDER BY from_state,symbol,to_state),'[]'::jsonb)
                        FROM nerode.transitions WHERE automaton_id=p_automaton)
    ) INTO v_snapshot;
    IF (SELECT count(*) FROM nerode.states
        WHERE automaton_id=p_automaton AND is_initial) <> 1 THEN
        RAISE EXCEPTION 'dfa_snapshot: expected exactly one initial state';
    END IF;
    RETURN v_snapshot;
END $$;

CREATE OR REPLACE FUNCTION nerode.count_language(
    p_automaton BIGINT, p_max_length INTEGER,
    p_state_budget INTEGER DEFAULT 256, p_transition_budget INTEGER DEFAULT 16384
)
RETURNS TABLE (length INTEGER, word_count NUMERIC)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_states INTEGER[]; v_symbols TEXT[]; v_current NUMERIC[]; v_next NUMERIC[];
    v_initial INTEGER; v_size INTEGER; v_edges INTEGER; v_k INTEGER; v_i INTEGER;
    v_edge RECORD;
BEGIN
    IF p_max_length IS NULL OR p_max_length < 0 OR p_max_length > 4096
       OR p_state_budget IS NULL OR p_state_budget NOT BETWEEN 1 AND 256
       OR p_transition_budget IS NULL OR p_transition_budget NOT BETWEEN 0 AND 16384 THEN
        RAISE EXCEPTION 'count_language: invalid length or resource budget';
    END IF;
    PERFORM nerode.dfa_snapshot(p_automaton);
    SELECT array_agg(state_id ORDER BY state_id) INTO v_states
      FROM nerode.states WHERE automaton_id=p_automaton;
    v_size := cardinality(v_states);
    SELECT a.symbols INTO v_symbols FROM nerode.alphabets a
      JOIN nerode.automata m ON m.alphabet_id=a.id WHERE m.id=p_automaton;
    IF cardinality(v_symbols)>16384 THEN
        RAISE EXCEPTION 'count_language: alphabet budget exceeded';
    END IF;
    SELECT count(*) INTO v_edges FROM nerode.transitions WHERE automaton_id=p_automaton;
    IF v_size > p_state_budget OR v_edges > p_transition_budget
       OR v_edges::BIGINT*p_max_length > 8000000 THEN
        RAISE EXCEPTION 'count_language: resource budget exceeded';
    END IF;
    IF EXISTS (SELECT 1 FROM nerode.transitions WHERE automaton_id=p_automaton
               AND (symbol IS NULL OR NOT symbol=ANY(v_symbols)))
       OR cardinality(v_symbols) <> (SELECT count(DISTINCT s) FROM unnest(v_symbols) s)
       OR EXISTS (SELECT 1 FROM unnest(v_symbols) s WHERE s IS NULL OR s='')
       OR EXISTS (SELECT 1 FROM nerode.transitions WHERE automaton_id=p_automaton
                  GROUP BY from_state,symbol HAVING count(*)>1) THEN
        RAISE EXCEPTION 'count_language: malformed or nondeterministic DFA';
    END IF;
    SELECT state_id INTO v_initial FROM nerode.states
      WHERE automaton_id=p_automaton AND is_initial;
    v_current := array_fill(0::NUMERIC, ARRAY[v_size]);
    v_current[array_position(v_states,v_initial)] := 1;
    FOR v_k IN 0..p_max_length LOOP
        length := v_k;
        SELECT COALESCE(sum(v_current[array_position(v_states,state_id)]),0)
          INTO word_count FROM nerode.states
          WHERE automaton_id=p_automaton AND is_accepting;
        RETURN NEXT;
        EXIT WHEN v_k=p_max_length;
        v_next := array_fill(0::NUMERIC, ARRAY[v_size]);
        FOR v_edge IN SELECT from_state,to_state FROM nerode.transitions
                      WHERE automaton_id=p_automaton LOOP
            v_i := array_position(v_states,v_edge.to_state);
            v_next[v_i] := v_next[v_i]+v_current[array_position(v_states,v_edge.from_state)];
        END LOOP;
        v_current := v_next;
    END LOOP;
END $$;

COMMENT ON FUNCTION nerode.count_language(BIGINT,INTEGER,INTEGER,INTEGER) IS
    'Exact counts of accepted words at lengths 0..N over every alphabet symbol; '
    'deterministic snapshots only, missing transitions reject, resource limits fail explicitly.';
