-- =============================================================================
--  nerode — Step A6: Cross-scenario monotonicity probe  [Phase 4]
--
--  Closes the open gap from docs/TOOL_IN_TOOL_EHA_WFFA.md (finding #5):
--  if scenario H₁ is strictly tighter than H₂ — L(H₁) ⊆ L(H₂) — then over the
--  same base WFFA the extremal bounds must satisfy
--        best_{H₁} ≤ best_{H₂}     and     worst_{H₁} ≥ worst_{H₂}.
--  Without a detector, two independently-'valid' extremal claims about nested
--  scenarios could violate this silently. This probe makes the relation a
--  re-runnable three-valued claim, in the same shape as extremal_consistency.
--
--  eha_subset(H₁,H₂) decides L(H₁) ⊆ L(H₂) structurally: over the synchronous
--  product of the two EHA state spaces, ρ₁(q₁) ⊆ ρ₂(q₂) must hold at every
--  reachable joint state (same history ⇒ nested admissible intervals). This is
--  a structural invariant check in the kan spirit — re-runnable evidence over
--  the current automata, with a counterexample history when it fails.
--
--  Depends on: A0..A5, 01_schema. Idempotent.
--  NOTE: starter file — not yet executed against a live database.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- nerode.eha_subset(h1, h2) → (subset BOOLEAN, witness JSONB)
--   subset=TRUE  ⇒ witness.kind='interval_refinement' (all checked joint states)
--   subset=FALSE ⇒ witness.kind='counterexample' (a history u with ρ₁ ⊄ ρ₂)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.eha_subset(p_h1 BIGINT, p_h2 BIGINT)
RETURNS TABLE (subset BOOLEAN, witness JSONB) AS $$
DECLARE
    v_a1      nerode.automata%ROWTYPE;
    v_a2      nerode.automata%ROWTYPE;
    v_symbols TEXT[];
    v_offset  INTEGER;
    v_init1   INTEGER;
    v_init2   INTEGER;
    v_init    INTEGER;

    v_todo    INTEGER[];
    v_seen    INTEGER[];
    v_cur     INTEGER;
    v_q1      INTEGER;
    v_q2      INTEGER;
    v_q1n     INTEGER;
    v_q2n     INTEGER;
    v_enc     INTEGER;
    v_sym     TEXT;

    v_iv1     nerode.interval;
    v_iv2     nerode.interval;

    v_pred    JSONB := '{}'::jsonb;     -- enc → {from, sym}
    v_bad     INTEGER := NULL;
    v_path    TEXT[];
    v_node    INTEGER;
    v_entry   JSONB;
BEGIN
    SELECT * INTO v_a1 FROM nerode.automata WHERE id = p_h1;
    IF NOT FOUND OR v_a1.type <> 'EHA' THEN RAISE EXCEPTION 'eha_subset: % is not an EHA', p_h1; END IF;
    SELECT * INTO v_a2 FROM nerode.automata WHERE id = p_h2;
    IF NOT FOUND OR v_a2.type <> 'EHA' THEN RAISE EXCEPTION 'eha_subset: % is not an EHA', p_h2; END IF;
    IF v_a1.alphabet_id <> v_a2.alphabet_id THEN RAISE EXCEPTION 'eha_subset: alphabet mismatch'; END IF;

    SELECT symbols INTO v_symbols FROM nerode.alphabets WHERE id = v_a1.alphabet_id;
    SELECT COALESCE(max(state_id),0)+1 INTO v_offset FROM nerode.states WHERE automaton_id = p_h2;

    SELECT state_id INTO v_init1 FROM nerode.states WHERE automaton_id = p_h1 AND is_initial LIMIT 1;
    SELECT state_id INTO v_init2 FROM nerode.states WHERE automaton_id = p_h2 AND is_initial LIMIT 1;
    v_init := v_init1 * v_offset + v_init2;

    v_todo := ARRAY[v_init];
    v_seen := ARRAY[]::INTEGER[];

    WHILE array_length(v_todo,1) IS NOT NULL AND array_length(v_todo,1) > 0 AND v_bad IS NULL LOOP
        v_cur  := v_todo[1];
        v_todo := v_todo[2:];
        IF v_cur = ANY(v_seen) THEN CONTINUE; END IF;
        v_seen := v_seen || v_cur;

        v_q1 := v_cur / v_offset;
        v_q2 := v_cur % v_offset;

        SELECT interval INTO v_iv1 FROM nerode.eha_output WHERE automaton_id = p_h1 AND state_id = v_q1;
        SELECT interval INTO v_iv2 FROM nerode.eha_output WHERE automaton_id = p_h2 AND state_id = v_q2;

        IF NOT nerode.iv_subset(v_iv1, v_iv2) THEN
            v_bad := v_cur;
            EXIT;
        END IF;

        FOREACH v_sym IN ARRAY v_symbols LOOP
            SELECT to_state INTO v_q1n FROM nerode.transitions
            WHERE automaton_id = p_h1 AND from_state = v_q1 AND symbol = v_sym LIMIT 1;
            SELECT to_state INTO v_q2n FROM nerode.transitions
            WHERE automaton_id = p_h2 AND from_state = v_q2 AND symbol = v_sym LIMIT 1;
            IF v_q1n IS NULL OR v_q2n IS NULL THEN CONTINUE; END IF;
            v_enc := v_q1n * v_offset + v_q2n;
            IF NOT (v_enc = ANY(v_seen)) THEN
                v_todo := v_todo || v_enc;
                IF NOT (v_pred ? v_enc::text) THEN
                    v_pred := jsonb_set(v_pred, ARRAY[v_enc::text],
                                        jsonb_build_object('from', v_cur, 'sym', v_sym));
                END IF;
            END IF;
        END LOOP;
    END LOOP;

    IF v_bad IS NOT NULL THEN
        -- reconstruct the offending event history
        v_path := ARRAY[]::TEXT[];
        v_node := v_bad;
        WHILE v_node <> v_init LOOP
            v_entry := v_pred->v_node::text;
            EXIT WHEN v_entry IS NULL;
            v_path := ARRAY[v_entry->>'sym'] || v_path;
            v_node := (v_entry->>'from')::integer;
        END LOOP;
        SELECT interval INTO v_iv1 FROM nerode.eha_output WHERE automaton_id = p_h1 AND state_id = v_bad / v_offset;
        SELECT interval INTO v_iv2 FROM nerode.eha_output WHERE automaton_id = p_h2 AND state_id = v_bad % v_offset;
        RETURN QUERY SELECT FALSE, jsonb_build_object(
            'kind','counterexample',
            'history', array_to_string(v_path,''),
            'rho1', nerode.iv_to_json(v_iv1),
            'rho2', nerode.iv_to_json(v_iv2),
            'note','ρ₁ ⊄ ρ₂ at this history ⇒ L(H₁) ⊄ L(H₂)');
    ELSE
        RETURN QUERY SELECT TRUE, jsonb_build_object(
            'kind','interval_refinement',
            'joint_states_checked', array_length(v_seen,1),
            'h1', p_h1, 'h2', p_h2,
            'note','ρ₁(q₁) ⊆ ρ₂(q₂) at every reachable joint state ⇒ L(H₁) ⊆ L(H₂)');
    END IF;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION nerode.eha_subset(BIGINT, BIGINT) IS
    'Decide L(H₁) ⊆ L(H₂) structurally: ρ₁(q₁) ⊆ ρ₂(q₂) at every reachable joint '
    'state of the two EHAs. Returns (subset, witness) — interval_refinement on '
    'success, counterexample history on failure.';

-- ---------------------------------------------------------------------------
-- nerode.extremal_monotonicity(prod1, prod2, h1, h2, horizon)
--   → (verdict TEXT, detail JSONB)
--   Precondition L(H₁) ⊆ L(H₂) (via eha_subset). Then assert
--   best₁ ≤ best₂ and worst₁ ≥ worst₂. Three-valued, with the same soundness
--   gate as extremal_consistency (empty/infeasible or non-PWL ⇒ unverified,
--   never a manufactured refuted).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.extremal_monotonicity(
    p_prod1 BIGINT, p_prod2 BIGINT,
    p_h1 BIGINT, p_h2 BIGINT,
    p_horizon INTEGER DEFAULT 64)
RETURNS TABLE (verdict TEXT, detail JSONB) AS $$
DECLARE
    v_subset  BOOLEAN;
    v_subw    JSONB;
    v_b1 NUMERIC; v_b2 NUMERIC; v_w1 NUMERIC; v_w2 NUMERIC;
    v_holds   BOOLEAN;
    v_verdict TEXT;
BEGIN
    SELECT subset, witness INTO v_subset, v_subw FROM nerode.eha_subset(p_h1, p_h2);

    IF NOT v_subset THEN
        RETURN QUERY SELECT 'unverified',
            jsonb_build_object('reason','precondition L(H₁)⊆L(H₂) not established',
                               'subset_witness', v_subw);
        RETURN;
    END IF;

    SELECT value INTO v_b1 FROM nerode.extremal(p_prod1, 'best',  p_horizon);
    SELECT value INTO v_b2 FROM nerode.extremal(p_prod2, 'best',  p_horizon);
    SELECT value INTO v_w1 FROM nerode.extremal(p_prod1, 'worst', p_horizon);
    SELECT value INTO v_w2 FROM nerode.extremal(p_prod2, 'worst', p_horizon);

    IF v_b1 IS NULL OR v_b2 IS NULL OR v_w1 IS NULL OR v_w2 IS NULL THEN
        v_verdict := 'unverified';          -- empty/infeasible engine ≠ refuted
    ELSE
        v_holds := (v_b1 <= v_b2) AND (v_w1 >= v_w2);
        IF v_holds THEN
            v_verdict := 'valid';
        ELSIF NOT (nerode.product_is_pwl(p_prod1) AND nerode.product_is_pwl(p_prod2)) THEN
            v_verdict := 'unverified';       -- optimizer not exact ⇒ cannot soundly refute
        ELSE
            v_verdict := 'refuted';          -- genuine monotonicity violation
        END IF;
    END IF;

    RETURN QUERY SELECT v_verdict, jsonb_build_object(
        'subset', TRUE,
        'best1', v_b1, 'best2', v_b2, 'worst1', v_w1, 'worst2', v_w2,
        'expect','best1<=best2 AND worst1>=worst2',
        'h1',p_h1,'h2',p_h2,'prod1',p_prod1,'prod2',p_prod2);
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.extremal_monotonicity(BIGINT,BIGINT,BIGINT,BIGINT,INTEGER) IS
    'Cross-scenario monotonicity claim: for L(H₁)⊆L(H₂), assert best₁≤best₂ and '
    'worst₁≥worst₂. Three-valued; refuted only when sound (feasible + PWL).';

-- ---------------------------------------------------------------------------
-- nerode.certify_monotonicity(...) → BIGINT  — record the probe as a cert claim
-- (witness = the full detail incl. the subset evidence). Mirrors certify_extremal.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.certify_monotonicity(
    p_prod1 BIGINT, p_prod2 BIGINT, p_h1 BIGINT, p_h2 BIGINT,
    p_horizon INTEGER DEFAULT 64)
RETURNS BIGINT AS $$
DECLARE
    v_verdict TEXT;
    v_detail  JSONB;
BEGIN
    SELECT verdict, detail INTO v_verdict, v_detail
    FROM nerode.extremal_monotonicity(p_prod1, p_prod2, p_h1, p_h2, p_horizon);

    RETURN nerode.certify(
        p_automaton_id => p_prod1,
        p_operation    => 'extremal_monotonicity',
        p_evidence     => jsonb_build_object('verdict', v_verdict, 'detail', v_detail),
        p_witness_kind => 'monotonicity_probe',
        p_witness_body => v_detail);
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.certify_monotonicity(BIGINT,BIGINT,BIGINT,BIGINT,INTEGER) IS
    'Record a cross-scenario monotonicity probe as a three-valued cert claim.';
