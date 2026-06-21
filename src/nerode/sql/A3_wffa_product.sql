-- =============================================================================
--  nerode — Step A3: Scenario-restricted WFFA product  [Phase 4]
--
--  nerode.wffa_product(eha, wffa) implements Theorem 2 of arXiv:2606.11223:
--  the synchronized product W' of an EHA H and a WFFA W with
--      [[W']] = [[W]] ∩ L(H),
--  i.e. W' assigns every admissible finance word the same payoff as W, and −∞
--  to words violating H's scenario constraints.
--
--  Construction (paper §3, proof of Thm 2): the product state records both the
--  EHA state and the WFFA state. Each product transition's payoff is the
--  original WFFA payoff ⊗ the interval guard of the *target* EHA state — folding
--  "dᵢ must lie in ρ(last_H(u_i))" directly into the weight. Interval-
--  completeness (every interval has a guard) is provided by A0.iv_guard.
--
--  Mirrors the BFS / (q1·offset+q2) encoding of nerode.product (04_product.sql).
--  Depends on: A0_interval.sql, A1_eha.sql, A2_wffa.sql, 01/04. Idempotent.
--  NOTE: starter file — not yet executed against a live database.
-- =============================================================================

CREATE OR REPLACE FUNCTION nerode.wffa_product(p_eha BIGINT, p_wffa BIGINT)
RETURNS BIGINT AS $$
DECLARE
    v_e        nerode.automata%ROWTYPE;
    v_w        nerode.automata%ROWTYPE;
    v_symbols  TEXT[];
    v_offset   INTEGER;
    v_new_id   BIGINT;

    v_init_e   INTEGER;

    v_todo     INTEGER[];
    v_seen     INTEGER[];
    v_cur      INTEGER;
    v_qe       INTEGER;
    v_qw       INTEGER;
    v_qen      INTEGER;
    v_qwn      INTEGER;
    v_enc_n    INTEGER;
    v_sym      TEXT;

    v_payoff   JSONB;
    v_guard    JSONB;
    v_iv       nerode.interval;
    v_new_payoff JSONB;
    v_new_tid  BIGINT;

    v_qw0      INTEGER;
    v_enc0     INTEGER;
    v_acc_w    BOOLEAN;
    v_state_count INTEGER;
BEGIN
    SELECT * INTO v_e FROM nerode.automata WHERE id = p_eha;
    IF NOT FOUND OR v_e.type <> 'EHA' THEN
        RAISE EXCEPTION 'wffa_product: automaton % is not an EHA', p_eha;
    END IF;
    SELECT * INTO v_w FROM nerode.automata WHERE id = p_wffa;
    IF NOT FOUND OR v_w.type <> 'WFFA' THEN
        RAISE EXCEPTION 'wffa_product: automaton % is not a WFFA', p_wffa;
    END IF;
    IF v_e.alphabet_id <> v_w.alphabet_id THEN
        RAISE EXCEPTION 'wffa_product: alphabet mismatch (% vs %)', v_e.alphabet_id, v_w.alphabet_id;
    END IF;

    SELECT symbols INTO v_symbols FROM nerode.alphabets WHERE id = v_e.alphabet_id;

    -- offset = max WFFA state + 1 ⇒ enc(qe,qw) = qe*offset + qw is collision-free
    SELECT COALESCE(max(state_id), 0) + 1 INTO v_offset
    FROM nerode.states WHERE automaton_id = p_wffa;
    IF v_offset < 1 THEN v_offset := 1; END IF;

    SELECT state_id INTO v_init_e FROM nerode.states
    WHERE automaton_id = p_eha AND is_initial LIMIT 1;
    IF v_init_e IS NULL THEN
        RAISE EXCEPTION 'wffa_product: EHA % has no initial state', p_eha;
    END IF;

    -- Result WFFA
    INSERT INTO nerode.automata (name, type, alphabet_id, state_count, provenance)
    VALUES (
        COALESCE(v_w.name,'wffa') || '_restricted',
        'WFFA', v_e.alphabet_id, 0,
        jsonb_build_object('operation','wffa_product','eha',p_eha,'wffa',p_wffa,'created_at',now())
    )
    RETURNING id INTO v_new_id;

    -- Initial product states: (init_e, qw) for every WFFA initial state qw.
    v_todo := ARRAY[]::INTEGER[];
    v_seen := ARRAY[]::INTEGER[];
    FOR v_qw0 IN
        SELECT state_id FROM nerode.wffa_terminal
        WHERE automaton_id = p_wffa AND role = 'initial'
        UNION
        SELECT state_id FROM nerode.states
        WHERE automaton_id = p_wffa AND is_initial
          AND NOT EXISTS (SELECT 1 FROM nerode.wffa_terminal
                          WHERE automaton_id = p_wffa AND role = 'initial')
    LOOP
        v_enc0 := v_init_e * v_offset + v_qw0;
        SELECT EXISTS (
            SELECT 1 FROM nerode.wffa_terminal
            WHERE automaton_id = p_wffa AND role = 'final' AND state_id = v_qw0
        ) OR EXISTS (
            SELECT 1 FROM nerode.states
            WHERE automaton_id = p_wffa AND state_id = v_qw0 AND is_accepting
        ) INTO v_acc_w;

        INSERT INTO nerode.states (automaton_id, state_id, label, is_initial, is_accepting)
        VALUES (v_new_id, v_enc0, format('(%s,%s)', v_init_e, v_qw0), TRUE, v_acc_w)
        ON CONFLICT DO NOTHING;

        IF NOT (v_enc0 = ANY(v_todo)) THEN v_todo := v_todo || v_enc0; END IF;
    END LOOP;

    -- BFS over reachable product states
    WHILE array_length(v_todo,1) IS NOT NULL AND array_length(v_todo,1) > 0 LOOP
        v_cur  := v_todo[1];
        v_todo := v_todo[2:];
        IF v_cur = ANY(v_seen) THEN CONTINUE; END IF;
        v_seen := v_seen || v_cur;

        v_qe := v_cur / v_offset;
        v_qw := v_cur % v_offset;

        FOREACH v_sym IN ARRAY v_symbols LOOP
            -- EHA is deterministic+complete: unique target
            SELECT to_state INTO v_qen FROM nerode.transitions
            WHERE automaton_id = p_eha AND from_state = v_qe AND symbol = v_sym LIMIT 1;
            IF v_qen IS NULL THEN CONTINUE; END IF;     -- inadmissible event ⇒ drop

            -- guard of the TARGET EHA state's interval ρ(qen)
            SELECT interval INTO v_iv FROM nerode.eha_output
            WHERE automaton_id = p_eha AND state_id = v_qen;
            IF v_iv IS NULL THEN CONTINUE; END IF;
            v_guard := nerode.iv_guard(v_iv);

            -- every WFFA transition (qw, a, qwn)
            FOR v_qwn, v_payoff IN
                SELECT t.to_state, w.payoff
                FROM nerode.transitions t
                JOIN nerode.wffa_weight w ON w.transition_id = t.id
                WHERE t.automaton_id = p_wffa AND t.from_state = v_qw AND t.symbol = v_sym
            LOOP
                v_enc_n := v_qen * v_offset + v_qwn;

                -- materialise the target product state if new
                IF NOT (v_enc_n = ANY(v_seen)) THEN
                    SELECT EXISTS (
                        SELECT 1 FROM nerode.wffa_terminal
                        WHERE automaton_id = p_wffa AND role = 'final' AND state_id = v_qwn
                    ) OR EXISTS (
                        SELECT 1 FROM nerode.states
                        WHERE automaton_id = p_wffa AND state_id = v_qwn AND is_accepting
                    ) INTO v_acc_w;

                    INSERT INTO nerode.states (automaton_id, state_id, label, is_initial, is_accepting)
                    VALUES (v_new_id, v_enc_n, format('(%s,%s)', v_qen, v_qwn), FALSE, v_acc_w)
                    ON CONFLICT DO NOTHING;

                    IF NOT (v_enc_n = ANY(v_todo)) THEN v_todo := v_todo || v_enc_n; END IF;
                END IF;

                -- folded payoff: wt_T(t) ⊗ guard(ρ(qen))
                v_new_payoff := jsonb_build_object('otimes', jsonb_build_array(v_payoff, v_guard));

                INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
                VALUES (v_new_id, v_cur, v_sym, v_enc_n)
                ON CONFLICT DO NOTHING
                RETURNING id INTO v_new_tid;
                IF v_new_tid IS NULL THEN
                    SELECT id INTO v_new_tid FROM nerode.transitions
                    WHERE automaton_id = v_new_id AND from_state = v_cur
                      AND symbol = v_sym AND to_state = v_enc_n LIMIT 1;
                END IF;

                INSERT INTO nerode.wffa_weight (transition_id, payoff)
                VALUES (v_new_tid, v_new_payoff)
                ON CONFLICT (transition_id) DO NOTHING;
            END LOOP;
        END LOOP;
    END LOOP;

    -- Copy terminal weights for every reachable product state
    FOREACH v_cur IN ARRAY v_seen LOOP
        v_qw := v_cur % v_offset;
        INSERT INTO nerode.wffa_terminal (automaton_id, state_id, role, weight)
        SELECT v_new_id, v_cur, 'initial', wt.weight
        FROM nerode.wffa_terminal wt
        WHERE wt.automaton_id = p_wffa AND wt.role = 'initial' AND wt.state_id = v_qw
          AND EXISTS (SELECT 1 FROM nerode.states s
                      WHERE s.automaton_id = v_new_id AND s.state_id = v_cur AND s.is_initial)
        ON CONFLICT (automaton_id, state_id, role) DO NOTHING;

        INSERT INTO nerode.wffa_terminal (automaton_id, state_id, role, weight)
        SELECT v_new_id, v_cur, 'final', wt.weight
        FROM nerode.wffa_terminal wt
        WHERE wt.automaton_id = p_wffa AND wt.role = 'final' AND wt.state_id = v_qw
        ON CONFLICT (automaton_id, state_id, role) DO NOTHING;
    END LOOP;

    SELECT count(*) INTO v_state_count FROM nerode.states WHERE automaton_id = v_new_id;
    UPDATE nerode.automata SET state_count = v_state_count WHERE id = v_new_id;

    INSERT INTO nerode.construction_log (automaton_id, operation, inputs, result)
    VALUES (v_new_id, 'wffa_product',
            jsonb_build_object('eha', p_eha, 'wffa', p_wffa),
            jsonb_build_object('output_id', v_new_id, 'states', v_state_count));

    RETURN v_new_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.wffa_product(BIGINT, BIGINT) IS
    'Scenario-restricted product W'' of an EHA and a WFFA (arXiv:2606.11223 Thm 2): '
    '[[W'']] = [[W]] ∩ L(H). Folds each target EHA state''s interval guard into the '
    'transition payoff so violating words evaluate to −∞. Returns the new WFFA id.';
