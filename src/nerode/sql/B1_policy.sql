-- =============================================================================
--  nerode — Step B1: Policy predicate registry (Porter policy gate)  [Phase 5]
--
--  The predicates Π of LedgerAgent (arXiv:2606.20529): domain rules that gate
--  environment-changing tools, expressed as ALLOW-predicates over ledger fields.
--  A rule's predicate_sql is a SQL boolean expression, TRUE iff the action is
--  PERMITTED, written over two bound parameters:
--        $1 = the rendered ledger JSONB   (nerode.ledger_render)
--        $2 = the proposed call args JSONB
--  It is evaluated with parameters BOUND (EXECUTE … USING), never interpolated,
--  so attacker-controlled arg values cannot inject SQL — same discipline as the
--  stored probe_sql in close_session.
--
--  Depends on: B0_ledger. Idempotent.
--  NOTE: starter file — not yet executed against a live database.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- nerode.gated_tool — registry of environment-changing tools the gate governs.
-- A tool NOT listed here is treated as a read and passes the gate by default.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nerode.gated_tool (
    tool        TEXT PRIMARY KEY,
    description TEXT,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE nerode.gated_tool IS
    'Environment-changing tools governed by the policy gate. Unlisted tools pass.';

-- ---------------------------------------------------------------------------
-- nerode.policy_rule — the predicates Π.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nerode.policy_rule (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name          TEXT UNIQUE NOT NULL,
    tool          TEXT NOT NULL,        -- environment-changing tool this gates
    predicate_sql TEXT NOT NULL,        -- ALLOW condition over $1=ledger, $2=args
    message       TEXT NOT NULL,        -- policy-grounded feedback when violated
    effect        TEXT NOT NULL DEFAULT 'block' CHECK (effect IN ('block','revise')),
    enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE nerode.policy_rule IS
    'Policy predicates Π (arXiv:2606.20529). predicate_sql is an ALLOW-condition '
    'boolean over $1=ledger JSONB, $2=args JSONB; violation = NOT allow. '
    'effect=block ⇒ hard (BLOCK); effect=revise ⇒ soft (REVISE).';

CREATE INDEX IF NOT EXISTS idx_policy_rule_tool
    ON nerode.policy_rule (tool) WHERE enabled;

-- ---------------------------------------------------------------------------
-- Registration helpers (idempotent upserts).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.register_gated_tool(p_tool TEXT, p_description TEXT DEFAULT NULL)
RETURNS VOID
LANGUAGE sql AS $$
    INSERT INTO nerode.gated_tool (tool, description)
    VALUES (p_tool, p_description)
    ON CONFLICT (tool) DO UPDATE SET description = COALESCE(EXCLUDED.description, nerode.gated_tool.description);
$$;

CREATE OR REPLACE FUNCTION nerode.register_policy(
    p_name          TEXT,
    p_tool          TEXT,
    p_predicate_sql TEXT,
    p_message       TEXT,
    p_effect        TEXT DEFAULT 'block')
RETURNS BIGINT AS $$
DECLARE
    v_id BIGINT;
BEGIN
    -- A tool with a policy is, by definition, environment-changing.
    PERFORM nerode.register_gated_tool(p_tool);

    INSERT INTO nerode.policy_rule (name, tool, predicate_sql, message, effect)
    VALUES (p_name, p_tool, p_predicate_sql, p_message, p_effect)
    ON CONFLICT (name) DO UPDATE
        SET tool          = EXCLUDED.tool,
            predicate_sql = EXCLUDED.predicate_sql,
            message       = EXCLUDED.message,
            effect        = EXCLUDED.effect,
            enabled       = TRUE
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.register_policy(TEXT, TEXT, TEXT, TEXT, TEXT) IS
    'Register/replace a policy predicate and mark its tool environment-changing. '
    'predicate_sql is an ALLOW-condition over $1=ledger, $2=args.';

-- ---------------------------------------------------------------------------
-- nerode.eval_policy(rule_id, ledger, args) → BOOLEAN
--   Evaluate one allow-predicate with parameters bound. Returns
--   TRUE (permitted) / FALSE (violation) / NULL (indeterminate: missing state).
--   SECURITY: predicate_sql references only $1/$2; arg VALUES are bound, not
--   interpolated. (Predicate TEXT itself is operator-authored, like probe_sql.)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.eval_policy(
    p_predicate_sql TEXT,
    p_ledger        JSONB,
    p_args          JSONB)
RETURNS BOOLEAN AS $$
DECLARE
    v_ok BOOLEAN;
BEGIN
    EXECUTE format('SELECT (%s)', p_predicate_sql)
        INTO v_ok
        USING p_ledger, p_args;
    RETURN v_ok;
EXCEPTION
    WHEN others THEN
        -- A predicate that errors on missing/ill-typed state is indeterminate,
        -- never a violation — preserves empty/undecidable ≠ refuted.
        RETURN NULL;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION nerode.eval_policy(TEXT, JSONB, JSONB) IS
    'Evaluate one ALLOW-predicate with $1=ledger, $2=args bound. '
    'TRUE=permitted, FALSE=violation, NULL=indeterminate (missing state or error).';
