-- =============================================================================
-- 96_dead_time_factory.sql
-- nerode.ensure_dead_time(k) — build a dead_time DFA for arbitrary k.
--
-- Usage:
--   SELECT nerode.ensure_dead_time(7);   -- returns automaton_id
--
-- The returned DFA is identical in structure to the hardcoded dead_time_5/10/20
-- DFAs in 95_cybernetic_automata.sql: k+2 states, control alphabet, pattern
-- A_{k,}.  Once created it is picked up by scan_cybernetic() automatically.
--
-- Idempotent: if dead_time_k already exists the existing id is returned.
-- Requires: 95_cybernetic_automata.sql applied first (control alphabet must exist).
-- =============================================================================

CREATE OR REPLACE FUNCTION nerode.ensure_dead_time(p_k INT)
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE
    v_name   TEXT;
    v_dt_id  BIGINT;
    v_alpha  BIGINT;
    v_s      INT;
BEGIN
    IF p_k < 1 THEN
        RAISE EXCEPTION 'ensure_dead_time: k must be >= 1, got %', p_k;
    END IF;

    v_name := 'dead_time_' || p_k;

    SELECT id INTO v_dt_id FROM nerode.automata WHERE name = v_name;
    IF FOUND THEN
        RETURN v_dt_id;
    END IF;

    SELECT id INTO v_alpha FROM nerode.alphabets WHERE name = 'control';
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'ensure_dead_time: control alphabet not found — '
            'apply 95_cybernetic_automata.sql before calling this function';
    END IF;

    INSERT INTO nerode.automata (name, type, alphabet_id, state_count, provenance)
    VALUES (v_name, 'DFA', v_alpha, p_k + 2,
            jsonb_build_object(
                'source',   '96_dead_time_factory',
                'pattern',  'A_{' || p_k || ',}',
                'shortcut', 'dead_time_k=' || p_k
            ))
    RETURNING id INTO v_dt_id;

    -- States: 0=idle, 1..k=wait_n, k+1=alarm
    FOR v_s IN 0..(p_k + 1) LOOP
        INSERT INTO nerode.states (automaton_id, state_id, label, is_initial, is_accepting)
        VALUES (v_dt_id, v_s,
            CASE
                WHEN v_s = 0       THEN 'idle'
                WHEN v_s = p_k + 1 THEN 'alarm'
                ELSE                    'wait_' || v_s
            END,
            v_s = 0,
            v_s = p_k + 1);
    END LOOP;

    -- State 0 (idle): A→1, R→0, _→0
    INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
    VALUES
        (v_dt_id, 0, 'A', 1),
        (v_dt_id, 0, 'R', 0),
        (v_dt_id, 0, '_', 0);

    -- States 1..k (wait_n): A→1 (reset timer), R→0 (response clears), _→n+1
    FOR v_s IN 1..p_k LOOP
        INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
        VALUES
            (v_dt_id, v_s, 'A', 1),
            (v_dt_id, v_s, 'R', 0),
            (v_dt_id, v_s, '_', v_s + 1);
    END LOOP;

    -- State k+1 (alarm): A→1, R→0, _→k+1 (alarm persists until response)
    INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
    VALUES
        (v_dt_id, p_k + 1, 'A', 1),
        (v_dt_id, p_k + 1, 'R', 0),
        (v_dt_id, p_k + 1, '_', p_k + 1);

    RETURN v_dt_id;
END;
$$;

COMMENT ON FUNCTION nerode.ensure_dead_time(INT) IS
    'Build (or return existing) dead_time_k DFA for arbitrary k >= 1. '
    'Pattern A_{k,}: fires after k consecutive no-response steps following an action. '
    'Idempotent; integrates with scan_cybernetic() automatically.';
