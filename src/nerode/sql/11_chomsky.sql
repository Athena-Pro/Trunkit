-- =============================================================================
--  chomsky — Chomsky Hierarchy Simulation (Types 0–3)
--
--  Four machines, each recognising a canonical language at its Chomsky level:
--
--    Type 3 (DFA):  a*b*             — regular (inline simulation)
--    Type 2 (PDA):  { a^n b^n | n≥1 }    — pushdown (explicit stack as TEXT[])
--    Type 1 (LBA):  { a^n b^n c^n | n≥1 } — linear-bounded (tape array, O(n²))
--    Type 0 (TM):   { 0^(2^k) | k≥0 }    — Turing machine (tape + halving rounds)
--
--  Each runner returns (accept BOOLEAN, level TEXT, witness JSONB).
--  chomsky.classify(input) runs all four and reports each result.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS chomsky;

-- ---------------------------------------------------------------------------
-- Register cert methods
-- ---------------------------------------------------------------------------

INSERT INTO cert.method (name, claim_kind, checker_kind, description) VALUES
    ('chomsky_dfa', 'structural', 'sql', 'Type-3 inline DFA simulation for a*b*.'),
    ('chomsky_pda', 'structural', 'sql', 'Type-2 PDA simulation for a^n b^n.'),
    ('chomsky_lba', 'structural', 'sql', 'Type-1 LBA tape simulation for a^n b^n c^n.'),
    ('chomsky_tm',  'structural', 'sql', 'Type-0 TM tape simulation for 0^(2^k).')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Type 3: DFA for a*b*
-- States: q_a (reading a's), q_b (reading b's), q_dead
-- Accepts: any string of zero or more a's followed by zero or more b's.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION chomsky.run_dfa(
    p_input TEXT,
    p_trace BOOLEAN DEFAULT TRUE
)
RETURNS TABLE (accept BOOLEAN, level TEXT, witness JSONB)
LANGUAGE plpgsql AS $$
DECLARE
    v_state TEXT    := 'q_a';
    v_sym   TEXT;
    v_i     INTEGER;
    v_parts TEXT[];
BEGIN
    IF p_trace THEN
        v_parts[1] := jsonb_build_object('step', 0, 'state', v_state, 'sym', NULL)::TEXT;
    END IF;

    FOR v_i IN 1..length(p_input) LOOP
        v_sym := substring(p_input, v_i, 1);
        CASE v_state
            WHEN 'q_a' THEN
                IF    v_sym = 'a' THEN v_state := 'q_a';
                ELSIF v_sym = 'b' THEN v_state := 'q_b';
                ELSE                   v_state := 'q_dead';
                END IF;
            WHEN 'q_b' THEN
                IF    v_sym = 'b' THEN v_state := 'q_b';
                ELSE                   v_state := 'q_dead';
                END IF;
            ELSE  -- q_dead: absorbing
                NULL;
        END CASE;

        IF p_trace THEN
            v_parts[v_i + 1] := jsonb_build_object(
                'step', v_i, 'state', v_state, 'sym', v_sym
            )::TEXT;
        END IF;

        IF v_state = 'q_dead' THEN
            RETURN QUERY SELECT
                FALSE,
                'dfa'::TEXT,
                jsonb_build_object(
                    'reason', format('unexpected %L in state %s at position %s',
                                     v_sym, v_state, v_i),
                    'steps',  CASE WHEN p_trace
                                   THEN ('[' || array_to_string(v_parts[1:v_i + 1], ',') || ']')::JSONB
                                   ELSE NULL::JSONB END
                );
            RETURN;
        END IF;
    END LOOP;

    RETURN QUERY SELECT
        TRUE,
        'dfa'::TEXT,
        jsonb_build_object(
            'final_state', v_state,
            'steps', CASE WHEN p_trace
                          THEN ('[' || array_to_string(v_parts, ',') || ']')::JSONB
                          ELSE NULL::JSONB END
        );
END;
$$;

COMMENT ON FUNCTION chomsky.run_dfa(TEXT, BOOLEAN) IS
    'Type-3 inline DFA for a*b*. Returns (accept, level=''dfa'', witness).';

