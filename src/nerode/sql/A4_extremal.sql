-- =============================================================================
--  nerode — Step A4: Exact extremal payoff analysis  [Phase 4]
--
--  Exact finite-horizon best/worst payoff for deterministic weighted automata
--  whose local expressions are affine with interval guards. Other syntax,
--  including nested maxima, yields an explicit unsupported result.
--
--  Two ingredients:
--   (1) Per-transition optimizer. Each step's data value dᵢ is chosen freely in
--       the admissible interval, and steps are INDEPENDENT (each has its own dᵢ)
--       and accumulate additively (⊗ = +). So the global optimum decomposes:
--       optimise each transition's payoff over its interval, then optimise the
--       PATH. An affine expression on a closed bounded interval attains its
--       optimum at an endpoint. Unbounded objectives are flagged explicitly.
--   (2) Max-plus DP over the product unrolled to a horizon. best = longest path,
--       worst = shortest path (worst-case sound for deterministic products, per
--       the paper). Backpointers reconstruct the witness word, exactly like the
--       predecessor chain in nerode.equivalent (04_product.sql).
--
--  Depends on: A0..A3, 01_schema. Idempotent.
--  A realizing word is evidence of attainment. Portable optimality claims
--  additionally need an independently checked bound (see A5).
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
-- payoff_breakpoints(expr) → NUMERIC[]  heuristic candidate breakpoints:
--   guard boundaries, and ⊕ crossovers of affine children. Incomplete for
--   nested maxima; NOT used by the certified optimizer.
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
-- The original breakpoint collector is insufficient for nested maxima: e.g.
-- max(max(d,0),3-d) on [0,4] has an interior minimum at 3/2 which it misses.
-- Until an exact rational upper-envelope checker exists, certify only affine
-- expressions and intersections of their interval guards. Unsupported and
-- unbounded objectives are explicit non-decisions, never finite optima.
CREATE OR REPLACE FUNCTION nerode.payoff_supported_affine(p JSONB)
RETURNS BOOLEAN LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE v_key TEXT; v_guard JSONB; v_iv JSONB;
BEGIN
    IF p IS NULL OR jsonb_typeof(p)<>'object' THEN
        RETURN FALSE;
    END IF;
    IF (SELECT count(*) FROM jsonb_object_keys(p))<>1 THEN RETURN FALSE; END IF;
    SELECT key INTO v_key FROM jsonb_each(p);
    IF v_key IN ('const','bind') THEN
        RETURN jsonb_typeof(p->v_key)='number';
    ELSIF v_key='otimes' THEN
        IF jsonb_typeof(p->v_key)<>'array' OR jsonb_array_length(p->v_key)<>2 THEN
            RETURN FALSE;
        END IF;
        RETURN nerode.payoff_supported_affine(p->v_key->0)
           AND nerode.payoff_supported_affine(p->v_key->1);
    ELSIF v_key='guard' THEN
        v_guard := p->v_key;
        IF jsonb_typeof(v_guard)<>'object' OR NOT v_guard ? 'iv'
           OR NOT v_guard ? 'then' THEN RETURN FALSE; END IF;
        v_iv := v_guard->'iv';
        IF jsonb_typeof(v_iv)<>'object' THEN RETURN FALSE; END IF;
        IF EXISTS (SELECT 1 FROM jsonb_object_keys(v_iv) k WHERE k NOT IN ('lo','hi','empty'))
           OR (v_iv ? 'lo' AND jsonb_typeof(v_iv->'lo')<>'number')
           OR (v_iv ? 'hi' AND (jsonb_typeof(v_iv->'hi')<>'number' OR NOT v_iv ? 'lo'))
           OR (v_iv ? 'empty' AND jsonb_typeof(v_iv->'empty')<>'boolean') THEN
            RETURN FALSE;
        END IF;
        RETURN nerode.payoff_supported_affine(v_guard->'then');
    END IF;
    RETURN FALSE;
END $$;

CREATE OR REPLACE FUNCTION nerode.payoff_affine_domain(p JSONB)
RETURNS nerode.interval LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF p ? 'guard' THEN
        RETURN nerode.iv_meet(nerode.iv_from_json(p->'guard'->'iv'),
                             nerode.payoff_affine_domain(p->'guard'->'then'));
    ELSIF p ? 'otimes' THEN
        RETURN nerode.iv_meet(nerode.payoff_affine_domain(p->'otimes'->0),
                             nerode.payoff_affine_domain(p->'otimes'->1));
    END IF;
    RETURN nerode.iv_full();
END $$;

CREATE OR REPLACE FUNCTION nerode.payoff_opt(p JSONB, p_sense TEXT)
RETURNS JSONB AS $$
DECLARE
    v_cands  NUMERIC[];
    v_c      NUMERIC;
    v_v      NUMERIC;
    v_best_v NUMERIC := NULL;
    v_best_d NUMERIC := NULL;
    v_domain nerode.interval;
    v_affine NUMERIC[];
