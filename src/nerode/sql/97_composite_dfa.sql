-- =============================================================================
-- 97_composite_dfa.sql
-- Cross-alphabet composite DFAs via paired-alphabet projection + product.
--
-- Problem: nerode.product() requires both DFAs to share an alphabet.
-- The cybernetic DFAs span two alphabets:
--   metric   {U,D,S}     — metric direction
--   control  {A,R,_}     — action/response/idle
--
-- Solution: build a *paired* alphabet metric_x_control with 9 symbols,
-- one per (metric, control) pair:
--
--   UA  UR  U_
--   DA  DR  D_
--   SA  SR  S_
--
-- Each component DFA is *projected* onto this alphabet: a transition that
-- consumed symbol X becomes transitions for every paired symbol whose
-- relevant component is X.  Once both are on the same alphabet, product()
-- builds the intersection.
--
-- Functions:
--
--   nerode.project_to_paired(dfa_id, paired_alpha_name, component INT)
--       component 1 = first char of each paired symbol (metric side)
--       component 2 = second char                      (control side)
--       Returns new dfa_id.
--
--   nerode.ensure_composite_cybernetic(name, dfa1, comp1, dfa2, comp2, ...)
--       Idempotent: if `name` already exists returns its id.
--       Projects both component DFAs, intersects, renames result.
--
-- Built on first apply:
--
--   dead_time_5_x_metric_oscillate
--       Pattern: A_{5,} (dead-time 5) AND (UD){3,} (oscillation)
--       Meaning: the metric has been oscillating while an action has gone
--                unanswered for 5+ steps — "oscillating and unresponsive"
--
-- Idempotent — safe to re-apply.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. Paired alphabet
-- ---------------------------------------------------------------------------

INSERT INTO nerode.alphabets (name, symbols)
VALUES ('metric_x_control', ARRAY[
    'UA', 'UR', 'U_',
    'DA', 'DR', 'D_',
    'SA', 'SR', 'S_'
])
ON CONFLICT (name) DO NOTHING;


-- ---------------------------------------------------------------------------
-- 2. nerode.project_to_paired(dfa_id, paired_alpha_name, component)
-- ---------------------------------------------------------------------------
-- Build a new DFA over the paired alphabet by expanding each original
-- transition (q, x, q') into all transitions (q, xy, q') where the
-- component-th character of the paired symbol xy equals x.
--
-- The result DFA is complete iff the source DFA is complete over its
-- single-character alphabet — which all eight cybernetic DFAs are.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.project_to_paired(
    p_dfa_id      BIGINT,
    p_paired_name TEXT,
    p_component   INT       -- 1 = first char (metric), 2 = second char (control)
)
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE
    v_src         nerode.automata%ROWTYPE;
    v_new_id      BIGINT;
    v_paired_id   BIGINT;
    v_paired_syms TEXT[];
    v_paired_sym  TEXT;
    v_from        INT;
    v_orig_sym    TEXT;
    v_to          INT;
BEGIN
    SELECT * INTO v_src FROM nerode.automata WHERE id = p_dfa_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'project_to_paired: DFA % not found', p_dfa_id;
    END IF;

    SELECT id, symbols
    INTO   v_paired_id, v_paired_syms
    FROM   nerode.alphabets WHERE name = p_paired_name;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'project_to_paired: alphabet ''%'' not found — '
                        'apply 97_composite_dfa.sql first', p_paired_name;
    END IF;

    IF p_component NOT IN (1, 2) THEN
        RAISE EXCEPTION 'project_to_paired: component must be 1 or 2, got %', p_component;
    END IF;

    INSERT INTO nerode.automata (name, type, alphabet_id, state_count, provenance)
    VALUES (
        v_src.name || '_proj' || p_component,
        'DFA',
        v_paired_id,
        v_src.state_count,
        jsonb_build_object(
            'source',          '97_composite_dfa',
            'operation',       'project_to_paired',
            'source_dfa_id',   p_dfa_id,
            'source_dfa_name', v_src.name,
            'paired_alphabet', p_paired_name,
            'component',       p_component
        )
    )
    RETURNING id INTO v_new_id;

    -- Copy states verbatim
    INSERT INTO nerode.states (automaton_id, state_id, label, is_initial, is_accepting)
    SELECT v_new_id, state_id, label, is_initial, is_accepting
    FROM   nerode.states WHERE automaton_id = p_dfa_id;

    -- Expand transitions: each (q, x, q') → all (q, xy, q') where component of xy = x
    FOR v_from, v_orig_sym, v_to IN
        SELECT from_state, symbol, to_state
        FROM   nerode.transitions WHERE automaton_id = p_dfa_id
    LOOP
        FOREACH v_paired_sym IN ARRAY v_paired_syms LOOP
            IF (p_component = 1 AND left(v_paired_sym, 1) = v_orig_sym)
            OR (p_component = 2 AND right(v_paired_sym, 1) = v_orig_sym)
            THEN
                INSERT INTO nerode.transitions
                    (automaton_id, from_state, symbol, to_state)
                VALUES (v_new_id, v_from, v_paired_sym, v_to)
                ON CONFLICT DO NOTHING;
            END IF;
        END LOOP;
    END LOOP;

    RETURN v_new_id;