-- ---------------------------------------------------------------------------
-- Type 2: PDA for { a^n b^n | n >= 1 }
-- Stack alphabet: { $=bottom, A=pushed-a }
-- States: q0 (reading a's and first b transition), q1 (reading subsequent b's)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION chomsky.run_pda(
    p_input TEXT,
    p_trace BOOLEAN DEFAULT TRUE
)
RETURNS TABLE (accept BOOLEAN, level TEXT, witness JSONB)
LANGUAGE plpgsql AS $$
DECLARE
    v_stack TEXT[]  := ARRAY['$'];
    v_state TEXT    := 'q0';
    v_sym   TEXT;
    v_i     INTEGER;
    v_parts TEXT[];
    v_steps JSONB;
    v_top   TEXT;
    v_slen  INTEGER;
BEGIN
    IF length(p_input) = 0 THEN
        RETURN QUERY SELECT FALSE, 'pda'::TEXT,
            jsonb_build_object('reason', 'empty string not in a^n b^n (n>=1)');
        RETURN;
    END IF;

    IF p_trace THEN
        v_parts[1] := jsonb_build_object(
            'step', 0, 'state', v_state, 'stack', v_stack, 'sym', NULL
        )::TEXT;
    END IF;

    FOR v_i IN 1..length(p_input) LOOP
        v_sym  := substring(p_input, v_i, 1);
        v_slen := array_length(v_stack, 1);
        v_top  := v_stack[v_slen];

        IF v_state = 'q0' THEN
            IF v_sym = 'a' THEN
                v_stack := array_append(v_stack, 'A');
            ELSIF v_sym = 'b' THEN
                IF v_top != 'A' THEN
                    -- No A on stack: more b's than a's
                    RETURN QUERY SELECT FALSE, 'pda'::TEXT,
                        jsonb_build_object('reason',
                            format('stack underflow at position %s: no a to match b', v_i),
                            'final_stack', v_stack,
                            'steps', CASE WHEN p_trace
                                THEN ('[' || array_to_string(v_parts[1:v_i], ',') || ']')::JSONB
                                ELSE NULL::JSONB END);
                    RETURN;
                END IF;
                v_stack := v_stack[1:v_slen - 1];
                v_state := 'q1';
            ELSE
                RETURN QUERY SELECT FALSE, 'pda'::TEXT,
                    jsonb_build_object('reason',
                        format('unexpected symbol %L in state q0 at position %s', v_sym, v_i));
                RETURN;
            END IF;
        ELSIF v_state = 'q1' THEN
            IF v_sym = 'b' THEN
                IF v_top != 'A' THEN
                    RETURN QUERY SELECT FALSE, 'pda'::TEXT,
                        jsonb_build_object('reason',
                            format('stack underflow at position %s: more b''s than a''s', v_i),
                            'final_stack', v_stack,
                            'steps', CASE WHEN p_trace
                                THEN ('[' || array_to_string(v_parts[1:v_i], ',') || ']')::JSONB
                                ELSE NULL::JSONB END);
                    RETURN;
                END IF;
                v_stack := v_stack[1:v_slen - 1];
            ELSE
                RETURN QUERY SELECT FALSE, 'pda'::TEXT,
                    jsonb_build_object('reason',
                        format('unexpected symbol %L in state q1 at position %s', v_sym, v_i));
                RETURN;
            END IF;
        END IF;

        IF p_trace THEN
            v_parts[v_i + 1] := jsonb_build_object(
                'step', v_i, 'state', v_state, 'stack', v_stack, 'sym', v_sym
            )::TEXT;
        END IF;
    END LOOP;

    -- Accept iff state=q1 and stack=['$'] (all a's matched by b's)
    IF v_state = 'q1' AND v_stack = ARRAY['$'] THEN
        IF p_trace THEN
            v_steps := ('[' || array_to_string(v_parts, ',') || ']')::JSONB;
        END IF;
        RETURN QUERY SELECT TRUE, 'pda'::TEXT,
            jsonb_build_object('final_state', v_state, 'final_stack', v_stack,
                               'steps', v_steps);
    ELSE
        IF p_trace THEN
            v_steps := ('[' || array_to_string(v_parts, ',') || ']')::JSONB;
        END IF;
        RETURN QUERY SELECT FALSE, 'pda'::TEXT,
            jsonb_build_object(
                'reason',
                CASE
                    WHEN v_state = 'q0' THEN 'no b''s seen (input has only a''s)'
                    WHEN array_length(v_stack, 1) > 1 THEN
                        format('more a''s than b''s: %s unmatched', array_length(v_stack, 1) - 1)
                    ELSE 'rejected'
                END,
                'final_state', v_state,
                'final_stack', v_stack,
                'steps', v_steps);
    END IF;
