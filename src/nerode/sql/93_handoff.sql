-- =============================================================================
-- 93_handoff.sql
-- nerode.close_session() — session boundary certificate + handoff envelope.
--
-- Five steps:
--   1. Replay both session DFAs via session_dfa_state() → final state_id per DFA
--   2. Collect session cache keys (from p_detail or by session-start timestamp)
--   3. Upsert cert.claim  (statement='session:<id>:closed', method='session_close')
--   4. Insert cert.certificate (valid_under=DFA states+schema; evidence=event tail)
--      + cert.witness           (kind='trace', body=input_window+states+cache_keys)
--   5. pg_notify('nerode_session_ready', envelope) + RETURN envelope JSONB
--
-- Idempotent — safe to re-apply.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- Register the session_close cert method (once).
-- ---------------------------------------------------------------------------

INSERT INTO cert.method (name, claim_kind, checker_kind, description)
VALUES (
    'session_close',
    'computational',
    'sql',
    'Session boundary claim: DFA states + cache keys at session close. '
    'Re-verifiable by replaying nerode.session_dfa_state() on stored session_log.'
)
ON CONFLICT (name) DO NOTHING;


-- ---------------------------------------------------------------------------
-- nerode.session_cache_tags
--
-- Explicit many-to-many between sessions and sequence_cache entries.
-- Replaces the time-based heuristic that was the fallback in close_session.
-- A pre-cache agent or any caller tags a key after building it; close_session
-- reads this table to auto-collect keys when none are passed in p_detail.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS nerode.session_cache_tags (
    session_id  TEXT         NOT NULL,
    seq_key     TEXT         NOT NULL REFERENCES nerode.sequence_cache (seq_key)
                                      ON DELETE CASCADE,
    tagged_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, seq_key)
);

CREATE INDEX IF NOT EXISTS idx_session_cache_tags_session
    ON nerode.session_cache_tags (session_id);


-- ---------------------------------------------------------------------------
-- nerode.tag_cache_key(session_id, seq_key)
--
-- Associate a sequence_cache entry with a session.
-- Idempotent — safe to call again if the tag already exists.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.tag_cache_key(
    p_session_id TEXT,
    p_seq_key    TEXT
)
RETURNS VOID
LANGUAGE sql AS $$
    INSERT INTO nerode.session_cache_tags (session_id, seq_key)
    VALUES (p_session_id, p_seq_key)
    ON CONFLICT DO NOTHING;
$$;

COMMENT ON FUNCTION nerode.tag_cache_key(TEXT, TEXT) IS
    'Tag a sequence_cache entry as belonging to a session. '
    'Idempotent. Used by precache agents and close_session auto-collection.';


-- ---------------------------------------------------------------------------
-- nerode.close_session(p_session_id, p_detail) RETURNS JSONB
--
-- Parameters
-- ----------
-- p_session_id  TEXT   Session identifier; must have rows in nerode.session_log
--                      (an empty session is still closeable — DFA states are the
--                      initial states, cache_keys defaults to []).
--
-- p_detail      JSONB  Optional caller hints (all fields optional):
--
--   cache_keys     JSONB array  — explicit list, e.g. ["arith_deriv:50"]
--                                 If absent, auto-collected from sequence_cache
--                                 by session start timestamp.
--   attention_hint TEXT         — freeform note placed verbatim in the envelope.
--   trunkit        JSONB        — Trunkit context snapshot to embed in valid_under,
--                                 e.g. {"kan_self_report_version":3,
--                                        "inference_id":"<uuid>"}.
--                                 Cross-DB (port 5434) so always passed by value.
--
-- Returns
-- -------
-- JSONB handoff envelope:
-- {
--   "handoff_v":          1,
--   "cert_bundle_id":     <claim_id BIGINT>,
--   "cache_keys":         [...],
--   "session_dfa_states": {"session_calx_loop": <state_id>,
--                           "session_edit_loop": <state_id>},
--   "attention_hint":     "..."
-- }
--
-- Side-effects
-- ------------
-- • Upserts one cert.claim  (statement unique per session_id)
-- • Appends one cert.certificate (seq increments on each call — re-closeable)
-- • Inserts  one cert.witness   (kind='trace', body carries the verifiable trace)
-- • Fires    pg_notify('nerode_session_ready', envelope::text)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.close_session(
    p_session_id  TEXT,
    p_detail      JSONB DEFAULT '{}'
)
RETURNS JSONB
LANGUAGE plpgsql AS $$
DECLARE
    v_calx_state    INT;
    v_edit_state    INT;
    v_cache_keys    JSONB;
    v_event_count   BIGINT;
    v_tail          JSONB;
    v_input_window  TEXT;
    v_subject_ref   JSONB;
    v_statement     TEXT;
    v_claim_id      BIGINT;
    v_cert_id       BIGINT;
    v_seq           INTEGER;
    v_valid_under   JSONB;
    v_evidence      JSONB;
    v_witness_body  JSONB;
    v_probe_sql     TEXT;
    v_envelope      JSONB;
