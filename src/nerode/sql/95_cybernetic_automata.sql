-- =============================================================================
-- 95_cybernetic_automata.sql
-- DFA pattern-matching over metric and control-signal sequences.
--
-- Architecture
-- ------------
-- Three new alphabets extend the DFA machinery to cybernetic domains:
--
--   metric      {U, D, S}   U=rise  D=fall  S=stable
--   control     {A, R, _}   A=action  R=response  _=neither
--   homeostasis {I, O}      I=inside band  O=outside band
--
-- Eight DFAs capture control-relevant patterns:
--
--   metric_rise_3        U{3,}          effector not responding (3+ consecutive rises)
--   metric_oscillate     (UD){3,}       oscillation / gain too high
--   metric_bounce_3      D{3,}U{3,}     V-shape setpoint search
--
--   dead_time_5          A_{5,}         action without response in 5 steps
--   dead_time_10         A_{10,}        action without response in 10 steps
--   dead_time_20         A_{20,}        action without response in 20 steps
--
--   homeostasis_alarm_5  O{5,}          outside target band for 5+ steps
--   homeostasis_stable_5 O+I{5,}        convergence: outside then inside 5+ steps
--
-- Infrastructure reuse
-- --------------------
-- The same automata/states/transitions tables and run_to_state() machinery
-- used for session DFAs carry over unchanged.  A separate cybernetic_log
-- table (parallel to session_log) stores metric / control / homeostasis events.
-- log_cybernetic() and scan_cybernetic() mirror log_event() and scan_session().
--
-- NOTIFY channel: nerode_control_warn
-- Payload:  {"session_id":"…","alphabet":"…","pattern":"…","shortcut":"…","input_tail":"…"}
--
-- Idempotent — safe to re-apply.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. Alphabets
-- ---------------------------------------------------------------------------

INSERT INTO nerode.alphabets (name, symbols)
VALUES
    ('metric',      ARRAY['U','D','S']),
    ('control',     ARRAY['A','R','_']),
    ('homeostasis', ARRAY['I','O'])
ON CONFLICT (name) DO NOTHING;


-- ---------------------------------------------------------------------------
-- 2. cybernetic_log
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS nerode.cybernetic_log (
    id          BIGSERIAL    PRIMARY KEY,
    session_id  TEXT         NOT NULL,
    seq         INT          NOT NULL,
    alphabet    TEXT         NOT NULL,
    symbol      TEXT         NOT NULL,
    detail      JSONB        NOT NULL DEFAULT '{}',
    logged_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (session_id, alphabet, seq)
);

CREATE INDEX IF NOT EXISTS idx_cybernetic_log_session
    ON nerode.cybernetic_log (session_id, alphabet, seq);


-- ---------------------------------------------------------------------------
-- 3–5. Build the eight cybernetic DFAs  (idempotent: checks IF NOT FOUND)
-- ---------------------------------------------------------------------------

DO $$
DECLARE
    v_alpha_metric      BIGINT;
    v_alpha_control     BIGINT;
    v_alpha_homeostasis BIGINT;

    v_rise_id    BIGINT;
    v_osc_id     BIGINT;
    v_bounce_id  BIGINT;
    v_alarm_id   BIGINT;
    v_stable_id  BIGINT;

    v_dt_id      BIGINT;
    v_dt_name    TEXT;
    v_k          INT;
    v_s          INT;
