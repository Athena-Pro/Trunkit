-- =============================================================================
-- 90_sequence.sql
-- Sequence generation from DFA walks.
--
-- Functions:
--   nerode.run_sequence(automaton_id, length, symbol)
--       Walk a single DFA for `length` steps.
--   nerode.parallel_run(automaton_ids[], length, symbol)
--       Walk multiple DFAs in parallel, return joint state+accept vectors.
--   nerode.accepting_positions(automaton_id, length, symbol)
--       Return the sorted list of steps where the DFA is in an accepting state.
--
-- All three assume the automaton has a deterministic transition for `symbol`
-- from every reachable state (i.e. it is complete/total on that symbol).
-- Cycle automata in the corpus satisfy this for symbol='a'.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- nerode.run_sequence
--
-- Walk the DFA one step at a time, reading `p_symbol` at every step.
-- Step 0  = initial state (before consuming any symbol).
-- Step k  = state reached after reading p_symbol exactly k times.
--
-- Returns p_length rows: (step 0 .. step p_length-1).
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.run_sequence(
    p_automaton_id BIGINT,
    p_length       INT,
    p_symbol       TEXT DEFAULT 'a'
)
RETURNS TABLE (step INT, state_id INT, is_accepting BOOLEAN)
LANGUAGE sql STABLE AS $$
    WITH RECURSIVE walk(step, sid) AS (
        -- Base case: initial state at step 0.
        SELECT 0, s.state_id
        FROM   nerode.states s
        WHERE  s.automaton_id = p_automaton_id
          AND  s.is_initial   = TRUE

        UNION ALL

        -- Recursive case: follow the transition for p_symbol.
        SELECT w.step + 1,
               t.to_state
        FROM   walk w
        JOIN   nerode.transitions t
               ON  t.automaton_id = p_automaton_id
               AND t.from_state   = w.sid
               AND t.symbol       = p_symbol
        WHERE  w.step < p_length - 1
    )
    SELECT  w.step,
            w.sid        AS state_id,
            s.is_accepting
    FROM    walk w
    JOIN    nerode.states s
            ON  s.automaton_id = p_automaton_id
            AND s.state_id     = w.sid
    WHERE   w.step < p_length   -- guard: exclude step 0 when p_length = 0
    ORDER BY w.step;
$$;


-- ---------------------------------------------------------------------------
-- nerode.parallel_run
--
-- Run multiple DFAs in parallel on the same unary input sequence.
-- Returns one row per step with two JSONB dicts:
--   state_vector  : { "<automaton_id>" : state_id, ... }
--   accept_vector : { "<automaton_id>" : true/false, ... }
--
-- Keys are automaton IDs cast to TEXT so they survive JSON round-trips.
-- Order of keys inside the JSONB is by automaton_id (ascending).
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.parallel_run(
    p_automaton_ids BIGINT[],
    p_length        INT,
    p_symbol        TEXT DEFAULT 'a'
)
RETURNS TABLE (step INT, state_vector JSONB, accept_vector JSONB)
LANGUAGE sql STABLE AS $$
    WITH per_auto AS (
        SELECT a_id,
               rs.step,
               rs.state_id,
               rs.is_accepting
        FROM   unnest(p_automaton_ids) AS a_id
        CROSS  JOIN LATERAL nerode.run_sequence(a_id, p_length, p_symbol) rs
    )
    SELECT  step,
            jsonb_object_agg(a_id::TEXT, state_id)    AS state_vector,
            jsonb_object_agg(a_id::TEXT, is_accepting) AS accept_vector
    FROM    per_auto
    GROUP   BY step
    ORDER   BY step;
$$;


-- ---------------------------------------------------------------------------
-- nerode.accepting_positions
--
-- Return a sorted BIGINT[] of the steps in [0, p_length) where the DFA
-- is in an accepting state.
--
-- Example: cycle_4 automaton for 20 steps → {0,4,8,12,16}.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.accepting_positions(
    p_automaton_id BIGINT,
    p_length       INT,
    p_symbol       TEXT DEFAULT 'a'
)
RETURNS BIGINT[]
LANGUAGE sql STABLE AS $$
    SELECT array_agg(step::BIGINT ORDER BY step)
    FROM   nerode.run_sequence(p_automaton_id, p_length, p_symbol)
    WHERE  is_accepting = TRUE;
$$;
