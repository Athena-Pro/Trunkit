-- =============================================================================
-- 92_session_automata.sql
-- DFA pattern-matching over tool-call event sequences.
--
-- Architecture
-- ------------
-- The same DFA machinery used for number sequences (alphabet {a}, unary steps)
-- is extended here to a 5-symbol tool-call alphabet:
--
--   Symbol  Event
--   ------  -----
--   R       read       (file-read tool call)
--   E       edit       (replace_string_in_file / create_file tool call)
--   P       powershell (PowerShell / shell execution tool call)
--   f       fail       (tool returned an error / test failure)
--   p       pass       (test/validation passed; terminal success signal)
--
-- Two DFAs recognise expensive session patterns and fire NOTIFY when matched:
--
--   session_calx_loop  :  P{10,}
--       ≥10 consecutive PowerShell calls without a pass — the expensive
--       "50 calx turns" loop.  Shortcut: use nerode_cached for arith_deriv:50.
--
--   session_edit_loop  :  (R f){2,}
--       Two or more read–fail cycles — the "read-fail-read-fail" edit loop.
--       Shortcut: verify file path; use replace_string_in_file with exact context.
--
-- NOTIFY channel: nerode_session_warn
-- Payload:  {"session_id":"…","pattern":"…","shortcut":"…","input_tail":"…"}
--
-- Idempotent — safe to re-apply.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. Tool-call alphabet   Σ = {E, P, R, f, p}
-- ---------------------------------------------------------------------------

INSERT INTO nerode.alphabets (name, symbols)
VALUES ('tool_call', ARRAY['E','P','R','f','p'])
ON CONFLICT (name) DO NOTHING;


