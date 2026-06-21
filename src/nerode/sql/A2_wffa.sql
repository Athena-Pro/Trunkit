-- =============================================================================
--  nerode — Step A2: Weighted Finite Finance Automata (WFFA)  [Phase 4]
--
--  WFFAs (arXiv:2606.11223 §3, after Droste & Nürnberg arXiv:2604.17370) extend
--  DFAs with quantitative semantics over the max-plus semiring S_max,+ =
--  (ℝ∪{−∞}, max, +, −∞, 0). Transition weights are *local payoff expressions*
--  e ∈ E_F whose value depends on the observed data value d: [[e]]: D → S.
--
--  We store weights beside the Boolean transition relation (no change to
--  nerode.transitions), so all DFA tooling keeps working. The semiring zero −∞
--  is represented by SQL NULL throughout.
--
--  Local payoff expression AST (JSONB):
--    {"const": s}                  → s                         (semiring constant)
--    {"bind":  s}                  → d · s                     (⟨⟨s⟩⟩ data-binding b_lin)
--    {"oplus": [e1, e2]}           → max([[e1]], [[e2]])       (⊕)
--    {"otimes":[e1, e2]}           → [[e1]] + [[e2]]           (⊗, −∞ absorbing)
--    {"guard": {"iv": I, "then": e}} → [[e]] if d ∈ I else −∞  (interval guard)
--
--  Depends on: A0_interval.sql, 01_schema.sql, 05_from_regex.sql (alphabet
--  naming). Idempotent. NOTE: starter file — not yet run against a live DB.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- nerode.wffa_weight — local payoff expression attached to a transition
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nerode.wffa_weight (
    transition_id BIGINT PRIMARY KEY
                  REFERENCES nerode.transitions(id) ON DELETE CASCADE,
    payoff        JSONB NOT NULL
);

COMMENT ON TABLE nerode.wffa_weight IS
    'Local payoff expression (max-plus AST) for a WFFA transition (arXiv:2606.11223 Def. 3/4).';

-- ---------------------------------------------------------------------------
-- nerode.wffa_terminal — initial / final weights wt_I, wt_F
--   weight NULL ⇒ −∞ (semiring zero). role distinguishes I from F.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nerode.wffa_terminal (
    automaton_id BIGINT  NOT NULL REFERENCES nerode.automata(id) ON DELETE CASCADE,
    state_id     INTEGER NOT NULL,
    role         TEXT    NOT NULL CHECK (role IN ('initial','final')),
    weight       NUMERIC,                       -- NULL ⇒ −∞
    PRIMARY KEY (automaton_id, state_id, role),
    FOREIGN KEY (automaton_id, state_id)
        REFERENCES nerode.states(automaton_id, state_id) ON DELETE CASCADE
);

COMMENT ON TABLE nerode.wffa_terminal IS
    'Initial (wt_I) and final (wt_F) weights of a WFFA. weight NULL ⇒ −∞.';

-- ---------------------------------------------------------------------------
-- nerode.payoff_eval(expr, d) → NUMERIC   (NULL ⇒ −∞, the semiring zero)
-- Recursive evaluator for the local payoff expression AST.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.payoff_eval(p JSONB, p_d NUMERIC)
RETURNS NUMERIC AS $$
DECLARE
    v_l  NUMERIC;
    v_r  NUMERIC;
    v_iv nerode.interval;