BEGIN
    IF p_sense IS NULL OR p_sense NOT IN ('best','worst') THEN
        RAISE EXCEPTION 'payoff_opt: sense must be best or worst';
    END IF;
    IF NOT nerode.payoff_supported_affine(p) THEN
        RETURN jsonb_build_object('supported',FALSE,'val',NULL,'d',NULL,
            'reason','only affine payoffs with interval guards are currently certified');
    END IF;
    v_domain := nerode.payoff_affine_domain(p);
    IF nerode.iv_is_empty(v_domain) THEN
        RETURN jsonb_build_object('supported',TRUE,'val',NULL,'d',NULL);
    END IF;
    v_affine := nerode.payoff_affine(p);
    IF v_domain.hi IS NULL AND ((p_sense='best' AND v_affine[1]>0)
                                 OR (p_sense='worst' AND v_affine[1]<0)) THEN
        RETURN jsonb_build_object('supported',FALSE,'unbounded',TRUE,'val',NULL,'d',NULL,
            'reason','objective is unbounded on its admissible interval');
    END IF;
    v_cands := ARRAY[v_domain.lo];
    IF v_domain.hi IS NOT NULL THEN v_cands := v_cands || v_domain.hi; END IF;

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

    RETURN jsonb_build_object('supported',TRUE,'val', v_best_v, 'd', v_best_d);
END;
$$ LANGUAGE plpgsql IMMUTABLE;

COMMENT ON FUNCTION nerode.payoff_opt(JSONB, TEXT) IS
    'Exact extremum of an affine payoff with interval guards; supported=false '
    'for nested maxima, unsupported syntax, or an unbounded objective. '
    'supported=true and val=NULL means the domain is infeasible.';

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
    IF p_sense IS NULL OR p_horizon IS NULL OR p_horizon<0 OR p_horizon>4096 THEN
        RAISE EXCEPTION 'extremal: invalid sense or horizon (supported range 0..4096)';
    END IF;
    IF (SELECT count(*) FROM nerode.states WHERE automaton_id=p_product)>256
       OR (SELECT count(*) FROM nerode.transitions WHERE automaton_id=p_product)>16384
       OR (SELECT count(*) FROM nerode.transitions WHERE automaton_id=p_product)::BIGINT
           *p_horizon>8000000 THEN
        RAISE EXCEPTION 'extremal: resource budget exceeded';
    END IF;
    IF (SELECT count(*) FROM nerode.wffa_terminal WHERE automaton_id=p_product
        AND role='initial')<>1
       OR EXISTS (SELECT 1 FROM nerode.wffa_terminal WHERE automaton_id=p_product
                   AND role='initial' AND weight IS NULL)
       OR EXISTS (SELECT 1 FROM nerode.transitions WHERE automaton_id=p_product
                   GROUP BY from_state,symbol HAVING count(*)>1)
       OR EXISTS (SELECT 1 FROM nerode.transitions t
                   LEFT JOIN nerode.wffa_weight w ON w.transition_id=t.id
                   WHERE t.automaton_id=p_product AND (w.transition_id IS NULL OR t.symbol IS NULL))
       OR EXISTS (SELECT 1 FROM nerode.transitions t
                   JOIN nerode.alphabets a ON a.id=v_a.alphabet_id
                   WHERE t.automaton_id=p_product AND NOT t.symbol=ANY(a.symbols)) THEN
        RETURN QUERY SELECT NULL::NUMERIC,jsonb_build_object('kind','payoff_trace',
            'supported',FALSE,'feasible',NULL,'value',NULL,'horizon',p_horizon,
            'reason','exact extremal replay requires a deterministic weighted automaton');
        RETURN;
    END IF;

    -- Check the complete input domain before DP: an unsupported edge must not
    -- silently disappear and turn a non-decision into an infeasibility claim.
    FOR v_row IN SELECT w.payoff FROM nerode.transitions t
                 JOIN nerode.wffa_weight w ON w.transition_id=t.id
                 WHERE t.automaton_id=p_product LOOP
        v_opt := nerode.payoff_opt(v_row.payoff,p_sense);
        IF NOT COALESCE((v_opt->>'supported')::BOOLEAN,FALSE) THEN
            RETURN QUERY SELECT NULL::NUMERIC,jsonb_build_object('kind','payoff_trace',
                'supported',FALSE,'feasible',NULL,'value',NULL,'horizon',p_horizon,
                'reason',v_opt->>'reason','unbounded',COALESCE((v_opt->>'unbounded')::BOOLEAN,FALSE));
            RETURN;
        END IF;
    END LOOP;

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
    SELECT 0, state_id, weight, NULL, NULL, NULL
    FROM nerode.wffa_terminal
    WHERE automaton_id = p_product AND role = 'initial' AND weight IS NOT NULL;

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
        SELECT dp.step AS k, dp.state AS s, dp.val + wt.weight AS total
        FROM _nerode_dp dp
        JOIN nerode.wffa_terminal wt
          ON wt.automaton_id = p_product AND wt.role = 'final' AND wt.state_id = dp.state
         AND wt.weight IS NOT NULL
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
                'value', NULL, 'horizon', p_horizon, 'supported',TRUE,'feasible', FALSE,
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
            'value', v_best_total, 'horizon', p_horizon, 'supported',TRUE,'feasible', TRUE,
            'length', v_best_step, 'history', v_hist,
            'product_id', p_product
        );
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.extremal(BIGINT, TEXT, INTEGER) IS
    'Exact finite-horizon extremum for deterministic WFFAs with affine interval-guarded '
    'payoffs. Unsupported or unbounded input gives value=NULL, supported=false. '
    'The returned word proves attainment; independent optimality needs a bound certificate.';
