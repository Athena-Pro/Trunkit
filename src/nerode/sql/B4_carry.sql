-- =============================================================================
--  nerode — Step B4: Carry the ledger across the Porter handoff  [Phase 5]
--
--  Composes the Porter handoff (close_session / open_session, steps 93/94) with
--  the LedgerAgent ledger (B0): the envelope carries the typed task-state ledger
--  forward, cert-verified, so Model B opens an already-populated ledger and can
--  gate its first environment-changing call with ZERO tool calls.
--
--  Flow:
--    Model A:  close_with_ledger(session, detail)
--                → close_session() boundary cert + handoff envelope,
--                  certify_ledger() snapshot cert, ledger packed into envelope.
--    Model B:  open_with_ledger(envelope, new_session)
--                → open_session() (verifies session cert, resolves cache),
--                  ledger unpacked into new_session, ledger_hash re-verified.
--              open_and_gate(envelope, new_session, tool, args)
--                → the above, then policy_gate() on the first proposed action.
--
--  Depends on: B0/B1/B2/B3 + 93_handoff + 94_open_session + cert schema. Idempotent.
--  NOTE: starter file — not yet executed against a live database.
-- =============================================================================

INSERT INTO cert.method (name, claim_kind, checker_kind, description)
VALUES (
    'ledger_snapshot',
    'computational',
    'sql',
    'Carried ledger snapshot (arXiv:2606.20529). Witness is the packed ledger; '
    're-verifiable by re-hashing the entries to the stored ledger_hash.'
)
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- nerode.ledger_pack(session) → JSONB   portable ledger snapshot
--   { "ledger_v":1, "entries":[{path,value,schema_type}…], "ledger_hash":md5 }
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.ledger_pack(p_session_id TEXT)
RETURNS JSONB
LANGUAGE sql STABLE AS $$
    SELECT jsonb_build_object(
        'ledger_v', 1,
        'entries', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'path', path, 'value', value, 'schema_type', schema_type)
                       ORDER BY path)
            FROM nerode.ledger_state WHERE session_id = p_session_id), '[]'::jsonb),
        'ledger_hash', nerode.ledger_hash(p_session_id)
    );
$$;

COMMENT ON FUNCTION nerode.ledger_pack(TEXT) IS
    'Portable snapshot of a session ledger: ordered entries + ledger_hash.';

-- ---------------------------------------------------------------------------
-- nerode.ledger_unpack(target_session, packed) → INTEGER  (entries restored)
--   Restore a packed ledger into target_session via ledger_absorb (latest wins).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.ledger_unpack(p_target_session TEXT, p_packed JSONB)
RETURNS INTEGER AS $$
DECLARE
    v_e JSONB;
    v_n INTEGER := 0;
BEGIN
    FOR v_e IN SELECT jsonb_array_elements(COALESCE(p_packed->'entries','[]'::jsonb))
    LOOP
        PERFORM nerode.ledger_absorb(
            p_target_session,
            v_e->>'path',
            v_e->'value',
            v_e->>'schema_type',
            NULL);
        v_n := v_n + 1;
    END LOOP;
    RETURN v_n;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.ledger_unpack(TEXT, JSONB) IS
    'Restore a packed ledger into target_session (latest wins). Returns count.';

-- ---------------------------------------------------------------------------
-- nerode.replay_ledger(packed) → BOOLEAN   re-hash entries → stored ledger_hash
--   Consumer self-consistency check on the carried snapshot (no DB state needed).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.replay_ledger(p_packed JSONB)
RETURNS BOOLEAN
LANGUAGE sql IMMUTABLE AS $$
    SELECT md5(
        COALESCE((
            SELECT jsonb_object_agg(e->>'path', e->'value' ORDER BY e->>'path')
            FROM jsonb_array_elements(COALESCE(p_packed->'entries','[]'::jsonb)) e
        ), '{}'::jsonb)::text
    ) = (p_packed->>'ledger_hash');
$$;

COMMENT ON FUNCTION nerode.replay_ledger(JSONB) IS
    'Re-hash a packed ledger''s entries (canonical render) and confirm it matches '
    'the stored ledger_hash. Pure; no DB state.';

-- ---------------------------------------------------------------------------
-- nerode.certify_ledger(session) → BIGINT  record the snapshot as a cert claim
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.certify_ledger(p_session_id TEXT)
RETURNS BIGINT AS $$
DECLARE
    v_pack     JSONB;
    v_hash     TEXT;
    v_statement TEXT;
    v_claim_id BIGINT;
    v_cert_id  BIGINT;
    v_seq      INTEGER;
    v_probe    TEXT;
