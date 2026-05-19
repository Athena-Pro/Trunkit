-- =============================================================================
-- 94_open_session.sql
-- nerode.open_session() — Model B's entry point for a verified handoff.
--
-- Consumes the envelope produced by nerode.close_session() and returns a
-- structured context object that a model can use without further tool calls:
--
--   • cert verification   — probe_sql re-run; cert_valid flag in context
--   • pre-cached values   — all cache_keys fetched and inlined as resolved{}
--   • enriched DFA state  — label + pattern appended to each state_id so
--                           the model can interpret the session history
--                           without knowing the DFA internals
--   • optional session    — if p_new_session_id is given, a synthetic 'pass'
--                           event is logged to start the new session's log
--
-- Idempotent — safe to re-apply.
-- =============================================================================


CREATE OR REPLACE FUNCTION nerode.open_session(
    p_envelope       JSONB,
    p_new_session_id TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql AS $$
DECLARE
    v_claim_id   BIGINT;
    v_probe_sql  TEXT;
    v_cert_valid BOOLEAN;
    v_dfa_states JSONB;
    v_enriched   JSONB;
    v_resolved   JSONB;
    v_prior      JSONB;
    v_context    JSONB;
BEGIN
    v_claim_id   := (p_envelope ->> 'cert_bundle_id')::BIGINT;
    v_dfa_states := COALESCE(p_envelope -> 'session_dfa_states', '{}'::JSONB);

    -- ── Step 1: verify the cert ───────────────────────────────────────────────
    -- Executes probe_sql stored in cert.claim — a single SELECT that returns
    -- TRUE iff the DFA states still match a fresh replay of the session log.
    v_cert_valid := NULL;
    IF v_claim_id IS NOT NULL THEN
        SELECT probe_sql INTO v_probe_sql
        FROM   cert.claim
        WHERE  id = v_claim_id;

        IF v_probe_sql IS NOT NULL THEN
            EXECUTE v_probe_sql INTO v_cert_valid;
        END IF;
    END IF;

    -- ── Step 2: fetch pre-cached values ───────────────────────────────────────
    -- All cache_keys in the envelope are resolved in one indexed scan.
    -- NULL result means the key was listed but is not (yet) in the cache.
    SELECT jsonb_object_agg(seq_key, result ORDER BY seq_key)
    INTO   v_resolved
    FROM   nerode.sequence_cache
    WHERE  seq_key = ANY (
        SELECT jsonb_array_elements_text(
            COALESCE(p_envelope -> 'cache_keys', '[]'::JSONB)
        )
    );

    -- ── Step 3: enrich DFA states ─────────────────────────────────────────────
    -- Joins state_id back to its label and the automaton's pattern string so
    -- the context object is self-describing (no DFA internals needed by caller).
    SELECT jsonb_object_agg(
               a.name,
               jsonb_build_object(
                   'state_id',    kv.value::INT,
                   'label',       s.label,
                   'is_accepting', s.is_accepting,
                   'pattern',     a.provenance ->> 'pattern'
               )
               ORDER BY a.name
           )
    INTO   v_enriched
    FROM   jsonb_each_text(v_dfa_states)  kv
    JOIN   nerode.automata  a  ON a.name = kv.key
    JOIN   nerode.states    s  ON s.automaton_id = a.id
                               AND s.state_id    = kv.value::INT;

    -- ── Step 4: optionally register the new session ───────────────────────────
    -- A synthetic 'pass' event marks the handoff receipt so the new session's
    -- DFA walk starts from a known clean state.
    IF p_new_session_id IS NOT NULL THEN
        PERFORM nerode.log_event(
            p_new_session_id,
            'pass',
            jsonb_build_object(
                'handoff_from_claim', v_claim_id,
                'prior_session_id',   p_envelope ->> 'session_id',
                'cert_valid',         v_cert_valid
            )
        );
    END IF;

    -- ── Step 5: assemble context object ──────────────────────────────────────
    v_prior := jsonb_build_object(
        'session_id',    p_envelope ->> 'session_id',
        'cert_claim_id', v_claim_id,
        'cert_valid',    v_cert_valid,
        'attention_hint', p_envelope ->> 'attention_hint'
    );

    v_context := jsonb_build_object(
        'handoff_v',     1,
        'prior_session', v_prior,
        'dfa_context',   COALESCE(v_enriched, v_dfa_states),
        'resolved',      COALESCE(v_resolved, '{}'::JSONB)
    );

    IF p_new_session_id IS NOT NULL THEN
        v_context := v_context
                  || jsonb_build_object('new_session_id', p_new_session_id);
    END IF;

    RETURN v_context;
END;
$$;

COMMENT ON FUNCTION nerode.open_session(JSONB, TEXT) IS
    'Consume a close_session() envelope on behalf of Model B. '
    'Verifies the cert (probe_sql replay), fetches all pre-cached values, '
    'enriches DFA states with label+pattern, optionally registers a new session. '
    'Returns a self-describing context object ready for model consumption.';
