-- =============================================================================
-- 91_sequence_cache.sql
-- Persistent pre-computed sequence cache with PostgreSQL NOTIFY callbacks.
--
-- Architecture
-- ------------
-- LLM agents are stateless: every API call starts from scratch.
-- The sequence cache is the opposite — a growing, persistent memory that
-- any stateless call can query at near-zero DB cost.
--
-- First call  : nerode.build_sequence_cache() computes + stores + notifies.
-- Nth call    : nerode.query_sequence_cache()  returns the stored result
--               instantly (single indexed lookup).
--
-- Callback protocol
-- -----------------
-- After a build completes, the function issues:
--
--   NOTIFY 'nerode_sequence_ready',
--          '{"seq_key":"…","id":N,"build_ms":M,"mode":"…"}'
--
-- Any listener that issued LISTEN nerode_sequence_ready will receive this
-- notification on its next network read — enabling agent-side callbacks
-- without polling.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- Cache table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS nerode.sequence_cache (
    id            BIGSERIAL    PRIMARY KEY,
    seq_key       TEXT         NOT NULL UNIQUE,  -- e.g. "accept_quad:60" or "lcm_accept:180"
    automaton_ids BIGINT[]     NOT NULL,
    length        INT          NOT NULL,
    mode          TEXT         NOT NULL,         -- 'parallel_accept' | 'accepting_positions'
    result        JSONB        NOT NULL,         -- the pre-computed sequence
    build_ms      FLOAT,
    built_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sequence_cache_key
    ON nerode.sequence_cache (seq_key);


-- ---------------------------------------------------------------------------
-- nerode.build_sequence_cache
--
-- Build (or rebuild) a sequence and persist it.
-- Issues NOTIFY 'nerode_sequence_ready' on completion.
--
-- p_seq_key       : human-readable name, e.g. "accept_quad:60"
-- p_automaton_ids : automata to run (pass ARRAY[]::BIGINT[] for store mode)
-- p_length        : number of steps
-- p_mode          : 'parallel_accept' → result is list of [0/1,...] per step
--                   'accepting_positions' → result is list of accepting steps
--                   'store' → caller supplies pre-computed result via p_result;
--                             the DFA walk is skipped entirely
-- p_symbol        : transition symbol (default 'a')
-- p_force_rebuild : if TRUE, drop existing cache entry and recompute
-- p_result        : pre-computed result to store (used only when p_mode='store')
--
-- Returns the cache row id.
-- ---------------------------------------------------------------------------

-- Drop the old 6-param signature if it exists (was superseded by adding p_result).
-- CREATE OR REPLACE with a different param count adds an overload instead of
-- replacing, which causes AmbiguousFunction errors on 4-arg calls.
DROP FUNCTION IF EXISTS nerode.build_sequence_cache(text, bigint[], int, text, text, boolean);

CREATE OR REPLACE FUNCTION nerode.build_sequence_cache(
    p_seq_key       TEXT,
    p_automaton_ids BIGINT[],
    p_length        INT,
    p_mode          TEXT     DEFAULT 'parallel_accept',
    p_symbol        TEXT     DEFAULT 'a',
    p_force_rebuild BOOLEAN  DEFAULT FALSE,
    p_result        JSONB    DEFAULT NULL
)
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE
    v_id       BIGINT;
    v_result   JSONB;
    v_start    TIMESTAMPTZ := clock_timestamp();
    v_ms       FLOAT;
    v_payload  TEXT;
BEGIN
    -- Return existing cache entry unless forced
    IF NOT p_force_rebuild THEN
        SELECT id INTO v_id
        FROM nerode.sequence_cache
        WHERE seq_key = p_seq_key;
        IF FOUND THEN
            RETURN v_id;
        END IF;
    ELSE
        DELETE FROM nerode.sequence_cache WHERE seq_key = p_seq_key;
    END IF;

    -- Compute the sequence
    IF p_mode = 'parallel_accept' THEN
        -- Returns list of {step, accepts:[0/1,...]} in automaton_ids order
        SELECT jsonb_agg(
            jsonb_build_object(
                'step',    pr.step,
                'accepts', (
                    SELECT jsonb_agg(
                        CASE WHEN (pr.accept_vector->>(a_id::TEXT))::BOOLEAN
                             THEN 1 ELSE 0 END
                        ORDER BY ord
                    )
                    FROM unnest(p_automaton_ids) WITH ORDINALITY AS t(a_id, ord)
                )
            ) ORDER BY pr.step
        )
        INTO v_result
        FROM nerode.parallel_run(p_automaton_ids, p_length, p_symbol) pr;

    ELSIF p_mode = 'accepting_positions' THEN
        -- Single automaton: returns sorted list of accepting steps
        SELECT to_jsonb(
            nerode.accepting_positions(p_automaton_ids[1], p_length, p_symbol)
        )
        INTO v_result;

    ELSIF p_mode = 'store' THEN
        -- Caller supplies a pre-computed result directly; skip the DFA walk.
        v_result := p_result;

    ELSE
        RAISE EXCEPTION 'Unknown mode: %. Use ''parallel_accept'', ''accepting_positions'', or ''store''.', p_mode;
    END IF;

    v_ms := EXTRACT(EPOCH FROM (clock_timestamp() - v_start)) * 1000.0;

    -- Persist
    INSERT INTO nerode.sequence_cache (seq_key, automaton_ids, length, mode, result, build_ms)
    VALUES (p_seq_key, p_automaton_ids, p_length, p_mode, v_result, v_ms)
    ON CONFLICT (seq_key) DO UPDATE
        SET result     = EXCLUDED.result,
            build_ms   = EXCLUDED.build_ms,
            built_at   = now()
    RETURNING id INTO v_id;

    -- Notify any listeners
    v_payload := json_build_object(
        'seq_key',  p_seq_key,
        'id',       v_id,
        'mode',     p_mode,
        'length',   p_length,
        'build_ms', ROUND(v_ms::NUMERIC, 2)
    )::TEXT;

    PERFORM pg_notify('nerode_sequence_ready', v_payload);

    RETURN v_id;
END;
$$;


-- ---------------------------------------------------------------------------
-- nerode.query_sequence_cache
--
-- Read a cached sequence.  Returns NULL if not yet built.
-- This is the "instant cache hit" path — a single indexed lookup.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.query_sequence_cache(p_seq_key TEXT)
RETURNS JSONB
LANGUAGE sql STABLE AS $$
    SELECT result
    FROM   nerode.sequence_cache
    WHERE  seq_key = p_seq_key;
$$;


-- ---------------------------------------------------------------------------
-- nerode.cache_status
--
-- Summary view: what's in the cache, how long each build took.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW nerode.cache_status AS
SELECT
    id,
    seq_key,
    mode,
    length,
    array_length(automaton_ids, 1) AS dfa_count,
    jsonb_array_length(CASE jsonb_typeof(result) WHEN 'array' THEN result ELSE 'null'::jsonb END)
        AS result_terms,
    ROUND(build_ms::NUMERIC, 1)    AS build_ms,
    built_at
FROM nerode.sequence_cache
ORDER BY built_at DESC;