BEGIN
    IF p IS NULL THEN
        RAISE EXCEPTION 'payoff_eval: null expression';
    END IF;

    IF p ? 'const' THEN
        RETURN (p->>'const')::numeric;

    ELSIF p ? 'bind' THEN
        IF p_d IS NULL THEN RETURN NULL; END IF;          -- −∞ if no data bound
        RETURN p_d * (p->>'bind')::numeric;

    ELSIF p ? 'oplus' THEN                                 -- max, with −∞(=NULL) identity
        v_l := nerode.payoff_eval(p->'oplus'->0, p_d);
        v_r := nerode.payoff_eval(p->'oplus'->1, p_d);
        IF v_l IS NULL THEN RETURN v_r; END IF;
        IF v_r IS NULL THEN RETURN v_l; END IF;
        RETURN GREATEST(v_l, v_r);

    ELSIF p ? 'otimes' THEN                                -- +, with −∞(=NULL) absorbing
        v_l := nerode.payoff_eval(p->'otimes'->0, p_d);
        IF v_l IS NULL THEN RETURN NULL; END IF;
        v_r := nerode.payoff_eval(p->'otimes'->1, p_d);
        IF v_r IS NULL THEN RETURN NULL; END IF;
        RETURN v_l + v_r;

    ELSIF p ? 'guard' THEN                                 -- interval membership guard
        v_iv := nerode.iv_from_json(p->'guard'->'iv');
        IF nerode.iv_contains(v_iv, p_d) THEN
            RETURN nerode.payoff_eval(p->'guard'->'then', p_d);
        ELSE
            RETURN NULL;                                   -- d ∉ I ⇒ −∞
        END IF;

    ELSE
        RAISE EXCEPTION 'payoff_eval: unknown AST node %', p;
    END IF;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

COMMENT ON FUNCTION nerode.payoff_eval(JSONB, NUMERIC) IS
    'Evaluate a local payoff expression [[e]](d) over the max-plus semiring. '
    'Returns NULL for the semiring zero −∞.';

-- ---------------------------------------------------------------------------
-- Builder helpers (make WFFAs constructible/testable without manual INSERTs).
-- wffa_create shares the alphabet row with from_regex/compile_eha (same
-- 'auto_'||md5(symbols) naming), so a WFFA and an EHA over the same Σ have
-- matching alphabet_id and can be combined by wffa_product (A3).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.wffa_create(p_name TEXT, p_symbols TEXT[])
RETURNS BIGINT AS $$
DECLARE
    v_alph_name TEXT;
    v_alph_id   BIGINT;
    v_id        BIGINT;
BEGIN
    v_alph_name := 'auto_' || md5(array_to_string(p_symbols, ''));
    INSERT INTO nerode.alphabets (name, symbols)
    VALUES (v_alph_name, p_symbols)
    ON CONFLICT (name) DO NOTHING;
    SELECT id INTO v_alph_id FROM nerode.alphabets WHERE name = v_alph_name;

    INSERT INTO nerode.automata (name, type, alphabet_id, state_count, provenance)
    VALUES (p_name, 'WFFA', v_alph_id, 0,
            jsonb_build_object('operation','wffa_create','created_at',now()))
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION nerode.wffa_add_state(
    p_id BIGINT, p_state_id INTEGER,
    p_initial BOOLEAN DEFAULT FALSE, p_final BOOLEAN DEFAULT FALSE,
    p_init_weight NUMERIC DEFAULT NULL, p_final_weight NUMERIC DEFAULT NULL)
RETURNS VOID AS $$
BEGIN
    INSERT INTO nerode.states (automaton_id, state_id, label, is_initial, is_accepting)
    VALUES (p_id, p_state_id, p_state_id::text, p_initial, p_final)
    ON CONFLICT (automaton_id, state_id) DO UPDATE
        SET is_initial = EXCLUDED.is_initial, is_accepting = EXCLUDED.is_accepting;

    IF p_initial THEN
        INSERT INTO nerode.wffa_terminal (automaton_id, state_id, role, weight)
        VALUES (p_id, p_state_id, 'initial', COALESCE(p_init_weight, 0))
        ON CONFLICT (automaton_id, state_id, role) DO UPDATE SET weight = EXCLUDED.weight;
    END IF;
    IF p_final THEN
        INSERT INTO nerode.wffa_terminal (automaton_id, state_id, role, weight)
        VALUES (p_id, p_state_id, 'final', COALESCE(p_final_weight, 0))
        ON CONFLICT (automaton_id, state_id, role) DO UPDATE SET weight = EXCLUDED.weight;
    END IF;

    UPDATE nerode.automata SET state_count =
        (SELECT count(*) FROM nerode.states WHERE automaton_id = p_id)
    WHERE id = p_id;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION nerode.wffa_add_edge(
    p_id BIGINT, p_from INTEGER, p_symbol TEXT, p_to INTEGER, p_payoff JSONB)
RETURNS BIGINT AS $$
DECLARE
    v_tid BIGINT;