BEGIN
    v_pack := nerode.ledger_pack(p_session_id);
    v_hash := v_pack->>'ledger_hash';
    v_statement := 'ledger:' || p_session_id || ':' || v_hash;
    v_probe := format('SELECT nerode.replay_ledger(%L::jsonb)', v_pack);

    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
    VALUES (
        'nerode_ledger',
        jsonb_build_object('session_id', p_session_id, 'ledger_hash', v_hash,
                           'entry_count', jsonb_array_length(v_pack->'entries')),
        v_statement, 'computational', 'ledger_snapshot', v_probe)
    ON CONFLICT (statement) DO UPDATE SET subject_ref = EXCLUDED.subject_ref, probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_claim_id;

    SELECT COALESCE(max(seq),0)+1 INTO v_seq FROM cert.certificate WHERE claim_id = v_claim_id;

    INSERT INTO cert.certificate (claim_id, seq, status, evidence, valid_under)
    VALUES (v_claim_id, v_seq, 'valid',
            jsonb_build_object('entry_count', jsonb_array_length(v_pack->'entries')),
            jsonb_build_object('nerode_schema_version', 1, 'ledger_hash', v_hash, 'packed_at', now()))
    RETURNING id INTO v_cert_id;

    INSERT INTO cert.witness (certificate_id, kind, body, schema_version)
    VALUES (v_cert_id, 'ledger_snapshot', v_pack,
            jsonb_build_object('nerode_schema_version', 1));

    RETURN v_claim_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.certify_ledger(TEXT) IS
    'Record the session ledger snapshot as a cert claim whose witness is the '
    'packed ledger; probe_sql re-hashes it. Returns the claim id.';

-- ---------------------------------------------------------------------------
-- nerode.close_with_ledger(session, detail) → JSONB  carry-augmented envelope
--   Base handoff envelope (close_session) + carried, certified ledger.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.close_with_ledger(
    p_session_id TEXT, p_detail JSONB DEFAULT '{}')
RETURNS JSONB AS $$
DECLARE
    v_pack   JSONB;
    v_lclaim BIGINT;
    v_env    JSONB;
BEGIN
    v_pack   := nerode.ledger_pack(p_session_id);
    v_lclaim := nerode.certify_ledger(p_session_id);
    v_env    := nerode.close_session(p_session_id, p_detail);

    RETURN v_env || jsonb_build_object(
        'ledger',          v_pack,
        'ledger_hash',     v_pack->>'ledger_hash',
        'ledger_claim_id', v_lclaim
    );
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.close_with_ledger(TEXT, JSONB) IS
    'close_session() plus the carried, certified task-state ledger embedded in '
    'the envelope. Model B can gate its first action with zero tool calls.';

-- ---------------------------------------------------------------------------
-- nerode.open_with_ledger(envelope, new_session) → JSONB  context + restored ledger
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.open_with_ledger(
    p_envelope JSONB, p_new_session_id TEXT)
RETURNS JSONB AS $$
DECLARE
    v_ctx       JSONB;
    v_restored  INTEGER := 0;
    v_hash_ok   BOOLEAN := NULL;
BEGIN
    v_ctx := nerode.open_session(p_envelope, p_new_session_id);

    IF p_envelope ? 'ledger' AND p_new_session_id IS NOT NULL THEN
        v_restored := nerode.ledger_unpack(p_new_session_id, p_envelope->'ledger');
        -- Re-verify: the restored ledger must hash to the carried ledger_hash.
        v_hash_ok := nerode.ledger_hash(p_new_session_id) IS NOT DISTINCT FROM (p_envelope->>'ledger_hash');
    END IF;

    RETURN v_ctx || jsonb_build_object(
        'ledger',          COALESCE(nerode.ledger_render(p_new_session_id), '{}'::jsonb),
        'ledger_restored', v_restored,
        'ledger_valid',    v_hash_ok,
        'ledger_claim_id', p_envelope->'ledger_claim_id'
    );
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.open_with_ledger(JSONB, TEXT) IS
    'open_session() plus restoration of the carried ledger into new_session, with '
    'ledger_hash re-verification (ledger_valid). Model B opens a populated ledger.';

-- ---------------------------------------------------------------------------
-- nerode.open_and_gate(envelope, new_session, tool, args) → JSONB
--   The headline: open the carried ledger and gate the first proposed action,
--   all with zero tool calls on Model B's side.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.open_and_gate(
    p_envelope JSONB, p_new_session_id TEXT, p_tool TEXT, p_args JSONB DEFAULT '{}')
RETURNS JSONB AS $$
DECLARE
    v_ctx      JSONB;
    v_verdict  TEXT;
    v_feedback TEXT;
    v_witness  JSONB;
    v_claim    BIGINT;
BEGIN
    v_ctx := nerode.open_with_ledger(p_envelope, p_new_session_id);

    SELECT verdict, feedback, witness INTO v_verdict, v_feedback, v_witness
    FROM nerode.policy_gate(p_new_session_id, p_tool, p_args);
    v_claim := nerode.certify_gate(p_new_session_id, p_tool, p_args);

    RETURN jsonb_build_object(
        'context',  v_ctx,
        'decision', jsonb_build_object(
            'verdict', v_verdict, 'feedback', v_feedback,
            'claim_id', v_claim, 'witness', v_witness)
    );
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.open_and_gate(JSONB, TEXT, TEXT, JSONB) IS
    'Open the carried ledger into new_session and gate the first proposed '
    'environment-changing call — Porter handoff + LedgerAgent gate as one object.';