-- ---------------------------------------------------------------------------
-- 2. session_log
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS nerode.session_log (
    id          BIGSERIAL    PRIMARY KEY,
    session_id  TEXT         NOT NULL,
    seq         INT          NOT NULL,       -- monotone within session
    event       TEXT         NOT NULL
                             CHECK (event IN ('read','edit','powershell','fail','pass')),
    detail      JSONB        NOT NULL DEFAULT '{}',
    logged_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_session_log_session
    ON nerode.session_log (session_id, seq);


-- ---------------------------------------------------------------------------
-- 3. Symbol mapping helper   event TEXT → single-char symbol
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.session_event_to_symbol(p_event TEXT)
RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE p_event
        WHEN 'read'       THEN 'R'
        WHEN 'edit'       THEN 'E'
        WHEN 'powershell' THEN 'P'
        WHEN 'fail'       THEN 'f'
        WHEN 'pass'       THEN 'p'
        ELSE                   '?'
    END;
$$;


-- ---------------------------------------------------------------------------
-- 4. Build the two session DFAs  (idempotent: checks IF NOT FOUND first)
-- ---------------------------------------------------------------------------

DO $$
DECLARE
    v_alpha_id  BIGINT;
    v_calx_id   BIGINT;
    v_edit_id   BIGINT;
    v_s         INTEGER;
    v_sym       TEXT;
BEGIN
    SELECT id INTO v_alpha_id FROM nerode.alphabets WHERE name = 'tool_call';

    -- =========================================================
    -- DFA 1: session_calx_loop   P{10,}
    -- =========================================================
    -- 11 states (0..10).  State 10 is the sole accepting state.
    -- Any non-P symbol resets the counter back to state 0.
    --
    --  s0 -P-> s1 -P-> s2 -P-> ... -P-> s10 [accept, self-loop on P]
    --  s_k -X-> s0   for all X in {E,R,f,p}, k in 0..10
    -- =========================================================

    SELECT id INTO v_calx_id FROM nerode.automata
    WHERE name = 'session_calx_loop';

    IF NOT FOUND THEN
        INSERT INTO nerode.automata
            (name, type, alphabet_id, state_count, provenance)
        VALUES
            ('session_calx_loop', 'DFA', v_alpha_id, 11,
             '{"source":"92_session_automata","pattern":"P{10,}"}'::JSONB)
        RETURNING id INTO v_calx_id;

        -- States
        FOR v_s IN 0..10 LOOP
            INSERT INTO nerode.states
                (automaton_id, state_id, label, is_initial, is_accepting)
            VALUES
                (v_calx_id, v_s, 'P^' || v_s, v_s = 0, v_s = 10);
        END LOOP;

        -- P transitions: chain s0→s1→…→s9→s10, then s10 self-loops
        FOR v_s IN 0..9 LOOP
            INSERT INTO nerode.transitions
                (automaton_id, from_state, symbol, to_state)
            VALUES (v_calx_id, v_s, 'P', v_s + 1);
        END LOOP;
        INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
        VALUES (v_calx_id, 10, 'P', 10);

        -- Non-P transitions: every state resets to s0
        FOREACH v_sym IN ARRAY ARRAY['E','R','f','p'] LOOP
            FOR v_s IN 0..10 LOOP
                INSERT INTO nerode.transitions
                    (automaton_id, from_state, symbol, to_state)
                VALUES (v_calx_id, v_s, v_sym, 0);
            END LOOP;
        END LOOP;
    END IF;


    -- =========================================================
    -- DFA 2: session_edit_loop   (R f){2,}
    -- =========================================================
    -- 5 states (0..4).  State 4 is the accepting state.
    --
    --  s0(init)  -R->  s1(saw_R)   -f->  s2(saw_Rf)
    --  s2        -R->  s3(saw_RfR) -f->  s4(accept)
    --  s4        -R->  s3    (can accumulate more cycles)
    --  s4        -f->  s4    (stay accepting on extra fails)
    --  All other transitions (wrong symbol) → s0
    --  Consecutive R in s1/s3 stays in the same state (wait for f)
    -- =========================================================

    SELECT id INTO v_edit_id FROM nerode.automata
    WHERE name = 'session_edit_loop';

    IF NOT FOUND THEN
        INSERT INTO nerode.automata
            (name, type, alphabet_id, state_count, provenance)
        VALUES
            ('session_edit_loop', 'DFA', v_alpha_id, 5,
             '{"source":"92_session_automata","pattern":"(Rf){2,}"}'::JSONB)
        RETURNING id INTO v_edit_id;

        -- States
        INSERT INTO nerode.states (automaton_id, state_id, label, is_initial, is_accepting)
        VALUES
            (v_edit_id, 0, 'init',    TRUE,  FALSE),
            (v_edit_id, 1, 'saw_R',   FALSE, FALSE),
            (v_edit_id, 2, 'saw_Rf',  FALSE, FALSE),
            (v_edit_id, 3, 'saw_RfR', FALSE, FALSE),
            (v_edit_id, 4, 'accept',  FALSE, TRUE);

        -- Transitions
        -- s0: R→s1, all others→s0
        INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
        VALUES
            (v_edit_id, 0, 'R', 1),
            (v_edit_id, 0, 'E', 0),
            (v_edit_id, 0, 'P', 0),
            (v_edit_id, 0, 'f', 0),
            (v_edit_id, 0, 'p', 0);

        -- s1 (saw R): f→s2, R stays→s1, others→s0
        INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
        VALUES
            (v_edit_id, 1, 'R', 1),
            (v_edit_id, 1, 'f', 2),
            (v_edit_id, 1, 'E', 0),
            (v_edit_id, 1, 'P', 0),
            (v_edit_id, 1, 'p', 0);

        -- s2 (saw Rf): R→s3, others→s0
        INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
        VALUES
            (v_edit_id, 2, 'R', 3),
            (v_edit_id, 2, 'E', 0),
            (v_edit_id, 2, 'P', 0),
            (v_edit_id, 2, 'f', 0),
            (v_edit_id, 2, 'p', 0);

        -- s3 (saw RfR): f→s4, R stays→s3, others→s0
        INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
        VALUES
            (v_edit_id, 3, 'R', 3),
            (v_edit_id, 3, 'f', 4),
            (v_edit_id, 3, 'E', 0),
            (v_edit_id, 3, 'P', 0),
            (v_edit_id, 3, 'p', 0);

        -- s4 (accept): R→s3 (another cycle), f→s4 (extra fail), others→s0
        INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
        VALUES
            (v_edit_id, 4, 'R', 3),
            (v_edit_id, 4, 'f', 4),
            (v_edit_id, 4, 'E', 0),
            (v_edit_id, 4, 'P', 0),
            (v_edit_id, 4, 'p', 0);
    END IF;

END;
$$;


-- ---------------------------------------------------------------------------
-- 5. nerode.scan_session — run both DFAs; NOTIFY on match
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.scan_session(
    p_session_id TEXT,
    p_window     INT  DEFAULT 60   -- examine only the last N events
)
RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE
    v_input     TEXT;
    v_calx_id   BIGINT;
    v_edit_id   BIGINT;
    v_accept    BOOLEAN;
    v_payload   TEXT;
BEGIN
    -- Build the input string from the last p_window events (chronological order).
    SELECT string_agg(
               nerode.session_event_to_symbol(event),
               ''
               ORDER BY seq
           )
    INTO   v_input
    FROM   (
        SELECT event, seq
        FROM   nerode.session_log
        WHERE  session_id = p_session_id
        ORDER  BY seq DESC
        LIMIT  p_window
    ) recent;

    IF v_input IS NULL OR v_input = '' THEN
        RETURN;
    END IF;

    SELECT id INTO v_calx_id FROM nerode.automata WHERE name = 'session_calx_loop';
    SELECT id INTO v_edit_id FROM nerode.automata WHERE name = 'session_edit_loop';

    -- ── calx_loop ──────────────────────────────────────────────────────────
    IF v_calx_id IS NOT NULL THEN
        SELECT accept INTO v_accept
        FROM   nerode.run(v_calx_id, v_input, FALSE);

        IF v_accept THEN
            v_payload := json_build_object(
                'session_id', p_session_id,
                'pattern',    'calx_loop',
                'shortcut',   'D(1..50) is pre-cached: SELECT nerode.query_sequence_cache(''arith_deriv:50'')',
                'input_tail', right(v_input, 15)
            )::TEXT;
            PERFORM pg_notify('nerode_session_warn', v_payload);
        END IF;
    END IF;

    -- ── edit_loop ──────────────────────────────────────────────────────────
    IF v_edit_id IS NOT NULL THEN
        SELECT accept INTO v_accept
        FROM   nerode.run(v_edit_id, v_input, FALSE);

        IF v_accept THEN
            v_payload := json_build_object(
                'session_id', p_session_id,
                'pattern',    'edit_loop',
                'shortcut',   'Two or more read-fail cycles detected; verify the file path and use replace_string_in_file with exact surrounding context',
                'input_tail', right(v_input, 15)
            )::TEXT;
            PERFORM pg_notify('nerode_session_warn', v_payload);
        END IF;
    END IF;
END;
$$;


-- ---------------------------------------------------------------------------
-- 6. nerode.log_event — append an event and auto-scan for patterns
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.log_event(
    p_session_id TEXT,
    p_event      TEXT,
    p_detail     JSONB DEFAULT '{}'
)
RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE
    v_seq INT;
BEGIN
    -- Derive next sequence number for this session
    SELECT COALESCE(MAX(seq), 0) + 1
    INTO   v_seq
    FROM   nerode.session_log
    WHERE  session_id = p_session_id;

    INSERT INTO nerode.session_log (session_id, seq, event, detail)
    VALUES (p_session_id, v_seq, p_event, p_detail);

    PERFORM nerode.scan_session(p_session_id);
END;
$$;


-- ---------------------------------------------------------------------------
-- 7. nerode.session_dfa_state — read the current DFA state for a session
--
-- Replays the last p_window events through the named DFA and returns the
-- resulting state_id.  Returns NULL if the DFA name is not found or any
-- transition is missing.  Returns the initial state_id for an empty log.
--
-- This is the readable counterpart to scan_session: scan_session fires a
-- NOTIFY on pattern match but discards the final state; session_dfa_state
-- makes that state addressable for handoff envelopes.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.session_dfa_state(
    p_session_id TEXT,
    p_dfa_name   TEXT,
    p_window     INT DEFAULT 60
)
RETURNS INT
LANGUAGE plpgsql AS $$
DECLARE
    v_automaton_id BIGINT;
    v_input        TEXT;
    v_init_state   INT;
BEGIN
    SELECT id INTO v_automaton_id
    FROM   nerode.automata
    WHERE  name = p_dfa_name;

    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    -- Mirror the window logic in scan_session: last p_window events, chronological.
    SELECT string_agg(
               nerode.session_event_to_symbol(event),
               ''
               ORDER BY seq
           )
    INTO   v_input
    FROM   (
        SELECT event, seq
        FROM   nerode.session_log
        WHERE  session_id = p_session_id
        ORDER  BY seq DESC
        LIMIT  p_window
    ) recent;

    IF v_input IS NULL OR v_input = '' THEN
        SELECT state_id INTO v_init_state
        FROM   nerode.states
        WHERE  automaton_id = v_automaton_id AND is_initial = TRUE
        LIMIT  1;
        RETURN v_init_state;
    END IF;

    RETURN nerode.run_to_state(v_automaton_id, v_input);
END;
$$;

COMMENT ON FUNCTION nerode.session_dfa_state(TEXT, TEXT, INT) IS
    'Return the current state_id of the named DFA after replaying the last '
    'p_window session events.  Returns the initial state for an empty log, '
    'NULL for an unknown DFA or a missing transition.';
