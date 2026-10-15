-- =============================================================================
-- 94_open_session.sql
-- nerode.open_session() — Model B's entry point for a verified handoff.
--
-- Consumes the envelope produced by nerode.close_session() and returns a
-- structured context object that a model can use without further tool calls:
--
--   • cert verification   — exact stored witness + DFA replay; cert_valid flag
--   • pre-cached values   — all cache_keys fetched and inlined as resolved{}
--   • enriched DFA state  — label + pattern appended to each state_id so
--                           the model can interpret the session history
--                           without knowing the DFA internals
--   • optional session    — if p_new_session_id is given, a synthetic 'pass'
--                           event is logged to start the new session's log
--
-- Idempotent — safe to re-apply.
-- =============================================================================


-- Bind an envelope to one exact certificate and its trace witness. A valid
-- probe for some other claim does not authenticate caller-supplied context.
CREATE OR REPLACE FUNCTION nerode.verify_handoff(p_envelope JSONB)
RETURNS BOOLEAN LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_witness JSONB;
BEGIN
    IF p_envelope->>'handoff_v' IS DISTINCT FROM '1' THEN
        RETURN FALSE;
    END IF;
    SELECT w.body INTO v_witness
    FROM cert.claim c
    JOIN cert.certificate ce ON ce.claim_id = c.id
    JOIN cert.witness w ON w.certificate_id = ce.id
    WHERE c.id = (p_envelope->>'cert_bundle_id')::bigint
      AND ce.id = (p_envelope->>'cert_certificate_id')::bigint
      AND c.subject_kind = 'nerode_session' AND c.method = 'session_close'
      AND c.subject_ref->>'session_id' = p_envelope->>'session_id'
      AND ce.status = 'valid' AND w.kind = 'trace';
    IF NOT FOUND OR v_witness->>'session_id' IS DISTINCT FROM p_envelope->>'session_id'
       OR v_witness->'dfa_states' IS DISTINCT FROM p_envelope->'session_dfa_states'
       OR v_witness->'cache_keys' IS DISTINCT FROM p_envelope->'cache_keys'
       OR v_witness->'ledger_binding' IS DISTINCT FROM p_envelope->'ledger_binding'
       OR v_witness->'attention_hint' IS DISTINCT FROM p_envelope->'attention_hint' THEN
        RETURN FALSE;
    END IF;
    RETURN nerode.session_dfa_state(p_envelope->>'session_id', 'session_calx_loop')
               IS NOT DISTINCT FROM (v_witness->'dfa_states'->>'session_calx_loop')::int
       AND nerode.session_dfa_state(p_envelope->>'session_id', 'session_edit_loop')
               IS NOT DISTINCT FROM (v_witness->'dfa_states'->>'session_edit_loop')::int;
EXCEPTION WHEN OTHERS THEN
    RETURN FALSE;
END;
$$;

CREATE OR REPLACE FUNCTION nerode.open_session(
    p_envelope       JSONB,
    p_new_session_id TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql AS $$
DECLARE
    v_claim_id   BIGINT;
    v_cert_valid BOOLEAN;
    v_dfa_states JSONB;
    v_enriched   JSONB;
    v_resolved   JSONB;
    v_prior      JSONB;
    v_context    JSONB;
BEGIN
    v_cert_valid := nerode.verify_handoff(p_envelope);
    IF v_cert_valid THEN
        v_claim_id := (p_envelope->>'cert_bundle_id')::bigint;
        v_dfa_states := COALESCE(p_envelope->'session_dfa_states', '{}'::jsonb);
    ELSE
        -- Invalid envelopes must neither resolve arbitrary cache keys nor
        -- append a receipt event before the caller can inspect the failure.
        RETURN jsonb_build_object(
            'handoff_v', 1,
            'prior_session', jsonb_build_object('cert_valid', FALSE),
            'dfa_context', '{}'::jsonb, 'resolved', '{}'::jsonb);
    END IF;

    -- ── Step 1: verify the cert ───────────────────────────────────────────────
    -- The exact stored certificate/witness is matched to the envelope and its
    -- DFA states are replayed without executing producer-authored SQL.
    -- verify_handoff checked the exact stored certificate and bound context;
    -- no producer-authored probe_sql is executed by the consumer here.

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