END;
$$;

COMMENT ON FUNCTION nerode.project_to_paired(BIGINT, TEXT, INT) IS
    'Project a DFA onto a paired alphabet. component=1 maps the first character of '
    'each paired symbol; component=2 maps the second character. Used to lift '
    'single-alphabet DFAs onto a product alphabet before nerode.product().';


-- ---------------------------------------------------------------------------
-- 3. nerode.ensure_composite_cybernetic(...)
-- ---------------------------------------------------------------------------
-- Idempotent: if a DFA named p_name already exists, return its id.
-- Otherwise: project both component DFAs onto the paired alphabet, compute
-- their intersection, rename the result.  Internal projection DFAs are
-- renamed to "_internal" to keep the namespace tidy.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.ensure_composite_cybernetic(
    p_name        TEXT,
    p_dfa1_name   TEXT,
    p_comp1       INT,           -- which component of paired symbol is dfa1's alphabet
    p_dfa2_name   TEXT,
    p_comp2       INT,
    p_paired_name TEXT DEFAULT 'metric_x_control',
    p_op          TEXT DEFAULT 'intersection'
)
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE
    v_id    BIGINT;
    v_dfa1  BIGINT;
    v_dfa2  BIGINT;
    v_proj1 BIGINT;
    v_proj2 BIGINT;
    v_prod  BIGINT;
BEGIN
    SELECT id INTO v_id FROM nerode.automata WHERE name = p_name;
    IF FOUND THEN RETURN v_id; END IF;

    SELECT id INTO v_dfa1 FROM nerode.automata WHERE name = p_dfa1_name;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'ensure_composite_cybernetic: DFA ''%'' not found', p_dfa1_name;
    END IF;
    SELECT id INTO v_dfa2 FROM nerode.automata WHERE name = p_dfa2_name;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'ensure_composite_cybernetic: DFA ''%'' not found', p_dfa2_name;
    END IF;

    v_proj1 := nerode.project_to_paired(v_dfa1, p_paired_name, p_comp1);
    v_proj2 := nerode.project_to_paired(v_dfa2, p_paired_name, p_comp2);
    v_prod  := nerode.product(v_proj1, v_proj2, p_op);

    -- Rename product to the requested name
    UPDATE nerode.automata SET name = p_name WHERE id = v_prod;

    -- Mark internal projections to avoid namespace pollution
    UPDATE nerode.automata SET name = p_name || '_proj1_internal' WHERE id = v_proj1;
    UPDATE nerode.automata SET name = p_name || '_proj2_internal' WHERE id = v_proj2;

    RETURN v_prod;
END;
$$;

COMMENT ON FUNCTION nerode.ensure_composite_cybernetic(TEXT, TEXT, INT, TEXT, INT, TEXT, TEXT) IS
    'Build (or return) a named composite DFA by projecting two component DFAs onto a '
    'paired alphabet and intersecting them. Idempotent. Internal projections are '
    'retained under <name>_projN_internal for inspection.';


-- ---------------------------------------------------------------------------
-- 3b. nerode.run_to_state_arr(dfa_id, symbols TEXT[])
-- ---------------------------------------------------------------------------
-- Variant of run_to_state that consumes one *element* per step rather than
-- one *character*.  Required for alphabets with multi-character symbols
-- (e.g. metric_x_control paired alphabet with symbols like 'UA', 'D_', etc).
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.run_to_state_arr(
    p_automaton_id BIGINT,
    p_symbols      TEXT[]
)
RETURNS INT
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_state INT;
    v_next  INT;
    v_sym   TEXT;
BEGIN
    SELECT state_id INTO v_state
    FROM   nerode.states
    WHERE  automaton_id = p_automaton_id AND is_initial = TRUE
    LIMIT  1;
    IF v_state IS NULL THEN RETURN NULL; END IF;

    FOREACH v_sym IN ARRAY p_symbols LOOP
        SELECT to_state INTO v_next
        FROM   nerode.transitions
        WHERE  automaton_id = p_automaton_id
          AND  from_state   = v_state
          AND  symbol       = v_sym;
        IF v_next IS NULL THEN RETURN NULL; END IF;
        v_state := v_next;
    END LOOP;

    RETURN v_state;
END;
$$;

COMMENT ON FUNCTION nerode.run_to_state_arr(BIGINT, TEXT[]) IS
    'Like run_to_state but consumes one array element per step. '
    'Use with multi-character alphabets (e.g. paired metric_x_control symbols).';


-- ---------------------------------------------------------------------------
-- 4. Build the specific composite
-- ---------------------------------------------------------------------------
-- dead_time_5_x_metric_oscillate
--
--   Component 1 (metric, char 1): metric_oscillate  — pattern (UD){3,}
--   Component 2 (control, char 2): dead_time_5       — pattern A_{5,}
--
-- Combined: oscillating metric behavior while an action has gone unanswered
-- for 5+ steps.  A signal that a control system is both unstable and deaf.
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    PERFORM nerode.ensure_composite_cybernetic(
        'dead_time_5_x_metric_oscillate',
        'metric_oscillate', 1,
        'dead_time_5',      2
    );
END;
$$;