END;
$$;

COMMENT ON FUNCTION chomsky.run_pda(TEXT, BOOLEAN) IS
    'Type-2 PDA for a^n b^n (n>=1). Stack alphabet {$,A}. '
    'Returns (accept, level=''pda'', witness with step-by-step stack trace).';

-- ---------------------------------------------------------------------------
-- Type 1: LBA for { a^n b^n c^n | n >= 1 }
-- Tape: TEXT[] over {a,b,c,A,Y,Z} (lowercase=unmatched, uppercase=matched)
-- Algorithm: O(n) rounds, each round marks one (a,b,c) triple.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION chomsky.run_lba(
    p_input TEXT,
    p_trace BOOLEAN DEFAULT TRUE
)
RETURNS TABLE (accept BOOLEAN, level TEXT, witness JSONB)
LANGUAGE plpgsql AS $$
DECLARE
    v_tape   TEXT[];
    v_n      INTEGER;
    v_round  INTEGER := 0;
    v_sweeps JSONB[] := '{}';
    v_i      INTEGER;
    v_pa     INTEGER;   -- position of the a just marked
    v_pb     INTEGER;   -- position of the b just marked
    v_pc     INTEGER;   -- position of the c just marked
    v_found  BOOLEAN;
BEGIN
    v_n := length(p_input);

    IF v_n = 0 THEN
        RETURN QUERY SELECT FALSE, 'lba'::TEXT,
            jsonb_build_object('reason', 'empty string not in a^n b^n c^n (n>=1)');
        RETURN;
    END IF;

    -- Alphabet guard: input must be entirely over {a,b,c}
    IF p_input != regexp_replace(p_input, '[^abc]', '', 'g') THEN
        RETURN QUERY SELECT FALSE, 'lba'::TEXT,
            jsonb_build_object('reason', 'input contains symbols outside alphabet {a,b,c}');
        RETURN;
    END IF;

    -- Build tape from input
    SELECT array_agg(substring(p_input, gs, 1) ORDER BY gs)
    INTO v_tape
    FROM generate_series(1, v_n) AS gs;

    LOOP
        v_round := v_round + 1;

        -- Phase 1: find leftmost unmatched 'a'.
        -- Ordering invariant: if any matched b (Y) or c (Z) appears before this 'a',
        -- the input is interleaved (not a^n b^n c^n) → reject.
        v_found := FALSE;
        FOR v_i IN 1..v_n LOOP
            IF v_tape[v_i] = 'a' THEN
                -- Check that all positions to the left are only 'A' (matched a's).
                -- Any Y or Z to the left means a's appear after b's/c's.
                DECLARE v_j INTEGER;
                BEGIN
                    FOR v_j IN 1..(v_i - 1) LOOP
                        IF v_tape[v_j] IN ('Y', 'Z', 'b', 'c') THEN
                            RETURN QUERY SELECT FALSE, 'lba'::TEXT,
                                jsonb_build_object('reason',
                                    format('ordering violation: a at position %s appears after b/c at position %s',
                                           v_i, v_j),
                                    'tape', v_tape, 'sweeps', to_jsonb(v_sweeps));
                            RETURN;
                        END IF;
                    END LOOP;
                END;
                v_tape[v_i] := 'A';
                v_pa := v_i;
                v_found := TRUE;
                EXIT;
            END IF;
        END LOOP;

        IF NOT v_found THEN
            EXIT;  -- no more a's; check if tape is clean
        END IF;

        -- Phase 2: find leftmost unmatched 'b' after pa
        v_found := FALSE;
        FOR v_i IN (v_pa + 1)..v_n LOOP
            IF v_tape[v_i] = 'b' THEN
                v_tape[v_i] := 'Y';
                v_pb := v_i;
                v_found := TRUE;
                EXIT;
            END IF;
        END LOOP;

        IF NOT v_found THEN
            RETURN QUERY SELECT FALSE, 'lba'::TEXT,
                jsonb_build_object('reason',
                    format('no unmatched b found after position %s in round %s', v_pa, v_round),
                    'tape', v_tape, 'sweeps', to_jsonb(v_sweeps));
            RETURN;
        END IF;

        -- Phase 3: find leftmost unmatched 'c' after pb
        v_found := FALSE;
        FOR v_i IN (v_pb + 1)..v_n LOOP
            IF v_tape[v_i] = 'c' THEN
                v_tape[v_i] := 'Z';
                v_pc := v_i;
                v_found := TRUE;
                EXIT;
            END IF;
        END LOOP;

        IF NOT v_found THEN
            RETURN QUERY SELECT FALSE, 'lba'::TEXT,
                jsonb_build_object('reason',
                    format('no unmatched c found after position %s in round %s', v_pb, v_round),
                    'tape', v_tape, 'sweeps', to_jsonb(v_sweeps));
            RETURN;
        END IF;

        IF p_trace THEN
            v_sweeps := array_append(v_sweeps,
                jsonb_build_object('round', v_round, 'tape', v_tape,
                                   'matched', jsonb_build_object('a', v_pa, 'b', v_pb, 'c', v_pc)));
        END IF;
    END LOOP;

    -- Accept iff no unmatched b's or c's remain (a's are already all matched)
    IF array_position(v_tape, 'b') IS NOT NULL
    OR array_position(v_tape, 'c') IS NOT NULL THEN
        RETURN QUERY SELECT FALSE, 'lba'::TEXT,
            jsonb_build_object('reason', 'unmatched b or c symbols remain after all a''s matched',
                               'tape', v_tape, 'sweeps', to_jsonb(v_sweeps));
    ELSE
        RETURN QUERY SELECT TRUE, 'lba'::TEXT,
            jsonb_build_object('rounds', v_round - 1,
                               'final_tape', v_tape,
                               'sweeps', to_jsonb(v_sweeps));
    END IF;
END;
$$;

COMMENT ON FUNCTION chomsky.run_lba(TEXT, BOOLEAN) IS
    'Type-1 LBA for a^n b^n c^n (n>=1). Tape alphabet {a,b,c,A,Y,Z}. '
    'O(n) rounds, each marking one (a→A, b→Y, c→Z) triple. '
    'Returns (accept, level=''lba'', witness with per-round tape snapshots).';

-- ---------------------------------------------------------------------------
-- Type 0: TM for { 0^(2^k) | k >= 0 }
-- Tape alphabet: { '0', 'X', 'B' }
-- Algorithm: O(log n) rounds. Each round crosses off every other '0'.
--   Accept when exactly one '0' remains; reject when odd count > 1.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION chomsky.run_tm(
    p_input  TEXT,
    p_budget INTEGER DEFAULT 64,   -- max rounds (log₂ budget)
    p_trace  BOOLEAN DEFAULT TRUE
)
RETURNS TABLE (accept BOOLEAN, level TEXT, witness JSONB)
LANGUAGE plpgsql AS $$
DECLARE
    v_tape   TEXT[];
    v_n      INTEGER;
    v_round  INTEGER := 0;
    v_rounds JSONB[] := '{}';
    v_count  INTEGER;
    v_i      INTEGER;
    v_keep   BOOLEAN;
BEGIN
    v_n := length(p_input);

    -- Guard: all characters must be '0'
    IF v_n > 0 AND p_input != repeat('0', v_n) THEN
        RETURN QUERY SELECT FALSE, 'tm'::TEXT,
            jsonb_build_object('reason', 'input contains symbols other than 0');
        RETURN;
    END IF;

    IF v_n = 0 THEN
        RETURN QUERY SELECT FALSE, 'tm'::TEXT,
            jsonb_build_object('reason', 'empty string not in language (smallest member is "0")');
        RETURN;
    END IF;

    -- Build tape: ['0','0',...,'0']
    v_tape := ARRAY(SELECT '0' FROM generate_series(1, v_n));

    LOOP
        IF v_round >= p_budget THEN
            RETURN QUERY SELECT FALSE, 'tm'::TEXT,
                jsonb_build_object('reason', format('step budget %s exhausted', p_budget),
                                   'rounds', v_round, 'tape', v_tape,
                                   'sweeps', to_jsonb(v_rounds));
            RETURN;
        END IF;

        v_round := v_round + 1;

        -- Count remaining '0's
        SELECT count(*) INTO v_count
        FROM unnest(v_tape) AS t(sym) WHERE t.sym = '0';

        IF p_trace THEN
            v_rounds := array_append(v_rounds,
                jsonb_build_object('round', v_round, 'zeros', v_count, 'tape', v_tape));
        END IF;

        -- Accept when exactly one '0' remains
        IF v_count = 1 THEN
            RETURN QUERY SELECT TRUE, 'tm'::TEXT,
                jsonb_build_object('rounds', v_round, 'final_tape', v_tape,
                                   'sweeps', to_jsonb(v_rounds));
            RETURN;
        END IF;

        -- Reject when odd count > 1 (can never be a power of 2)
        IF v_count % 2 != 0 THEN
            RETURN QUERY SELECT FALSE, 'tm'::TEXT,
                jsonb_build_object('reason',
                    format('odd zero count %s in round %s — not a power of 2', v_count, v_round),
                    'rounds', v_round, 'tape', v_tape, 'sweeps', to_jsonb(v_rounds));
            RETURN;
        END IF;

        -- Cross off every other '0': keep 1st, cross 2nd, keep 3rd, ...
        v_keep := TRUE;
        FOR v_i IN 1..array_length(v_tape, 1) LOOP
            IF v_tape[v_i] = '0' THEN
                IF v_keep THEN
                    v_keep := FALSE;    -- next '0' gets crossed
                ELSE
                    v_tape[v_i] := 'X';
                    v_keep := TRUE;     -- next '0' is kept
                END IF;
            END IF;
        END LOOP;
    END LOOP;
END;
$$;

COMMENT ON FUNCTION chomsky.run_tm(TEXT, INTEGER, BOOLEAN) IS
    'Type-0 TM for 0^(2^k) (k>=0). Tape alphabet {0,X}. '
    'O(log n) rounds: each round crosses off every other 0. '
    'Returns (accept, level=''tm'', witness) or UNKNOWN if step budget exhausted. '
    'Budget defaults to 64 rounds (~2^64 input length).';

-- ---------------------------------------------------------------------------
-- Orchestrator: chomsky.classify(input)
-- Runs all four simulators. Returns one row per machine.
-- The "tightest" level (highest type number) that accepts is the natural class.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION chomsky.classify(p_input TEXT)
RETURNS TABLE (
    chomsky_type INTEGER,
    machine      TEXT,
    accept       BOOLEAN,
    witness      JSONB
)
LANGUAGE plpgsql AS $$
DECLARE
    r RECORD;
BEGIN
    -- Type 3: DFA (a*b*)
    SELECT d.accept, d.witness INTO r FROM chomsky.run_dfa(p_input, TRUE) d;
    RETURN QUERY SELECT 3, 'dfa'::TEXT, r.accept, r.witness;

    -- Type 2: PDA (a^n b^n)
    SELECT p.accept, p.witness INTO r FROM chomsky.run_pda(p_input, TRUE) p;
    RETURN QUERY SELECT 2, 'pda'::TEXT, r.accept, r.witness;

    -- Type 1: LBA (a^n b^n c^n)
    SELECT l.accept, l.witness INTO r FROM chomsky.run_lba(p_input, TRUE) l;
    RETURN QUERY SELECT 1, 'lba'::TEXT, r.accept, r.witness;

    -- Type 0: TM (0^(2^k))
    SELECT t.accept, t.witness INTO r FROM chomsky.run_tm(p_input) t;
    RETURN QUERY SELECT 0, 'tm'::TEXT, r.accept, r.witness;
END;
$$;

COMMENT ON FUNCTION chomsky.classify(TEXT) IS
    'Run all four Chomsky-level simulators on p_input. '
    'Returns four rows: DFA (type 3), PDA (type 2), LBA (type 1), TM (type 0). '
    'Each row: (chomsky_type, machine, accept, witness). '
    'The highest accepting type is the tightest Chomsky class for the input.';
