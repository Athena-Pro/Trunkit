-- =============================================================================
--  nerode — Step A4: Exact extremal payoff analysis  [Phase 4]
--
--  Implements Theorem 3 of arXiv:2606.11223: exact best-case / worst-case payoff
--  over the scenario-restricted product (A3), with an interpretable WITNESS
--  finance word realizing the extremum.
--
--  Two ingredients:
--   (1) Per-transition optimizer. Each step's data value dᵢ is chosen freely in
--       the admissible interval, and steps are INDEPENDENT (each has its own dᵢ)
--       and accumulate additively (⊗ = +). So the global optimum decomposes:
--       optimise each transition's payoff over its interval, then optimise the
--       PATH. For the linear finance semiring the payoff is piecewise-linear in
--       d, so its extremum on an interval is attained at an endpoint or an
--       internal breakpoint (a guard boundary, or an ⊕ crossover of affine
--       pieces). payoff_opt() evaluates exactly that finite candidate set.
--   (2) Max-plus DP over the product unrolled to a horizon. best = longest path,
--       worst = shortest path (worst-case sound for deterministic products, per
--       the paper). Backpointers reconstruct the witness word, exactly like the
--       predecessor chain in nerode.equivalent (04_product.sql).
--
--  Depends on: A0..A3, 01_schema. Idempotent.
--  NOTE: starter file — not yet executed against a live database. Exact for
--  piecewise-linear payoffs; admissible intervals are assumed bounded above
--  (an unbounded interval with an increasing payoff yields +∞ and is flagged).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- payoff_affine(expr) → [slope, intercept]  (NULL if not affine in d)
--   const s → [0,s];  bind s → [s,0];  otimes affine⊗affine → sum;  else NULL.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.payoff_affine(p JSONB)
RETURNS NUMERIC[] AS $$
DECLARE a NUMERIC[]; b NUMERIC[];
BEGIN
    IF p ? 'const' THEN RETURN ARRAY[0, (p->>'const')::numeric]; END IF;
    IF p ? 'bind'  THEN RETURN ARRAY[(p->>'bind')::numeric, 0];  END IF;
    IF p ? 'otimes' THEN
        a := nerode.payoff_affine(p->'otimes'->0);
        b := nerode.payoff_affine(p->'otimes'->1);
        IF a IS NULL OR b IS NULL THEN RETURN NULL; END IF;
        RETURN ARRAY[a[1]+b[1], a[2]+b[2]];
    END IF;
    IF p ? 'guard' THEN RETURN nerode.payoff_affine(p->'guard'->'then'); END IF;
    RETURN NULL;  -- oplus (or unknown) ⇒ not globally affine
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- ---------------------------------------------------------------------------
-- payoff_breakpoints(expr) → NUMERIC[]  candidate interior optima:
--   guard boundaries, and ⊕ crossovers of affine children.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.payoff_breakpoints(p JSONB)
RETURNS NUMERIC[] AS $$
DECLARE
    v_out NUMERIC[] := ARRAY[]::NUMERIC[];
    v_iv  nerode.interval;
    a NUMERIC[]; b NUMERIC[]; v_x NUMERIC;
BEGIN
    IF p ? 'const' OR p ? 'bind' THEN
        RETURN v_out;
    ELSIF p ? 'guard' THEN
        v_iv := nerode.iv_from_json(p->'guard'->'iv');
        IF NOT nerode.iv_is_empty(v_iv) THEN
            IF v_iv.lo IS NOT NULL THEN v_out := v_out || v_iv.lo; END IF;
            IF v_iv.hi IS NOT NULL THEN v_out := v_out || v_iv.hi; END IF;
        END IF;
        RETURN v_out || nerode.payoff_breakpoints(p->'guard'->'then');
    ELSIF p ? 'otimes' THEN
        RETURN nerode.payoff_breakpoints(p->'otimes'->0)
             || nerode.payoff_breakpoints(p->'otimes'->1);
    ELSIF p ? 'oplus' THEN
        v_out := nerode.payoff_breakpoints(p->'oplus'->0)
               || nerode.payoff_breakpoints(p->'oplus'->1);
        a := nerode.payoff_affine(p->'oplus'->0);
        b := nerode.payoff_affine(p->'oplus'->1);
        IF a IS NOT NULL AND b IS NOT NULL AND a[1] <> b[1] THEN
            v_x := (b[2] - a[2]) / (a[1] - b[1]);   -- slope_a·x+int_a = slope_b·x+int_b
            v_out := v_out || v_x;
        END IF;
        RETURN v_out;
    END IF;
    RETURN v_out;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- ---------------------------------------------------------------------------