BEGIN
    SELECT id INTO v_alpha_metric      FROM nerode.alphabets WHERE name = 'metric';
    SELECT id INTO v_alpha_control     FROM nerode.alphabets WHERE name = 'control';
    SELECT id INTO v_alpha_homeostasis FROM nerode.alphabets WHERE name = 'homeostasis';

    -- =========================================================
    -- DFA 1: metric_rise_3   U{3,}
    -- =========================================================
    -- 4 states (0..3).  State 3 is the sole accepting state.
    --
    --   0 -U-> 1 -U-> 2 -U-> 3 [accept, self-loop on U]
    --   s_k -D/S-> 0   for all k in 0..3
    -- =========================================================

    SELECT id INTO v_rise_id FROM nerode.automata WHERE name = 'metric_rise_3';
    IF NOT FOUND THEN
        INSERT INTO nerode.automata (name, type, alphabet_id, state_count, provenance)
        VALUES ('metric_rise_3', 'DFA', v_alpha_metric, 4,
                '{"source":"95_cybernetic_automata","pattern":"U{3,}","shortcut":"effector_not_responding"}'::JSONB)
        RETURNING id INTO v_rise_id;

        INSERT INTO nerode.states (automaton_id, state_id, label, is_initial, is_accepting)
        VALUES
            (v_rise_id, 0, 'U^0', TRUE,  FALSE),
            (v_rise_id, 1, 'U^1', FALSE, FALSE),
            (v_rise_id, 2, 'U^2', FALSE, FALSE),
            (v_rise_id, 3, 'U^3', FALSE, TRUE);

        -- U: chain 0→1→2→3, then 3 self-loops on U
        INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
        VALUES
            (v_rise_id, 0, 'U', 1),
            (v_rise_id, 1, 'U', 2),
            (v_rise_id, 2, 'U', 3),
            (v_rise_id, 3, 'U', 3);

        -- D and S: all states reset to 0
        INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
        SELECT v_rise_id, s, sym, 0
        FROM   generate_series(0, 3) s
        CROSS  JOIN (VALUES ('D'), ('S')) t(sym);
    END IF;


    -- =========================================================
    -- DFA 2: metric_oscillate   (UD){3,}
    -- =========================================================
    -- 7 states (0..6).  State 6 is the sole accepting state.
    --
    --   0=init  1=U  2=UD  3=UDU  4=UDUD  5=UDUDU  6=osc3+ [accept]
    --
    --   Within "waiting for opposite" states, repeated same-symbol readings
    --   are absorbed (sensor persistence):
    --     U states (1,3,5): U→self, D→next
    --     D states (2,4,6): D→self, U→next
    --   S resets to 0 from any state.
    -- =========================================================

    SELECT id INTO v_osc_id FROM nerode.automata WHERE name = 'metric_oscillate';
    IF NOT FOUND THEN
        INSERT INTO nerode.automata (name, type, alphabet_id, state_count, provenance)
        VALUES ('metric_oscillate', 'DFA', v_alpha_metric, 7,
                '{"source":"95_cybernetic_automata","pattern":"(UD){3,}","shortcut":"oscillation_high_gain"}'::JSONB)
        RETURNING id INTO v_osc_id;

        INSERT INTO nerode.states (automaton_id, state_id, label, is_initial, is_accepting)
        VALUES
            (v_osc_id, 0, 'init',  TRUE,  FALSE),
            (v_osc_id, 1, 'U',     FALSE, FALSE),
            (v_osc_id, 2, 'UD',    FALSE, FALSE),
            (v_osc_id, 3, 'UDU',   FALSE, FALSE),
            (v_osc_id, 4, 'UDUD',  FALSE, FALSE),
            (v_osc_id, 5, 'UDUDU', FALSE, FALSE),
            (v_osc_id, 6, 'osc3+', FALSE, TRUE);

        INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
        VALUES
            -- 0 (init): U starts, D/S reset
            (v_osc_id, 0, 'U', 1), (v_osc_id, 0, 'D', 0), (v_osc_id, 0, 'S', 0),
            -- 1 (U): D advances, U stays (persistence), S resets
            (v_osc_id, 1, 'D', 2), (v_osc_id, 1, 'U', 1), (v_osc_id, 1, 'S', 0),
            -- 2 (UD): U advances, D stays, S resets
            (v_osc_id, 2, 'U', 3), (v_osc_id, 2, 'D', 2), (v_osc_id, 2, 'S', 0),
            -- 3 (UDU): D advances, U stays, S resets
            (v_osc_id, 3, 'D', 4), (v_osc_id, 3, 'U', 3), (v_osc_id, 3, 'S', 0),
            -- 4 (UDUD): U advances, D stays, S resets
            (v_osc_id, 4, 'U', 5), (v_osc_id, 4, 'D', 4), (v_osc_id, 4, 'S', 0),
            -- 5 (UDUDU): D accepts, U stays, S resets
            (v_osc_id, 5, 'D', 6), (v_osc_id, 5, 'U', 5), (v_osc_id, 5, 'S', 0),
            -- 6 (osc3+, accept): D stays, U→5 (new pair), S resets
            (v_osc_id, 6, 'D', 6), (v_osc_id, 6, 'U', 5), (v_osc_id, 6, 'S', 0);
    END IF;


    -- =========================================================
    -- DFA 3: metric_bounce_3   D{3,}U{3,}
    -- =========================================================
    -- 7 states (0..6).  State 6 is the sole accepting state.
    --
    --   0=init  1=D  2=DD  3=DDD+  4=DDD+U  5=DDD+UU  6=DDD+UUU+ [accept]
    --
    --   State 3 self-loops on D (absorbs additional descent steps).
    --   State 6 self-loops on U (absorbs additional ascent steps).
    --   A D interrupting the U-run resets to state 1 (one new D).
    --   S resets to 0 from any state.
    -- =========================================================

    SELECT id INTO v_bounce_id FROM nerode.automata WHERE name = 'metric_bounce_3';
    IF NOT FOUND THEN
        INSERT INTO nerode.automata (name, type, alphabet_id, state_count, provenance)
        VALUES ('metric_bounce_3', 'DFA', v_alpha_metric, 7,
                '{"source":"95_cybernetic_automata","pattern":"D{3,}U{3,}","shortcut":"setpoint_search"}'::JSONB)
        RETURNING id INTO v_bounce_id;

        INSERT INTO nerode.states (automaton_id, state_id, label, is_initial, is_accepting)
        VALUES
            (v_bounce_id, 0, 'init',     TRUE,  FALSE),
            (v_bounce_id, 1, 'D',        FALSE, FALSE),
            (v_bounce_id, 2, 'DD',       FALSE, FALSE),
            (v_bounce_id, 3, 'DDD+',     FALSE, FALSE),
            (v_bounce_id, 4, 'DDD+U',    FALSE, FALSE),
            (v_bounce_id, 5, 'DDD+UU',   FALSE, FALSE),
            (v_bounce_id, 6, 'DDD+UUU+', FALSE, TRUE);

        INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
        VALUES
            -- 0: D starts descent; U/S reset (no history)
            (v_bounce_id, 0, 'D', 1), (v_bounce_id, 0, 'U', 0), (v_bounce_id, 0, 'S', 0),
            -- 1: D deepens; U/S reset (1 D — insufficient for V)
            (v_bounce_id, 1, 'D', 2), (v_bounce_id, 1, 'U', 0), (v_bounce_id, 1, 'S', 0),
            -- 2: D deepens; U/S reset (2 D's — insufficient for V)
            (v_bounce_id, 2, 'D', 3), (v_bounce_id, 2, 'U', 0), (v_bounce_id, 2, 'S', 0),
            -- 3 (DDD+): D self-loops; U starts ascent; S resets
            (v_bounce_id, 3, 'D', 3), (v_bounce_id, 3, 'U', 4), (v_bounce_id, 3, 'S', 0),
            -- 4 (DDD+U): D restarts descent from 1; U advances; S resets
            (v_bounce_id, 4, 'D', 1), (v_bounce_id, 4, 'U', 5), (v_bounce_id, 4, 'S', 0),
            -- 5 (DDD+UU): D restarts from 1; U accepts; S resets
            (v_bounce_id, 5, 'D', 1), (v_bounce_id, 5, 'U', 6), (v_bounce_id, 5, 'S', 0),
            -- 6 (accept): D restarts descent; U self-loops; S resets
            (v_bounce_id, 6, 'D', 1), (v_bounce_id, 6, 'U', 6), (v_bounce_id, 6, 'S', 0);
    END IF;


    -- =========================================================
    -- DFAs 4–6: dead_time_5, dead_time_10, dead_time_20
    --           Pattern: A_{k,}
    -- =========================================================
    -- k+2 states (0..k+1).  State k+1 is the sole accepting state.
    --
    --   State 0 (idle):     A→1, R→0, _→0
    --   State n (1≤n≤k):    A→1 (new action resets timer),
    --                        R→0 (response clears),
    --                        _→n+1 (one more no-response step)
    --   State k+1 (alarm):  A→1 (new action during alarm),
    --                        R→0 (late response),
    --                        _→k+1 (alarm persists)
    --
    -- dead_time_k fires after exactly k consecutive _ symbols follow an A.
    -- =========================================================

    FOREACH v_k IN ARRAY ARRAY[5, 10, 20]::INT[] LOOP
        v_dt_name := 'dead_time_' || v_k;
        SELECT id INTO v_dt_id FROM nerode.automata WHERE name = v_dt_name;
        IF NOT FOUND THEN
            INSERT INTO nerode.automata (name, type, alphabet_id, state_count, provenance)
            VALUES (v_dt_name, 'DFA', v_alpha_control, v_k + 2,
                    jsonb_build_object(
                        'source',   '95_cybernetic_automata',
                        'pattern',  'A_{' || v_k || ',}',
                        'shortcut', 'dead_time_k=' || v_k
                    ))
            RETURNING id INTO v_dt_id;

            -- States: 0=idle, 1..k=wait_n, k+1=alarm
            FOR v_s IN 0..(v_k + 1) LOOP
                INSERT INTO nerode.states
                    (automaton_id, state_id, label, is_initial, is_accepting)
                VALUES (v_dt_id, v_s,
                    CASE WHEN v_s = 0        THEN 'idle'
                         WHEN v_s = v_k + 1  THEN 'alarm'
                         ELSE                     'wait_' || v_s
                    END,
                    v_s = 0, v_s = v_k + 1);
            END LOOP;

            -- State 0 (idle): A→1, R→0, _→0
            INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
            VALUES
                (v_dt_id, 0, 'A', 1),
                (v_dt_id, 0, 'R', 0),
                (v_dt_id, 0, '_', 0);

            -- States 1..k (wait_n): A→1, R→0, _→n+1
            FOR v_s IN 1..v_k LOOP
                INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
                VALUES
                    (v_dt_id, v_s, 'A', 1),
                    (v_dt_id, v_s, 'R', 0),
                    (v_dt_id, v_s, '_', v_s + 1);
            END LOOP;

            -- State k+1 (alarm): A→1, R→0, _→k+1 (persists)
            INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
            VALUES
                (v_dt_id, v_k + 1, 'A', 1),
                (v_dt_id, v_k + 1, 'R', 0),
                (v_dt_id, v_k + 1, '_', v_k + 1);
        END IF;
    END LOOP;


    -- =========================================================
    -- DFA 7: homeostasis_alarm_5   O{5,}
    -- =========================================================
    -- 6 states (0..5).  State 5 is the sole accepting state.
    --
    --   O advances through 0→1→2→3→4→5; state 5 self-loops on O.
    --   I resets to 0 from any state.
    -- =========================================================

    SELECT id INTO v_alarm_id FROM nerode.automata WHERE name = 'homeostasis_alarm_5';
    IF NOT FOUND THEN
        INSERT INTO nerode.automata (name, type, alphabet_id, state_count, provenance)
        VALUES ('homeostasis_alarm_5', 'DFA', v_alpha_homeostasis, 6,
                '{"source":"95_cybernetic_automata","pattern":"O{5,}","shortcut":"out_of_band_5"}'::JSONB)
        RETURNING id INTO v_alarm_id;

        INSERT INTO nerode.states (automaton_id, state_id, label, is_initial, is_accepting)
        VALUES
            (v_alarm_id, 0, 'O^0', TRUE,  FALSE),
            (v_alarm_id, 1, 'O^1', FALSE, FALSE),
            (v_alarm_id, 2, 'O^2', FALSE, FALSE),
            (v_alarm_id, 3, 'O^3', FALSE, FALSE),
            (v_alarm_id, 4, 'O^4', FALSE, FALSE),
            (v_alarm_id, 5, 'O^5', FALSE, TRUE);

        -- O: chain 0→5, then 5 self-loops
        INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
        VALUES
            (v_alarm_id, 0, 'O', 1), (v_alarm_id, 0, 'I', 0),
            (v_alarm_id, 1, 'O', 2), (v_alarm_id, 1, 'I', 0),
            (v_alarm_id, 2, 'O', 3), (v_alarm_id, 2, 'I', 0),
            (v_alarm_id, 3, 'O', 4), (v_alarm_id, 3, 'I', 0),
            (v_alarm_id, 4, 'O', 5), (v_alarm_id, 4, 'I', 0),
            (v_alarm_id, 5, 'O', 5), (v_alarm_id, 5, 'I', 0);
    END IF;


    -- =========================================================
    -- DFA 8: homeostasis_stable_5   O+I{5,}
    -- =========================================================
    -- 7 states (0..6).  State 6 is the sole accepting state.
    --
    --   0=pre_O (no O seen yet)   1=O+ (in/after O-run)
    --   2..5 = O+I{1..4}          6=converged (O+I{5+}) [accept]
    --
    --   I before any O stays in 0 — inside band from the start is NOT
    --   convergence; convergence requires having left the band first.
    --   O from any state ≥1 restarts the I-count from 1.
    --   O from state 6 restarts too — a new excursion requires re-convergence.
    -- =========================================================

    SELECT id INTO v_stable_id FROM nerode.automata WHERE name = 'homeostasis_stable_5';
    IF NOT FOUND THEN
        INSERT INTO nerode.automata (name, type, alphabet_id, state_count, provenance)
        VALUES ('homeostasis_stable_5', 'DFA', v_alpha_homeostasis, 7,
                '{"source":"95_cybernetic_automata","pattern":"O+I{5,}","shortcut":"convergence_after_excursion"}'::JSONB)
        RETURNING id INTO v_stable_id;

        INSERT INTO nerode.states (automaton_id, state_id, label, is_initial, is_accepting)
        VALUES
            (v_stable_id, 0, 'pre_O',     TRUE,  FALSE),
            (v_stable_id, 1, 'O+',        FALSE, FALSE),
            (v_stable_id, 2, 'O+I^1',     FALSE, FALSE),
            (v_stable_id, 3, 'O+I^2',     FALSE, FALSE),
            (v_stable_id, 4, 'O+I^3',     FALSE, FALSE),
            (v_stable_id, 5, 'O+I^4',     FALSE, FALSE),
            (v_stable_id, 6, 'converged', FALSE, TRUE);

        INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
        VALUES
            -- 0 (pre_O): O→1 (first excursion); I→0 (inside from start)
            (v_stable_id, 0, 'O', 1), (v_stable_id, 0, 'I', 0),
            -- 1 (O+): O→1 (more outside); I→2 (first I after O-run)
            (v_stable_id, 1, 'O', 1), (v_stable_id, 1, 'I', 2),
            -- 2..5: O→1 (new excursion resets I-count); I→next
            (v_stable_id, 2, 'O', 1), (v_stable_id, 2, 'I', 3),
            (v_stable_id, 3, 'O', 1), (v_stable_id, 3, 'I', 4),
            (v_stable_id, 4, 'O', 1), (v_stable_id, 4, 'I', 5),
            (v_stable_id, 5, 'O', 1), (v_stable_id, 5, 'I', 6),
            -- 6 (converged, accept): O→1 (new excursion); I→6 (more I's fine)
            (v_stable_id, 6, 'O', 1), (v_stable_id, 6, 'I', 6);
    END IF;

END;
$$;


-- ---------------------------------------------------------------------------
-- 6. nerode.log_cybernetic — append a symbol and auto-scan
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.log_cybernetic(
    p_session_id TEXT,
    p_alphabet   TEXT,    -- 'metric', 'control', or 'homeostasis'
    p_symbol     TEXT,    -- e.g. 'U','D','S'  or  'A','R','_'  or  'I','O'
    p_detail     JSONB    DEFAULT '{}'
)
RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE
    v_seq INT;
BEGIN
    SELECT COALESCE(MAX(seq), 0) + 1
    INTO   v_seq
    FROM   nerode.cybernetic_log
    WHERE  session_id = p_session_id
      AND  alphabet   = p_alphabet;

    INSERT INTO nerode.cybernetic_log (session_id, seq, alphabet, symbol, detail)
    VALUES (p_session_id, v_seq, p_alphabet, p_symbol, p_detail);

    PERFORM nerode.scan_cybernetic(p_session_id, p_alphabet);
END;
$$;

COMMENT ON FUNCTION nerode.log_cybernetic(TEXT, TEXT, TEXT, JSONB) IS
    'Append a cybernetic symbol to the log and fire scan_cybernetic() for the session.';


-- ---------------------------------------------------------------------------
-- 7. nerode.scan_cybernetic — run all DFAs for an alphabet; NOTIFY on match
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.scan_cybernetic(
    p_session_id TEXT,
    p_alphabet   TEXT,
    p_window     INT  DEFAULT 100
)
RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE
    v_input      TEXT;
    v_auto_id    BIGINT;
    v_auto_name  TEXT;
    v_accept     BOOLEAN;
    v_shortcut   TEXT;
    v_payload    TEXT;
BEGIN
    -- Build the input string from the last p_window symbols (chronological order).
    SELECT string_agg(symbol, '' ORDER BY seq)
    INTO   v_input
    FROM   (
        SELECT symbol, seq
        FROM   nerode.cybernetic_log
        WHERE  session_id = p_session_id
          AND  alphabet   = p_alphabet
        ORDER  BY seq DESC
        LIMIT  p_window
    ) recent;

    IF v_input IS NULL OR v_input = '' THEN
        RETURN;
    END IF;

    -- Run every DFA whose alphabet matches p_alphabet.
    FOR v_auto_id, v_auto_name, v_shortcut IN
        SELECT a.id, a.name, a.provenance->>'shortcut'
        FROM   nerode.automata a
        JOIN   nerode.alphabets al ON al.id = a.alphabet_id
        WHERE  al.name = p_alphabet
    LOOP
        SELECT accept INTO v_accept
        FROM   nerode.run(v_auto_id, v_input, FALSE);

        IF v_accept THEN
            v_payload := json_build_object(
                'session_id', p_session_id,
                'alphabet',   p_alphabet,
                'pattern',    v_auto_name,
                'shortcut',   COALESCE(v_shortcut, ''),
                'input_tail', right(v_input, 20)
            )::TEXT;
            PERFORM pg_notify('nerode_control_warn', v_payload);
        END IF;
    END LOOP;
END;
$$;

COMMENT ON FUNCTION nerode.scan_cybernetic(TEXT, TEXT, INT) IS
    'Run all DFAs for the given alphabet over the last p_window cybernetic_log '
    'symbols.  Fires nerode_control_warn NOTIFY for each matching pattern.';