BEGIN
    INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
    VALUES (p_id, p_from, p_symbol, p_to)
    ON CONFLICT DO NOTHING
    RETURNING id INTO v_tid;
    IF v_tid IS NULL THEN
        SELECT id INTO v_tid FROM nerode.transitions
        WHERE automaton_id = p_id AND from_state = p_from
          AND symbol = p_symbol AND to_state = p_to LIMIT 1;
    END IF;
    INSERT INTO nerode.wffa_weight (transition_id, payoff)
    VALUES (v_tid, p_payoff)
    ON CONFLICT (transition_id) DO UPDATE SET payoff = EXCLUDED.payoff;
    RETURN v_tid;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- nerode.wffa_path_value(wffa, word) → NUMERIC
--   word = [["a", d], ["b", d2], ...]  (finance word; d as JSON number)
--   Deterministic walk: accumulate wt_I ⊗ Π[[wt_T(tᵢ)]](dᵢ) ⊗ wt_F over the
--   max-plus semiring (⊗ = +). Returns NULL (−∞) if no accepting run exists or
--   any step is infeasible. This is also the replay primitive a consumer uses
--   to re-verify an extremal-payoff witness (A5).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.wffa_path_value(p_wffa BIGINT, p_word JSONB)
RETURNS NUMERIC AS $$
DECLARE
    v_state  INTEGER;
    v_acc    NUMERIC;
    v_init_w NUMERIC;
    v_fin_w  NUMERIC;
    v_n      INTEGER;
    v_i      INTEGER;
    v_sym    TEXT;
    v_d      NUMERIC;
    v_to     INTEGER;
    v_payoff JSONB;
    v_step   NUMERIC;
    v_taken  BOOLEAN;
BEGIN
    SELECT state_id, weight INTO v_state, v_init_w
    FROM nerode.wffa_terminal
    WHERE automaton_id = p_wffa AND role = 'initial'
    ORDER BY state_id LIMIT 1;

    IF v_state IS NULL THEN
        SELECT state_id INTO v_state FROM nerode.states
        WHERE automaton_id = p_wffa AND is_initial LIMIT 1;
        v_init_w := 0;
    END IF;
    IF v_state IS NULL THEN
        RAISE EXCEPTION 'wffa_path_value: automaton % has no initial state', p_wffa;
    END IF;

    v_acc := COALESCE(v_init_w, 0);
    v_n   := jsonb_array_length(p_word);

    FOR v_i IN 0 .. v_n - 1 LOOP
        v_sym  := p_word->v_i->>0;
        v_d    := (p_word->v_i->>1)::numeric;
        v_taken := FALSE;

        FOR v_to, v_payoff IN
            SELECT t.to_state, w.payoff
            FROM nerode.transitions t
            JOIN nerode.wffa_weight w ON w.transition_id = t.id
            WHERE t.automaton_id = p_wffa AND t.from_state = v_state AND t.symbol = v_sym
            ORDER BY t.to_state
        LOOP
            v_step := nerode.payoff_eval(v_payoff, v_d);
            IF v_step IS NOT NULL THEN
                v_acc   := v_acc + v_step;
                v_state := v_to;
                v_taken := TRUE;
                EXIT;                      -- deterministic: take first enabled
            END IF;
        END LOOP;

        IF NOT v_taken THEN
            RETURN NULL;                   -- infeasible step ⇒ −∞
        END IF;
    END LOOP;

    SELECT weight INTO v_fin_w FROM nerode.wffa_terminal
    WHERE automaton_id = p_wffa AND role = 'final' AND state_id = v_state;
    IF v_fin_w IS NULL THEN
        IF EXISTS (SELECT 1 FROM nerode.states
                   WHERE automaton_id = p_wffa AND state_id = v_state AND is_accepting) THEN
            v_fin_w := 0;
        ELSE
            RETURN NULL;                   -- not a final state ⇒ no accepting run
        END IF;
    END IF;

    RETURN v_acc + v_fin_w;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION nerode.wffa_path_value(BIGINT, JSONB) IS
    'Evaluate the max-plus payoff of a finance word along a (deterministic) WFFA. '
    'word = [["a",d],...]. Returns NULL (−∞) if infeasible / no accepting run. '
    'Also the consumer-side replay primitive for extremal-payoff witnesses.';