-- payoff_opt(expr, sense) → {"val": NUMERIC, "d": NUMERIC}  (val NULL ⇒ −∞)
--   Optimum of the (folded) transition payoff over its feasible domain, with
--   the argmax/argmin data value. sense ∈ {'best','worst'}.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.payoff_opt(p JSONB, p_sense TEXT)
RETURNS JSONB AS $$
DECLARE
    v_cands  NUMERIC[];
    v_c      NUMERIC;
    v_v      NUMERIC;
    v_best_v NUMERIC := NULL;
    v_best_d NUMERIC := NULL;
BEGIN
    -- candidate data values: D's lower bound 0, plus all interior breakpoints.
    v_cands := ARRAY[0::numeric] || nerode.payoff_breakpoints(p);

    FOREACH v_c IN ARRAY v_cands LOOP
        IF v_c < 0 THEN CONTINUE; END IF;            -- D = ℝ≥0
        v_v := nerode.payoff_eval(p, v_c);
        IF v_v IS NULL THEN CONTINUE; END IF;         -- infeasible at this d
        IF v_best_v IS NULL
           OR (p_sense = 'best'  AND v_v > v_best_v)
           OR (p_sense = 'worst' AND v_v < v_best_v) THEN
            v_best_v := v_v;
            v_best_d := v_c;
        END IF;
    END LOOP;

    RETURN jsonb_build_object('val', v_best_v, 'd', v_best_d);
END;
$$ LANGUAGE plpgsql IMMUTABLE;

COMMENT ON FUNCTION nerode.payoff_opt(JSONB, TEXT) IS
    'Exact extremum (best|worst) of a piecewise-linear local payoff expression '
    'over its feasible domain, returning {val, d}. val NULL ⇒ −∞ (infeasible).';

-- ---------------------------------------------------------------------------
-- nerode.extremal(product, sense, horizon) → (value NUMERIC, witness JSONB)
--   Max-plus DP over the product unrolled to <= horizon steps.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.extremal(
    p_product BIGINT,
    p_sense   TEXT,                -- 'best' | 'worst'
    p_horizon INTEGER DEFAULT 64
)
RETURNS TABLE (value NUMERIC, witness JSONB) AS $$
DECLARE
    v_a        nerode.automata%ROWTYPE;
    v_k        INTEGER;
    v_opt      JSONB;
    v_w        NUMERIC;
    v_nv       NUMERIC;
    v_better   BOOLEAN;
    v_best_total NUMERIC := NULL;
    v_best_step  INTEGER := NULL;
    v_best_state INTEGER := NULL;
    v_fin      NUMERIC;
    v_hist     JSONB := '[]'::jsonb;
    v_cur_s    INTEGER;
    v_cur_k    INTEGER;
    v_row      RECORD;
