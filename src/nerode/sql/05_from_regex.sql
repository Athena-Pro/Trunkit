-- =============================================================================
--  nerode — Step 05: nerode.from_regex()
--
--  Full pipeline:
--    1. Parse regex → extract alphabet
--    2. Shunting-yard: infix (with explicit concatenation '.') → postfix
--    3. Thompson NFA construction (in PL/pgSQL, result stored in nerode tables)
--    4. ε-closure / subset construction  → DFA
--    5. Hopcroft minimization
--    6. Clean up intermediate NFA_E
--    7. Return minimal DFA id
--
--  Supported syntax:
--    literals a–z A–Z 0–9 _ (extend by adding to SYMBOL_CHARS)
--    |  union
--    *  Kleene star (postfix)
--    +  one-or-more (postfix)
--    ?  optional (postfix)
--    () grouping
--
--  Concatenation is inserted explicitly as '.' during preprocessing.
--  Operator precedence (low → high): | < . < * + ?
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Internal: preprocess — insert explicit '.' between adjacent operands
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode._insert_concat(p_regex TEXT)
RETURNS TEXT AS $$
DECLARE
    v_result TEXT := '';
    v_prev   CHAR;
    v_cur    CHAR;
    v_i      INTEGER;
BEGIN
    FOR v_i IN 1..length(p_regex) LOOP
        v_cur := substring(p_regex, v_i, 1);
        IF v_i > 1 THEN
            -- Insert '.' when:
            --   prev is: operand char, or * + ? or ) — AND
            --   cur  is: operand char or (
            IF v_prev ~ '[a-zA-Z0-9_]' OR v_prev IN ('*', '+', '?', ')')
            THEN
                IF v_cur ~ '[a-zA-Z0-9_]' OR v_cur = '(' THEN
                    v_result := v_result || '.';
                END IF;
            END IF;
        END IF;
        v_result := v_result || v_cur;
        v_prev := v_cur;
    END LOOP;
    RETURN v_result;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- ---------------------------------------------------------------------------
-- Internal: shunting-yard — convert infix regex to postfix (RPN)
-- Operators: | (precedence 1), . (2), * + ? (3, right-assoc unary postfix)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode._to_postfix(p_regex TEXT)
RETURNS TEXT AS $$
DECLARE
    v_output  TEXT := '';
    v_ops     TEXT[] := ARRAY[]::TEXT[];   -- operator stack
    v_c       CHAR;
    v_i       INTEGER;
    v_top     CHAR;
    v_prec    INTEGER;
    v_tprec   INTEGER;

    -- Operator precedence
    v_prec_of JSONB := '{"*":3,"+":3,"?":3,".":2,"|":1}'::JSONB;
BEGIN
    FOR v_i IN 1..length(p_regex) LOOP
        v_c := substring(p_regex, v_i, 1);

        IF v_c ~ '[a-zA-Z0-9_]' THEN
            -- Operand: push directly to output
            v_output := v_output || v_c;

        ELSIF v_c IN ('*', '+', '?') THEN
            -- Postfix unary operators: flush same-precedence items, push
            v_prec := (v_prec_of->>v_c)::INTEGER;
            LOOP
                IF array_length(v_ops, 1) IS NULL THEN EXIT; END IF;
                v_top   := v_ops[array_length(v_ops, 1)];
                IF v_top = '(' THEN EXIT; END IF;
                v_tprec := COALESCE((v_prec_of->>v_top)::INTEGER, 0);
                EXIT WHEN v_tprec < v_prec;
                v_output := v_output || v_top;
                v_ops    := v_ops[1:array_length(v_ops,1)-1];
            END LOOP;
            v_ops := v_ops || v_c;

        ELSIF v_c IN ('.', '|') THEN
            -- Binary operators (left-associative)
            v_prec := (v_prec_of->>v_c)::INTEGER;
            LOOP
                IF array_length(v_ops, 1) IS NULL THEN EXIT; END IF;
                v_top   := v_ops[array_length(v_ops, 1)];
                IF v_top = '(' THEN EXIT; END IF;
                v_tprec := COALESCE((v_prec_of->>v_top)::INTEGER, 0);
                EXIT WHEN v_tprec < v_prec;
                v_output := v_output || v_top;
                v_ops    := v_ops[1:array_length(v_ops,1)-1];
            END LOOP;
            v_ops := v_ops || v_c;

        ELSIF v_c = '(' THEN
            v_ops := v_ops || v_c;

        ELSIF v_c = ')' THEN
            LOOP
                IF array_length(v_ops, 1) IS NULL THEN
                    RAISE EXCEPTION 'nerode._to_postfix: mismatched parentheses';
                END IF;
                v_top := v_ops[array_length(v_ops, 1)];
                v_ops := v_ops[1:array_length(v_ops,1)-1];
                EXIT WHEN v_top = '(';
                v_output := v_output || v_top;
            END LOOP;
        ELSE
            RAISE EXCEPTION 'nerode._to_postfix: unsupported character %', v_c;
        END IF;
    END LOOP;

    -- Drain operator stack LIFO (top-to-bottom)
    FOR v_i IN REVERSE coalesce(array_length(v_ops, 1), 0)..1 LOOP
        v_top := v_ops[v_i];
        IF v_top = '(' THEN
            RAISE EXCEPTION 'nerode._to_postfix: mismatched parentheses';
        END IF;
        v_output := v_output || v_top;
    END LOOP;

    RETURN v_output;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- ---------------------------------------------------------------------------
-- Internal: Thompson NFA construction from postfix expression.
-- Writes states + transitions directly into nerode tables.
-- Returns the automaton id (type NFA_E).
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode._thompson(
    p_postfix    TEXT,
    p_alphabet_id BIGINT
)
RETURNS BIGINT AS $$
DECLARE
    v_auto_id BIGINT;
    v_sc      INTEGER := 0;   -- running state counter

    -- A fragment: (start, accept) both are state_ids already inserted
    v_stack   JSONB[] := ARRAY[]::JSONB[];   -- stack of {s: int, a: int}
    v_c       CHAR;
    v_i       INTEGER;

    v_f1      JSONB;
    v_f2      JSONB;
    v_s       INTEGER;
    v_a       INTEGER;
    v_s1      INTEGER;
    v_a1      INTEGER;
    v_s2      INTEGER;
    v_a2      INTEGER;
    v_new_s   INTEGER;
    v_new_a   INTEGER;
BEGIN
    -- Create skeleton NFA_E automaton
    INSERT INTO nerode.automata (name, type, alphabet_id, state_count, provenance)
    VALUES ('__thompson_temp__', 'NFA_E', p_alphabet_id, 0,
            jsonb_build_object('operation', 'thompson', 'postfix', p_postfix))
    RETURNING id INTO v_auto_id;

    -- Helper: allocate a new state
    -- We use a nested function pattern via inline counter v_sc

    FOR v_i IN 1..length(p_postfix) LOOP
        v_c := substring(p_postfix, v_i, 1);

        IF v_c ~ '[a-zA-Z0-9_]' THEN
            -- Literal: two states, one transition on v_c
            v_s := v_sc;  v_sc := v_sc + 1;
            v_a := v_sc;  v_sc := v_sc + 1;

            INSERT INTO nerode.states(automaton_id,state_id,is_initial,is_accepting)
            VALUES (v_auto_id, v_s, FALSE, FALSE),
                   (v_auto_id, v_a, FALSE, FALSE);

            INSERT INTO nerode.transitions(automaton_id, from_state, symbol, to_state)
            VALUES (v_auto_id, v_s, v_c, v_a);

            v_stack := v_stack || jsonb_build_object('s', v_s, 'a', v_a);

        ELSIF v_c = '.' THEN
            -- Concatenation: pop two fragments, ε-glue accept of F1 → start of F2
            IF array_length(v_stack, 1) < 2 THEN
                RAISE EXCEPTION 'nerode._thompson: stack underflow on .';
            END IF;
            v_f2 := v_stack[array_length(v_stack, 1)];
            v_f1 := v_stack[array_length(v_stack, 1)-1];
            v_stack := v_stack[1:array_length(v_stack,1)-2];

            v_s1 := (v_f1->>'s')::INTEGER;  v_a1 := (v_f1->>'a')::INTEGER;
            v_s2 := (v_f2->>'s')::INTEGER;  v_a2 := (v_f2->>'a')::INTEGER;

            -- ε from a1 → s2
            INSERT INTO nerode.transitions(automaton_id, from_state, symbol, to_state)
            VALUES (v_auto_id, v_a1, NULL, v_s2)
            ON CONFLICT DO NOTHING;

            v_stack := v_stack || jsonb_build_object('s', v_s1, 'a', v_a2);

        ELSIF v_c = '|' THEN
            -- Union: new start → ε→ both, both accepts → ε→ new accept
            IF array_length(v_stack, 1) < 2 THEN
                RAISE EXCEPTION 'nerode._thompson: stack underflow on |';
            END IF;
            v_f2 := v_stack[array_length(v_stack, 1)];
            v_f1 := v_stack[array_length(v_stack, 1)-1];
            v_stack := v_stack[1:array_length(v_stack,1)-2];

            v_s1 := (v_f1->>'s')::INTEGER;  v_a1 := (v_f1->>'a')::INTEGER;
            v_s2 := (v_f2->>'s')::INTEGER;  v_a2 := (v_f2->>'a')::INTEGER;

            v_new_s := v_sc;  v_sc := v_sc + 1;
            v_new_a := v_sc;  v_sc := v_sc + 1;

            INSERT INTO nerode.states(automaton_id,state_id,is_initial,is_accepting)
            VALUES (v_auto_id, v_new_s, FALSE, FALSE),
                   (v_auto_id, v_new_a, FALSE, FALSE);

            INSERT INTO nerode.transitions(automaton_id, from_state, symbol, to_state)
            VALUES
                (v_auto_id, v_new_s, NULL, v_s1),
                (v_auto_id, v_new_s, NULL, v_s2),
                (v_auto_id, v_a1,    NULL, v_new_a),
                (v_auto_id, v_a2,    NULL, v_new_a)
            ON CONFLICT DO NOTHING;

            v_stack := v_stack || jsonb_build_object('s', v_new_s, 'a', v_new_a);

        ELSIF v_c = '*' THEN
            -- Kleene star
            IF array_length(v_stack, 1) < 1 THEN
                RAISE EXCEPTION 'nerode._thompson: stack underflow on *';
            END IF;
            v_f1 := v_stack[array_length(v_stack, 1)];
            v_stack := v_stack[1:array_length(v_stack,1)-1];

            v_s1 := (v_f1->>'s')::INTEGER;  v_a1 := (v_f1->>'a')::INTEGER;

            v_new_s := v_sc;  v_sc := v_sc + 1;
            v_new_a := v_sc;  v_sc := v_sc + 1;

            INSERT INTO nerode.states(automaton_id,state_id,is_initial,is_accepting)
            VALUES (v_auto_id, v_new_s, FALSE, FALSE),
                   (v_auto_id, v_new_a, FALSE, FALSE);

            INSERT INTO nerode.transitions(automaton_id, from_state, symbol, to_state)
            VALUES
                (v_auto_id, v_new_s, NULL, v_s1),
                (v_auto_id, v_new_s, NULL, v_new_a),
                (v_auto_id, v_a1,    NULL, v_s1),
                (v_auto_id, v_a1,    NULL, v_new_a)
            ON CONFLICT DO NOTHING;

            v_stack := v_stack || jsonb_build_object('s', v_new_s, 'a', v_new_a);

        ELSIF v_c = '+' THEN
            -- One-or-more: F then F*
            IF array_length(v_stack, 1) < 1 THEN
                RAISE EXCEPTION 'nerode._thompson: stack underflow on +';
            END IF;
            v_f1 := v_stack[array_length(v_stack, 1)];
            v_stack := v_stack[1:array_length(v_stack,1)-1];

            v_s1 := (v_f1->>'s')::INTEGER;  v_a1 := (v_f1->>'a')::INTEGER;

            v_new_s := v_sc;  v_sc := v_sc + 1;
            v_new_a := v_sc;  v_sc := v_sc + 1;

            INSERT INTO nerode.states(automaton_id,state_id,is_initial,is_accepting)
            VALUES (v_auto_id, v_new_s, FALSE, FALSE),
                   (v_auto_id, v_new_a, FALSE, FALSE);

            -- new_s →ε→ s1, a1 →ε→ new_a, a1 →ε→ s1 (loop back)
            INSERT INTO nerode.transitions(automaton_id, from_state, symbol, to_state)
            VALUES
                (v_auto_id, v_new_s, NULL, v_s1),
                (v_auto_id, v_a1,    NULL, v_new_a),
                (v_auto_id, v_a1,    NULL, v_s1)
            ON CONFLICT DO NOTHING;

            v_stack := v_stack || jsonb_build_object('s', v_new_s, 'a', v_new_a);

        ELSIF v_c = '?' THEN
            -- Optional: F | ε
            IF array_length(v_stack, 1) < 1 THEN
                RAISE EXCEPTION 'nerode._thompson: stack underflow on ?';
            END IF;
            v_f1 := v_stack[array_length(v_stack, 1)];
            v_stack := v_stack[1:array_length(v_stack,1)-1];

            v_s1 := (v_f1->>'s')::INTEGER;  v_a1 := (v_f1->>'a')::INTEGER;

            v_new_s := v_sc;  v_sc := v_sc + 1;
            v_new_a := v_sc;  v_sc := v_sc + 1;

            INSERT INTO nerode.states(automaton_id,state_id,is_initial,is_accepting)
            VALUES (v_auto_id, v_new_s, FALSE, FALSE),
                   (v_auto_id, v_new_a, FALSE, FALSE);

            INSERT INTO nerode.transitions(automaton_id, from_state, symbol, to_state)
            VALUES
                (v_auto_id, v_new_s, NULL, v_s1),
                (v_auto_id, v_new_s, NULL, v_new_a),
                (v_auto_id, v_a1,    NULL, v_new_a)
            ON CONFLICT DO NOTHING;

            v_stack := v_stack || jsonb_build_object('s', v_new_s, 'a', v_new_a);

        ELSE
            RAISE EXCEPTION 'nerode._thompson: unknown postfix token %', v_c;
        END IF;
    END LOOP;

    IF array_length(v_stack, 1) != 1 THEN
        RAISE EXCEPTION 'nerode._thompson: malformed postfix expression (stack size %)',
            array_length(v_stack, 1);
    END IF;

    -- Mark initial and accepting
    v_f1 := v_stack[1];
    v_s  := (v_f1->>'s')::INTEGER;
    v_a  := (v_f1->>'a')::INTEGER;

    UPDATE nerode.states SET is_initial  = TRUE WHERE automaton_id = v_auto_id AND state_id = v_s;
    UPDATE nerode.states SET is_accepting = TRUE WHERE automaton_id = v_auto_id AND state_id = v_a;

    UPDATE nerode.automata SET state_count = v_sc WHERE id = v_auto_id;

    RETURN v_auto_id;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- Internal: ε-closure of a set of NFA states (iterative via temp table).
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode._epsilon_closure(
    p_auto_id  BIGINT,
    p_states   INTEGER[]
)
RETURNS INTEGER[] AS $$
DECLARE
    v_result   INTEGER[];
    v_frontier INTEGER[];
    v_new      INTEGER[];
BEGIN
    v_result   := p_states;
    v_frontier := p_states;

    LOOP
        SELECT array_agg(DISTINCT t.to_state) INTO v_new
        FROM nerode.transitions t
        WHERE t.automaton_id = p_auto_id
          AND t.symbol IS NULL
          AND t.from_state = ANY(v_frontier)
          AND NOT (t.to_state = ANY(v_result));

        EXIT WHEN v_new IS NULL OR array_length(v_new, 1) = 0;

        v_result   := v_result || v_new;
        v_frontier := v_new;
    END LOOP;

    RETURN v_result;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- Internal: subset construction (NFA_E → DFA).
-- p_nfa_id: the NFA_E automaton id built by _thompson.
-- Returns new DFA automaton id.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode._subset_construction(
    p_nfa_id      BIGINT,
    p_alphabet_id BIGINT
)
RETURNS BIGINT AS $$
DECLARE
    v_symbols  TEXT[];
    v_dfa_id   BIGINT;

    -- Each DFA state = sorted integer array of NFA states; encoded as TEXT for maps
    v_init_nfa  INTEGER[];
    v_init_key  TEXT;
    v_state_map JSONB := '{}'::JSONB;   -- key TEXT → dfa_state_id INTEGER
    v_todo      TEXT[];
    v_cur_key   TEXT;
    v_cur_nfa   INTEGER[];
    v_cur_dfa   INTEGER;
    v_dfa_sc    INTEGER := 0;

    v_sym       TEXT;
    v_moved     INTEGER[];
    v_next_nfa  INTEGER[];
    v_next_key  TEXT;
    v_next_dfa  INTEGER;

    v_acc_states INTEGER[];
    v_is_acc     BOOLEAN;
    v_state_count INTEGER;
BEGIN
    SELECT symbols INTO v_symbols
    FROM nerode.alphabets WHERE id = p_alphabet_id;

    -- Accepting NFA states
    SELECT array_agg(state_id) INTO v_acc_states
    FROM nerode.states WHERE automaton_id = p_nfa_id AND is_accepting = TRUE;

    -- Initial DFA state = ε-closure({q0})
    SELECT array_agg(state_id) INTO v_init_nfa
    FROM nerode.states WHERE automaton_id = p_nfa_id AND is_initial = TRUE;

    v_init_nfa := nerode._epsilon_closure(p_nfa_id, v_init_nfa);
    v_init_nfa := ARRAY(SELECT DISTINCT unnest(v_init_nfa) ORDER BY 1);
    v_init_key := array_to_string(v_init_nfa, ',');

    INSERT INTO nerode.automata (name, type, alphabet_id, state_count, provenance)
    VALUES ('__subset_temp__', 'DFA', p_alphabet_id, 0,
            jsonb_build_object('operation', 'subset_construction', 'nfa_id', p_nfa_id))
    RETURNING id INTO v_dfa_id;

    -- Create initial DFA state
    v_is_acc := EXISTS (
        SELECT 1 FROM unnest(v_init_nfa) AS s
        WHERE s = ANY(v_acc_states)
    );

    INSERT INTO nerode.states (automaton_id, state_id, label, is_initial, is_accepting)
    VALUES (v_dfa_id, v_dfa_sc, v_init_key, TRUE, v_is_acc);

    v_state_map := jsonb_set(v_state_map, ARRAY[v_init_key], to_jsonb(v_dfa_sc));
    v_dfa_sc    := v_dfa_sc + 1;

    v_todo := ARRAY[v_init_key];

    WHILE array_length(v_todo, 1) IS NOT NULL AND array_length(v_todo, 1) > 0 LOOP
        v_cur_key  := v_todo[1];
        v_todo     := v_todo[2:];
        v_cur_dfa  := (v_state_map->>v_cur_key)::INTEGER;

        -- Parse NFA set from key
        SELECT array_agg(s::INTEGER ORDER BY s) INTO v_cur_nfa
        FROM unnest(string_to_array(v_cur_key, ',')) AS s;

        FOREACH v_sym IN ARRAY v_symbols LOOP
            -- Move: NFA states reachable from v_cur_nfa on v_sym
            SELECT array_agg(DISTINCT t.to_state ORDER BY t.to_state) INTO v_moved
            FROM nerode.transitions t
            WHERE t.automaton_id = p_nfa_id
              AND t.symbol = v_sym
              AND t.from_state = ANY(v_cur_nfa);

            IF v_moved IS NULL OR array_length(v_moved, 1) = 0 THEN
                CONTINUE;  -- dead transition; handled by complete_dfa later
            END IF;

            v_next_nfa := nerode._epsilon_closure(p_nfa_id, v_moved);
            v_next_nfa := ARRAY(SELECT DISTINCT unnest(v_next_nfa) ORDER BY 1);
            v_next_key := array_to_string(v_next_nfa, ',');

            IF NOT (v_state_map ? v_next_key) THEN
                v_is_acc := EXISTS (
                    SELECT 1 FROM unnest(v_next_nfa) AS s
                    WHERE s = ANY(v_acc_states)
                );

                INSERT INTO nerode.states (automaton_id, state_id, label, is_initial, is_accepting)
                VALUES (v_dfa_id, v_dfa_sc, v_next_key, FALSE, v_is_acc);

                v_state_map := jsonb_set(v_state_map, ARRAY[v_next_key], to_jsonb(v_dfa_sc));
                v_next_dfa  := v_dfa_sc;
                v_dfa_sc    := v_dfa_sc + 1;

                v_todo := v_todo || v_next_key;
            ELSE
                v_next_dfa := (v_state_map->>v_next_key)::INTEGER;
            END IF;

            INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
            VALUES (v_dfa_id, v_cur_dfa, v_sym, v_next_dfa)
            ON CONFLICT DO NOTHING;
        END LOOP;
    END LOOP;

    SELECT count(*) INTO v_state_count FROM nerode.states WHERE automaton_id = v_dfa_id;
    UPDATE nerode.automata SET state_count = v_state_count WHERE id = v_dfa_id;

    RETURN v_dfa_id;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- Public: nerode.from_regex(p_pattern, p_name)
-- Full pipeline: regex → Thompson NFA → subset DFA → Hopcroft minimal DFA
-- Returns the id of the stored, minimized DFA.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.from_regex(
    p_pattern TEXT,
    p_name    TEXT    DEFAULT NULL,
    p_symbols TEXT[]  DEFAULT NULL
)
RETURNS BIGINT AS $$
DECLARE
    v_prepped     TEXT;
    v_postfix     TEXT;
    v_symbols     TEXT[];
    v_sym         TEXT;
    v_alph_id     BIGINT;
    v_alph_name   TEXT;
    v_nfa_id      BIGINT;
    v_dfa_id      BIGINT;
    v_min_id      BIGINT;
BEGIN
    -- Alphabet: use explicit symbols when provided, else derive from pattern
    IF p_symbols IS NOT NULL THEN
        v_symbols := p_symbols;
    ELSE
        SELECT array_agg(DISTINCT c ORDER BY c) INTO v_symbols
        FROM (
            SELECT regexp_split_to_table(p_pattern, '') AS c
        ) sub
        WHERE c ~ '[a-zA-Z0-9_]';
    END IF;

    IF v_symbols IS NULL OR array_length(v_symbols, 1) = 0 THEN
        RAISE EXCEPTION 'nerode.from_regex: no alphabet symbols found in pattern %', p_pattern;
    END IF;

    -- Find or create alphabet
    v_alph_name := 'auto_' || md5(array_to_string(v_symbols, ''));

    INSERT INTO nerode.alphabets (name, symbols)
    VALUES (v_alph_name, v_symbols)
    ON CONFLICT (name) DO NOTHING;

    SELECT id INTO v_alph_id FROM nerode.alphabets WHERE name = v_alph_name;

    -- Preprocess: insert explicit concatenation operators
    v_prepped := nerode._insert_concat(p_pattern);

    -- Convert to postfix
    v_postfix := nerode._to_postfix(v_prepped);

    -- Thompson NFA construction
    v_nfa_id := nerode._thompson(v_postfix, v_alph_id);

    -- Subset construction → DFA
    v_dfa_id := nerode._subset_construction(v_nfa_id, v_alph_id);

    -- Minimize
    v_min_id := nerode.minimize(v_dfa_id);

    -- Update source_regex and name on final DFA
    UPDATE nerode.automata
    SET source_regex = p_pattern,
        name         = COALESCE(p_name, 'regex_' || left(p_pattern, 40)),
        provenance   = provenance || jsonb_build_object(
            'source_regex',     p_pattern,
            'postfix',          v_postfix,
            'nfa_id',           v_nfa_id,
            'intermediate_dfa', v_dfa_id
        )
    WHERE id = v_min_id;

    -- Clean up intermediate automata (NFA_E and unminimized DFA)
    DELETE FROM nerode.automata WHERE id IN (v_nfa_id, v_dfa_id);

    -- Log
    INSERT INTO nerode.construction_log (automaton_id, operation, inputs, result)
    VALUES (
        v_min_id, 'from_regex',
        jsonb_build_object('pattern', p_pattern, 'name', p_name),
        jsonb_build_object('output_id', v_min_id)
    );

    RETURN v_min_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.from_regex(TEXT, TEXT, TEXT[]) IS
    'Full regex → minimal DFA pipeline. '
    'Supports: literals [a-zA-Z0-9_], | * + ? (). '
    'p_symbols: explicit alphabet (derived from pattern if NULL). '
    'Pipeline: Thompson NFA → subset construction → Hopcroft minimization. '
    'Returns the stored minimal DFA id.';
