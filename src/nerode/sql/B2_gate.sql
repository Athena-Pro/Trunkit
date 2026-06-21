-- =============================================================================
--  nerode — Step B2: policy_gate (Porter policy gate)  [Phase 5]
--
--  GateFilter(a, L, Π) of LedgerAgent (arXiv:2606.20529): before an
--  environment-changing tool call fires, evaluate it against the policy
--  predicates over the current ledger and return ALLOW / REVISE / BLOCK with
--  policy-grounded feedback naming the violated rule and conflicting state.
--
--  Verdict resolution (three-valued, empty-guarded):
--    any FALSE with effect='block'                       → BLOCK   (→ cert refuted)
--    else any FALSE with effect='revise', or any NULL    → REVISE  (→ unverified)
--    else (all governing predicates TRUE)                → ALLOW   (→ valid)
--  A tool not in nerode.gated_tool and with no rules     → ALLOW   (reads pass)
--
--  Depends on: B0_ledger, B1_policy. Idempotent.
--  NOTE: starter file — not yet executed against a live database.
-- =============================================================================

CREATE OR REPLACE FUNCTION nerode.policy_gate(
    p_session_id TEXT,
    p_tool       TEXT,
    p_args       JSONB DEFAULT '{}')
RETURNS TABLE (verdict TEXT, feedback TEXT, witness JSONB) AS $$
DECLARE
    v_ledger    JSONB;
    v_gated     BOOLEAN;
    v_rule      RECORD;
    v_ok        BOOLEAN;
    v_results   JSONB := '[]'::jsonb;
    v_msgs      TEXT[] := ARRAY[]::TEXT[];
    v_has_block BOOLEAN := FALSE;
    v_has_soft  BOOLEAN := FALSE;   -- soft violation (effect=revise) or indeterminate
    v_n_rules   INTEGER := 0;
    v_verdict   TEXT;
    v_feedback  TEXT;
BEGIN
    v_ledger := nerode.ledger_render(p_session_id);
    SELECT EXISTS (SELECT 1 FROM nerode.gated_tool WHERE tool = p_tool) INTO v_gated;

    FOR v_rule IN
        SELECT id, name, predicate_sql, message, effect
        FROM nerode.policy_rule
        WHERE tool = p_tool AND enabled
        ORDER BY id
    LOOP
        v_n_rules := v_n_rules + 1;
        v_ok := nerode.eval_policy(v_rule.predicate_sql, v_ledger, p_args);

        v_results := v_results || jsonb_build_object(
            'rule', v_rule.name, 'effect', v_rule.effect,
            'result', CASE WHEN v_ok IS NULL THEN 'indeterminate'
                           WHEN v_ok THEN 'permit' ELSE 'violate' END);

        IF v_ok IS TRUE THEN
            CONTINUE;                                  -- permitted by this rule
        ELSIF v_ok IS FALSE AND v_rule.effect = 'block' THEN
            v_has_block := TRUE;
            v_msgs := v_msgs || v_rule.message;
        ELSE
            -- FALSE+revise, or NULL (missing state) → soft
            v_has_soft := TRUE;
            v_msgs := v_msgs || v_rule.message;
        END IF;
    END LOOP;

    -- ── resolve verdict ───────────────────────────────────────────────────
    IF v_has_block THEN
        v_verdict := 'BLOCK';
    ELSIF v_has_soft THEN
        v_verdict := 'REVISE';
    ELSE
        v_verdict := 'ALLOW';                          -- all rules permitted (or none)
    END IF;

    v_feedback := CASE
        WHEN v_verdict = 'ALLOW' THEN
            CASE WHEN v_gated THEN 'permitted: all governing policies satisfied'
                 ELSE 'not gated: ' || p_tool || ' is not an environment-changing tool' END
        ELSE array_to_string(v_msgs, '; ')
    END;

    RETURN QUERY SELECT v_verdict, v_feedback, jsonb_build_object(
        'kind',        'gate_decision',
        'session_id',  p_session_id,
        'tool',        p_tool,
        'args',        p_args,
        'gated',       v_gated,
        'verdict',     v_verdict,
        'rules_evaluated', v_n_rules,
        'rule_results',    v_results,
        'ledger',      v_ledger,
        'ledger_hash', md5(v_ledger::text),
        'decided_at',  now()
    );
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION nerode.policy_gate(TEXT, TEXT, JSONB) IS
    'GateFilter (arXiv:2606.20529): evaluate a proposed environment-changing call '
    'against the policy predicates over the session ledger. Returns '
    '(verdict ∈ ALLOW|REVISE|BLOCK, policy-grounded feedback, re-verifiable witness).';