BEGIN
    SELECT * INTO v_a FROM nerode.automata WHERE id = p_product;
    IF NOT FOUND OR v_a.type <> 'WFFA' THEN
        RAISE EXCEPTION 'extremal: automaton % is not a WFFA', p_product;
    END IF;
    IF p_sense NOT IN ('best','worst') THEN
        RAISE EXCEPTION 'extremal: sense must be best or worst, got %', p_sense;
    END IF;

    -- DP table:  one row per (step, state) holding the optimal accumulated value
    -- and the backpointer (predecessor state, symbol, chosen data value).
    CREATE TEMP TABLE IF NOT EXISTS _nerode_dp (
        step INTEGER, state INTEGER, val NUMERIC,
        from_state INTEGER, sym TEXT, d NUMERIC,
        PRIMARY KEY (step, state)
    ) ON COMMIT DROP;
    TRUNCATE _nerode_dp;

    -- Layer 0: initial states with their wt_I.
    INSERT INTO _nerode_dp (step, state, val, from_state, sym, d)
    SELECT 0, state_id, COALESCE(weight, 0), NULL, NULL, NULL
    FROM nerode.wffa_terminal
    WHERE automaton_id = p_product AND role = 'initial';

    -- Relaxation over the horizon.
    FOR v_k IN 0 .. p_horizon - 1 LOOP
        FOR v_row IN
            SELECT dp.state AS s, dp.val AS val,
                   t.symbol AS sym, t.to_state AS s2, w.payoff AS payoff
            FROM _nerode_dp dp
            JOIN nerode.transitions t ON t.automaton_id = p_product AND t.from_state = dp.state
            JOIN nerode.wffa_weight  w ON w.transition_id = t.id
            WHERE dp.step = v_k
        LOOP
            v_opt := nerode.payoff_opt(v_row.payoff, p_sense);
            IF v_opt->>'val' IS NULL THEN CONTINUE; END IF;     -- −∞ edge
            v_w  := (v_opt->>'val')::numeric;
            v_nv := v_row.val + v_w;

            -- upsert keeping the extremal value at (k+1, s2)
            IF EXISTS (SELECT 1 FROM _nerode_dp WHERE step = v_k+1 AND state = v_row.s2) THEN
                SELECT (p_sense = 'best'  AND v_nv > val)
                    OR (p_sense = 'worst' AND v_nv < val)
                  INTO v_better
                FROM _nerode_dp WHERE step = v_k+1 AND state = v_row.s2;
                IF v_better THEN
                    UPDATE _nerode_dp
                    SET val = v_nv, from_state = v_row.s, sym = v_row.sym, d = (v_opt->>'d')::numeric
                    WHERE step = v_k+1 AND state = v_row.s2;
                END IF;
            ELSE
                INSERT INTO _nerode_dp (step, state, val, from_state, sym, d)
                VALUES (v_k+1, v_row.s2, v_nv, v_row.s, v_row.sym, (v_opt->>'d')::numeric);
            END IF;
        END LOOP;
    END LOOP;

    -- Terminal selection: best/worst of (val + wt_F) over all final states and
    -- all step counts <= horizon (variable-length words: early autocall etc.).
    FOR v_row IN
        SELECT dp.step AS k, dp.state AS s, dp.val + COALESCE(wt.weight,0) AS total
        FROM _nerode_dp dp
        JOIN nerode.wffa_terminal wt
          ON wt.automaton_id = p_product AND wt.role = 'final' AND wt.state_id = dp.state
    LOOP
        IF v_best_total IS NULL
           OR (p_sense = 'best'  AND v_row.total > v_best_total)
           OR (p_sense = 'worst' AND v_row.total < v_best_total) THEN
            v_best_total := v_row.total;
            v_best_step  := v_row.k;
            v_best_state := v_row.s;
        END IF;
    END LOOP;

    IF v_best_total IS NULL THEN
        -- no accepting admissible run within the horizon
        RETURN QUERY SELECT NULL::numeric,
            jsonb_build_object('kind','payoff_trace','sense',p_sense,
                'value', NULL, 'horizon', p_horizon, 'feasible', FALSE,
                'note','no admissible accepting run within horizon');
        RETURN;
    END IF;

    -- Reconstruct the witness word by walking backpointers to step 0.
    v_cur_s := v_best_state;
    v_cur_k := v_best_step;
    WHILE v_cur_k > 0 LOOP
        SELECT * INTO v_row FROM _nerode_dp WHERE step = v_cur_k AND state = v_cur_s;
        EXIT WHEN v_row.from_state IS NULL;
        v_hist  := jsonb_build_array(jsonb_build_array(v_row.sym, v_row.d)) || v_hist;
        v_cur_s := v_row.from_state;
        v_cur_k := v_cur_k - 1;
    END LOOP;

    RETURN QUERY SELECT v_best_total,
        jsonb_build_object(
            'kind','payoff_trace', 'sense', p_sense,
            'value', v_best_total, 'horizon', p_horizon, 'feasible', TRUE,
            'length', v_best_step, 'history', v_hist,
            'product_id', p_product
        );
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.extremal(BIGINT, TEXT, INTEGER) IS
    'Exact best/worst-case payoff over a scenario-restricted product WFFA '
    '(arXiv:2606.11223 Thm 3), with a witness finance word. Best = longest path, '
    'worst = shortest path over the max-plus DP; worst-case assumes a '
    'deterministic product. Returns (value, witness JSONB of kind payoff_trace).';