BEGIN
    -- ── Step 1: replay both session DFAs ──────────────────────────────────
    v_calx_state := nerode.session_dfa_state(p_session_id, 'session_calx_loop');
    v_edit_state := nerode.session_dfa_state(p_session_id, 'session_edit_loop');

    -- ── Step 2: collect cache keys ────────────────────────────────────────
    -- Prefer explicit caller list; fall back to session_cache_tags.
    IF p_detail ? 'cache_keys'
       AND jsonb_typeof(p_detail -> 'cache_keys') = 'array'
    THEN
        v_cache_keys := p_detail -> 'cache_keys';
    ELSE
        SELECT COALESCE(jsonb_agg(seq_key ORDER BY tagged_at), '[]'::jsonb)
        INTO   v_cache_keys
        FROM   nerode.session_cache_tags
        WHERE  session_id = p_session_id;
    END IF;

    -- ── Step 3: gather session_log stats + event tail ─────────────────────
    SELECT COUNT(*)
    INTO   v_event_count
    FROM   nerode.session_log
    WHERE  session_id = p_session_id;

    -- Last 10 events (most-recent first) for the evidence field.
    SELECT jsonb_agg(
               jsonb_build_object('seq', seq, 'event', event)
               ORDER BY seq DESC
           )
    INTO   v_tail
    FROM   (
        SELECT seq, event
        FROM   nerode.session_log
        WHERE  session_id = p_session_id
        ORDER  BY seq DESC
        LIMIT  10
    ) t;

    -- Full input window (last 60 events, chronological) stored in the witness.
    -- Lets Model B verify DFA states offline:
    --   nerode.run_to_state(dfa_id, v_input_window)  should equal the stored states.
    SELECT string_agg(
               nerode.session_event_to_symbol(event),
               ''
               ORDER BY seq
           )
    INTO   v_input_window
    FROM   (
        SELECT event, seq
        FROM   nerode.session_log
        WHERE  session_id = p_session_id
        ORDER  BY seq DESC
        LIMIT  60
    ) r;

    -- ── Step 4a: upsert cert.claim ────────────────────────────────────────
    v_statement := 'session:' || p_session_id || ':closed';

    v_subject_ref := jsonb_build_object(
        'session_id', p_session_id,
        'dfa_states', jsonb_build_object(
                          'session_calx_loop', v_calx_state,
                          'session_edit_loop', v_edit_state
                      ),
        'cache_keys', v_cache_keys
    );

    -- probe_sql: a single SELECT that returns TRUE iff the stored DFA states
    -- still match a fresh replay of the session log.  Uses IS NOT DISTINCT FROM
    -- so that NULL states (empty session / unknown DFA) round-trip correctly.
    v_probe_sql := format(
        'SELECT nerode.session_dfa_state(%L, ''session_calx_loop'') IS NOT DISTINCT FROM %s'
        '   AND nerode.session_dfa_state(%L, ''session_edit_loop'') IS NOT DISTINCT FROM %s',
        p_session_id,
        CASE WHEN v_calx_state IS NULL THEN 'NULL' ELSE v_calx_state::TEXT END,
        p_session_id,
        CASE WHEN v_edit_state IS NULL THEN 'NULL' ELSE v_edit_state::TEXT END
    );

    INSERT INTO cert.claim
        (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
    VALUES (
        'nerode_session',
        v_subject_ref,
        v_statement,
        'computational',
        'session_close',
        v_probe_sql
    )
    ON CONFLICT (statement) DO UPDATE
        SET subject_ref = EXCLUDED.subject_ref,
            probe_sql   = EXCLUDED.probe_sql
    RETURNING id INTO v_claim_id;

    -- ── Step 4b: issue cert.certificate ───────────────────────────────────
    SELECT COALESCE(MAX(seq), 0) + 1
    INTO   v_seq
    FROM   cert.certificate
    WHERE  claim_id = v_claim_id;

    -- valid_under: the epistemic context under which the session ran.
    -- terminal_dfa_states are the state_ids at close time.
    -- trunkit context is by-value (separate DB on port 5434).
    v_valid_under := jsonb_build_object(
        'nerode_schema_version', 1,
        'terminal_dfa_states',   jsonb_build_object(
                                     'session_calx_loop', v_calx_state,
                                     'session_edit_loop', v_edit_state
                                 ),
        'closed_at',             now()
    );

    IF p_detail ? 'trunkit' THEN
        v_valid_under := v_valid_under
                      || jsonb_build_object('trunkit', p_detail -> 'trunkit');
    END IF;

    v_evidence := jsonb_build_object(
        'event_count', v_event_count,
        'tail',        COALESCE(v_tail, '[]'::jsonb)
    );

    INSERT INTO cert.certificate (claim_id, seq, status, evidence, valid_under)
    VALUES (v_claim_id, v_seq, 'valid', v_evidence, v_valid_under)
    RETURNING id INTO v_cert_id;

    -- ── Step 4c: issue cert.witness (kind='trace') ────────────────────────
    -- The witness body carries everything Model B needs to re-derive the states:
    --   input_window — the exact symbol string fed to the DFAs
    --   dfa_states   — expected final states after replaying input_window
    --   cache_keys   — pre-built sequence_cache entries available this session
    v_witness_body := jsonb_build_object(
        'input_window', COALESCE(v_input_window, ''),
        'dfa_states',   jsonb_build_object(
                            'session_calx_loop', v_calx_state,
                            'session_edit_loop', v_edit_state
                        ),
        'cache_keys',   v_cache_keys
    );

    INSERT INTO cert.witness (certificate_id, kind, body, schema_version)
    VALUES (
        v_cert_id,
        'trace',
        v_witness_body,
        jsonb_build_object('nerode_schema_version', 1)
    );

    -- ── Step 5: build handoff envelope, notify, return ────────────────────
    v_envelope := jsonb_build_object(
        'handoff_v',          1,
        'session_id',         p_session_id,
        'cert_bundle_id',     v_claim_id,
        'cache_keys',         v_cache_keys,
        'session_dfa_states', jsonb_build_object(
                                  'session_calx_loop', v_calx_state,
                                  'session_edit_loop', v_edit_state
                              ),
        'attention_hint',     COALESCE(
                                  p_detail ->> 'attention_hint',
                                  'session closed; DFA states and cache keys captured'
                              )
    );

    PERFORM pg_notify('nerode_session_ready', v_envelope::TEXT);

    RETURN v_envelope;
END;
$$;

COMMENT ON FUNCTION nerode.close_session(TEXT, JSONB) IS
    'Close a session: replay session DFAs via session_dfa_state(), collect cache '
    'keys, upsert cert.claim (statement=''session:<id>:closed''), append '
    'cert.certificate + cert.witness (kind=trace), fire '
    'pg_notify(''nerode_session_ready'', envelope), and return the handoff '
    'envelope JSONB. Re-calling on the same session_id is safe — it upserts the '
    'claim and appends a new certificate seq (re-attestation, not duplication).';
