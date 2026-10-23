-- Porter v2: immutable, certificate-bound handoffs on the Nerode database only.
-- Hashes use PostgreSQL JSONB text encoded as UTF-8; they are integrity checks,
-- not signatures. Trust comes from matching the stored closing certificate.

CREATE OR REPLACE FUNCTION nerode.porter_sha256(p_value JSONB)
RETURNS TEXT LANGUAGE sql IMMUTABLE STRICT AS $$
    SELECT encode(sha256(convert_to(p_value::text, 'UTF8')), 'hex');
$$;

CREATE TABLE IF NOT EXISTS nerode.porter_envelopes (
    envelope_hash TEXT PRIMARY KEY CHECK (envelope_hash ~ '^[0-9a-f]{64}$'),
    envelope JSONB NOT NULL,
    payloads JSONB NOT NULL,
    certificate_id BIGINT NOT NULL REFERENCES cert.certificate(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION nerode.porter_immutable()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Porter snapshots are append-only; close a new version instead';
END;
$$;
DROP TRIGGER IF EXISTS porter_snapshot_immutable ON nerode.porter_envelopes;
CREATE TRIGGER porter_snapshot_immutable BEFORE UPDATE OR DELETE
    ON nerode.porter_envelopes FOR EACH ROW EXECUTE FUNCTION nerode.porter_immutable();

DROP TRIGGER IF EXISTS porter_snapshot_no_truncate ON nerode.porter_envelopes;
CREATE TRIGGER porter_snapshot_no_truncate BEFORE TRUNCATE
    ON nerode.porter_envelopes FOR EACH STATEMENT EXECUTE FUNCTION nerode.porter_immutable();

CREATE TABLE IF NOT EXISTS nerode.porter_claims (
    envelope_hash TEXT PRIMARY KEY REFERENCES nerode.porter_envelopes(envelope_hash),
    receiver_session TEXT NOT NULL,
    claim_token TEXT NOT NULL,
    claimed_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    released_at TIMESTAMPTZ
);

-- Snapshot behavioural warnings at close. These are observations about the
-- prior trace, not a certificate that the receiver's proposed action is safe.
CREATE OR REPLACE FUNCTION nerode.porter_health(p_session_id TEXT)
RETURNS JSONB LANGUAGE sql STABLE AS $$
    WITH traces AS (
        SELECT alphabet, string_agg(symbol, '' ORDER BY seq) AS input
        FROM nerode.cybernetic_log WHERE session_id = p_session_id GROUP BY alphabet
    ), candidates AS (
        SELECT a.id, a.name, a.provenance,
               nerode.run_to_state(a.id, t.input) AS state_id
        FROM traces t JOIN nerode.alphabets al ON al.name = t.alphabet
        JOIN nerode.automata a ON a.alphabet_id = al.id
        WHERE a.name IN ('metric_oscillate', 'metric_rise_3', 'homeostasis_alarm_5')
           OR a.name ~ '^dead_time_[0-9]+$'
        UNION ALL
        SELECT a.id, a.name, a.provenance,
               nerode.session_dfa_state(p_session_id, a.name)
        FROM nerode.automata a
        WHERE a.name IN ('session_calx_loop', 'session_edit_loop')
    )
    SELECT jsonb_build_object('warnings', COALESCE(jsonb_agg(
        jsonb_build_object('detector', c.name, 'state_id', c.state_id,
                          'label', s.label, 'pattern', c.provenance->>'pattern',
                          'hint', c.provenance->>'shortcut') ORDER BY c.name)
        FILTER (WHERE s.is_accepting), '[]'::jsonb))
    FROM candidates c LEFT JOIN nerode.states s
        ON s.automaton_id = c.id AND s.state_id = c.state_id;
$$;

CREATE OR REPLACE FUNCTION nerode.porter_manifest(p_payloads JSONB)
RETURNS JSONB LANGUAGE sql IMMUTABLE AS $$
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
        'key', key, 'sha256', nerode.porter_sha256(value),
        'bytes', octet_length(convert_to(value::text, 'UTF8'))
    ) ORDER BY key), '[]'::jsonb) FROM jsonb_each(p_payloads);
$$;

CREATE OR REPLACE FUNCTION nerode.porter_close(
    p_session_id TEXT, p_options JSONB DEFAULT '{}')
RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
    v_keys JSONB;
    v_payloads JSONB := '{}'::jsonb;
    v_row RECORD;
    v_body JSONB;
    v_legacy JSONB;
    v_detail JSONB;
    v_envelope JSONB;
    v_content_hash TEXT;
    v_envelope_hash TEXT;
    v_mode TEXT := COALESCE(p_options->>'mode', 'reopenable');
    v_budget INT := COALESCE((p_options->>'max_envelope_bytes')::int, 65536);
    v_closed TIMESTAMPTZ := clock_timestamp();
    v_created TIMESTAMPTZ;
    v_with_ledger BOOLEAN := COALESCE((p_options->>'include_ledger')::boolean, TRUE);
BEGIN
    IF p_session_id IS NULL OR length(trim(p_session_id)) = 0 THEN
        RAISE EXCEPTION 'sender session is required';
    END IF;
    IF v_mode NOT IN ('reopenable', 'claim_once') THEN
        RAISE EXCEPTION 'Porter mode must be reopenable or claim_once';
    END IF;
    IF v_budget NOT BETWEEN 1024 AND 1048576 THEN
        RAISE EXCEPTION 'max_envelope_bytes must be between 1024 and 1048576';
    END IF;
    -- Serialise re-closes of the same sender (certificate sequence allocation).
    PERFORM pg_advisory_xact_lock(hashtextextended('nerode.porter:' || p_session_id, 0));
    IF p_options ? 'cache_keys' THEN
        IF jsonb_typeof(p_options->'cache_keys') IS DISTINCT FROM 'array'
           OR EXISTS (SELECT 1 FROM jsonb_array_elements(p_options->'cache_keys') e
                      WHERE jsonb_typeof(e) <> 'string') THEN
            RAISE EXCEPTION 'cache_keys must be an array of strings';
        END IF;
        SELECT COALESCE(jsonb_agg(k ORDER BY k), '[]'::jsonb) INTO v_keys
        FROM (SELECT DISTINCT jsonb_array_elements_text(p_options->'cache_keys') k) x;
    ELSE
        SELECT COALESCE(jsonb_agg(seq_key ORDER BY seq_key), '[]'::jsonb) INTO v_keys
        FROM nerode.session_cache_tags WHERE session_id = p_session_id;
    END IF;
    -- Lock the source values until both snapshot and certificate are recorded.
    FOR v_row IN SELECT seq_key, result FROM nerode.sequence_cache
        WHERE seq_key IN (SELECT jsonb_array_elements_text(v_keys)) ORDER BY seq_key FOR SHARE
    LOOP
        v_payloads := v_payloads || jsonb_build_object(v_row.seq_key, v_row.result);
    END LOOP;
    IF jsonb_array_length(v_keys) <> (SELECT count(*) FROM jsonb_object_keys(v_payloads)) THEN
        RAISE EXCEPTION 'one or more requested cache keys do not exist';
    END IF;
    SELECT COALESCE(min(logged_at), v_closed) INTO v_created
    FROM nerode.session_log WHERE session_id = p_session_id;
    v_body := jsonb_build_object(
        'envelope_v', 2, 'sender_session', p_session_id,
        'intended_receiver', p_options->>'intended_receiver',
        'created_at', v_created, 'closed_at', v_closed,
        'mode', v_mode, 'include_ledger', v_with_ledger,
        'attention_hint', COALESCE(p_options->>'attention_hint',
            format('%s cached key(s); inspect manifest and health before acting', jsonb_array_length(v_keys))),
        'payload_manifest', nerode.porter_manifest(v_payloads),
        'health', nerode.porter_health(p_session_id));
    v_content_hash := nerode.porter_sha256(v_body);
    v_detail := jsonb_build_object(
        'cache_keys', v_keys, 'attention_hint', v_body->>'attention_hint',
        'trunkit', COALESCE(p_options->'trunkit', '{}'::jsonb) || jsonb_build_object(
            'porter', jsonb_build_object('envelope_v', 2, 'content_hash', v_content_hash)));
    IF v_with_ledger THEN
        v_legacy := nerode.close_with_ledger(p_session_id, v_detail);
    ELSE
        v_legacy := nerode.close_session(p_session_id, v_detail);
    END IF;
    v_envelope := v_body || jsonb_build_object('content_hash', v_content_hash, 'session', v_legacy);
    v_envelope_hash := nerode.porter_sha256(v_envelope);
    v_envelope := v_envelope || jsonb_build_object('envelope_hash', v_envelope_hash);
    IF octet_length(convert_to(v_envelope::text, 'UTF8')) > v_budget THEN
        RAISE EXCEPTION 'Porter manifest envelope exceeds byte budget; select fewer cache keys';
    END IF;
    INSERT INTO nerode.porter_envelopes(envelope_hash, envelope, payloads, certificate_id)
    VALUES (v_envelope_hash, v_envelope, v_payloads,
            (v_legacy->>'cert_certificate_id')::bigint);
    RETURN v_envelope;
END;
$$;

CREATE OR REPLACE FUNCTION nerode.porter_verify(p_envelope JSONB)
RETURNS BOOLEAN LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_stored nerode.porter_envelopes%ROWTYPE;
    v_body JSONB;
    v_binding JSONB;
BEGIN
    IF p_envelope->>'envelope_v' IS DISTINCT FROM '2' THEN RETURN FALSE; END IF;
    SELECT * INTO v_stored FROM nerode.porter_envelopes
    WHERE envelope_hash = p_envelope->>'envelope_hash';
    IF NOT FOUND OR v_stored.envelope IS DISTINCT FROM p_envelope
       OR v_stored.certificate_id IS DISTINCT FROM (p_envelope->'session'->>'cert_certificate_id')::bigint
       OR p_envelope->>'sender_session' IS DISTINCT FROM p_envelope->'session'->>'session_id'
       OR nerode.porter_sha256(p_envelope - 'envelope_hash') IS DISTINCT FROM p_envelope->>'envelope_hash'
       OR nerode.porter_manifest(v_stored.payloads) IS DISTINCT FROM p_envelope->'payload_manifest'
       OR nerode.verify_handoff(p_envelope->'session') IS NOT TRUE THEN
        RETURN FALSE;
    END IF;
    IF (p_envelope->>'include_ledger')::boolean
       AND nerode.verify_carried_ledger(p_envelope->'session') IS NOT TRUE THEN
        RETURN FALSE;
    END IF;
    v_body := p_envelope - ARRAY['content_hash', 'envelope_hash', 'session'];
    SELECT valid_under->'trunkit'->'porter' INTO v_binding
    FROM cert.certificate WHERE id = v_stored.certificate_id AND status = 'valid';
    RETURN v_binding IS NOT DISTINCT FROM jsonb_build_object(
        'envelope_v', 2, 'content_hash', nerode.porter_sha256(v_body))
        AND v_binding->>'content_hash' IS NOT DISTINCT FROM p_envelope->>'content_hash';
EXCEPTION WHEN OTHERS THEN
    RETURN FALSE;
END;
$$;

CREATE OR REPLACE FUNCTION nerode.porter_open(
    p_envelope JSONB, p_receiver_session TEXT DEFAULT NULL,
    p_payload_keys JSONB DEFAULT '[]', p_max_payload_bytes INT DEFAULT 65536,
    p_claim_token TEXT DEFAULT NULL)
RETURNS JSONB LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_resolved JSONB := '{}'::jsonb;
    v_payloads JSONB;
    v_context JSONB;
    v_missing JSONB;
BEGIN
    IF p_envelope->>'envelope_v' IS DISTINCT FROM '2' THEN
        RAISE EXCEPTION 'Unsupported Porter envelope version; v1 requires Precacher.open()';
    END IF;
    IF p_max_payload_bytes NOT BETWEEN 0 AND 1048576 THEN
        RAISE EXCEPTION 'max_payload_bytes must be between 0 and 1048576';
    END IF;
    IF jsonb_typeof(p_payload_keys) IS DISTINCT FROM 'array'
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(p_payload_keys) e WHERE jsonb_typeof(e) <> 'string') THEN
        RAISE EXCEPTION 'payload_keys must be an array of strings';
    END IF;
    IF nerode.porter_verify(p_envelope) IS NOT TRUE THEN
        RETURN jsonb_build_object('envelope_v', 2, 'verification', 'refuted',
            'prior_session', jsonb_build_object('cert_valid', FALSE),
            'payload_manifest', '[]'::jsonb, 'resolved', '{}'::jsonb,
            'error', 'envelope, snapshot or certificate verification failed');
    END IF;
    IF p_envelope->>'intended_receiver' IS NOT NULL
       AND p_envelope->>'intended_receiver' IS DISTINCT FROM p_receiver_session THEN
        RETURN jsonb_build_object('envelope_v', 2, 'verification', 'refuted',
            'prior_session', jsonb_build_object('cert_valid', FALSE),
            'payload_manifest', '[]'::jsonb, 'resolved', '{}'::jsonb,
            'error', 'receiver session does not match intended_receiver');
    END IF;
    IF p_envelope->>'mode' = 'claim_once' AND jsonb_array_length(p_payload_keys) > 0
       AND NOT EXISTS (SELECT 1 FROM nerode.porter_claims
            WHERE envelope_hash = p_envelope->>'envelope_hash'
              AND receiver_session = p_receiver_session AND claim_token = p_claim_token
              AND released_at IS NULL AND expires_at > clock_timestamp()) THEN
        RETURN jsonb_build_object('envelope_v', 2, 'verification', 'refuted',
            'prior_session', jsonb_build_object('cert_valid', TRUE),
            'payload_manifest', p_envelope->'payload_manifest', 'resolved', '{}'::jsonb,
            'error', 'active claim token required to resolve payloads');
    END IF;
    SELECT payloads INTO v_payloads FROM nerode.porter_envelopes
    WHERE envelope_hash = p_envelope->>'envelope_hash';
    SELECT COALESCE(jsonb_agg(k), '[]'::jsonb) INTO v_missing
    FROM jsonb_array_elements_text(p_payload_keys) k WHERE NOT v_payloads ? k;
    IF jsonb_array_length(v_missing) > 0 THEN
        RAISE EXCEPTION 'requested payload keys are absent from this envelope: %', v_missing;
    END IF;
    SELECT COALESCE(jsonb_object_agg(key, value), '{}'::jsonb) INTO v_resolved
    FROM jsonb_each(v_payloads) WHERE key IN (SELECT jsonb_array_elements_text(p_payload_keys));
    v_context := jsonb_build_object(
        'envelope_v', 2, 'envelope_hash', p_envelope->>'envelope_hash',
        'verification', 'valid', 'prior_session', jsonb_build_object(
            'session_id', p_envelope->>'sender_session', 'cert_valid', TRUE,
            'cert_claim_id', p_envelope->'session'->'cert_bundle_id',
            'attention_hint', p_envelope->>'attention_hint'),
        'payload_manifest', p_envelope->'payload_manifest',
        'health', p_envelope->'health', 'mode', p_envelope->>'mode',
        'ledger_valid', CASE WHEN (p_envelope->>'include_ledger')::boolean THEN TRUE ELSE NULL END,
        'resolved', v_resolved, 'payload_budget_exceeded', FALSE);
    IF jsonb_array_length(p_payload_keys) > 0
       AND octet_length(convert_to(v_resolved::text, 'UTF8')) > p_max_payload_bytes THEN
        v_context := v_context || jsonb_build_object('resolved', '{}'::jsonb,
            'payload_budget_exceeded', TRUE, 'error', 'selected payloads exceed byte budget');
    END IF;
    -- Audit/replay opens stay read-only even in claim_once mode. Claim ownership
    -- governs work pickup; possession of a token does not restrict later audits.
    RETURN v_context;
END;
$$;

CREATE OR REPLACE FUNCTION nerode.porter_claim(
    p_envelope JSONB, p_receiver_session TEXT, p_lease_seconds INT DEFAULT 300)
RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
    v_token TEXT := replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', '');
    v_claim nerode.porter_claims%ROWTYPE;
    v_context JSONB;
BEGIN
    IF p_receiver_session IS NULL OR length(trim(p_receiver_session)) = 0 THEN
        RAISE EXCEPTION 'receiver session is required';
    END IF;
    IF p_lease_seconds NOT BETWEEN 1 AND 86400 THEN
        RAISE EXCEPTION 'lease_seconds must be between 1 and 86400';
    END IF;
    v_context := nerode.porter_open(p_envelope, p_receiver_session);
    IF v_context->>'verification' IS DISTINCT FROM 'valid' THEN RETURN v_context; END IF;
    IF p_envelope->>'mode' IS DISTINCT FROM 'claim_once' THEN
        RETURN jsonb_build_object('claimed', FALSE, 'error', 'envelope mode is reopenable');
    END IF;
    INSERT INTO nerode.porter_claims AS c
        (envelope_hash, receiver_session, claim_token, claimed_at, expires_at, released_at)
    VALUES (p_envelope->>'envelope_hash', p_receiver_session, v_token, clock_timestamp(),
            clock_timestamp() + make_interval(secs => p_lease_seconds), NULL)
    ON CONFLICT (envelope_hash) DO UPDATE SET
        receiver_session = EXCLUDED.receiver_session, claim_token = EXCLUDED.claim_token,
        claimed_at = clock_timestamp(),
        expires_at = clock_timestamp() + make_interval(secs => p_lease_seconds), released_at = NULL
    WHERE c.expires_at <= clock_timestamp() OR c.released_at IS NOT NULL
    RETURNING * INTO v_claim;
    IF NOT FOUND THEN RETURN jsonb_build_object('claimed', FALSE, 'error', 'lease already held'); END IF;
    RETURN jsonb_build_object('claimed', TRUE, 'envelope_hash', v_claim.envelope_hash,
        'receiver_session', v_claim.receiver_session, 'claim_token', v_claim.claim_token,
        'claimed_at', v_claim.claimed_at, 'expires_at', v_claim.expires_at);
END;
$$;

CREATE OR REPLACE FUNCTION nerode.porter_release(p_envelope JSONB, p_claim_token TEXT)
RETURNS JSONB LANGUAGE plpgsql AS $$
DECLARE
    v_released BOOLEAN;
    v_expires TIMESTAMPTZ;
BEGIN
    IF nerode.porter_verify(p_envelope) IS NOT TRUE THEN
        RETURN jsonb_build_object('released', FALSE, 'error', 'envelope verification failed');
    END IF;
    SELECT expires_at INTO v_expires FROM nerode.porter_claims
    WHERE envelope_hash = p_envelope->>'envelope_hash' AND claim_token = p_claim_token
      AND released_at IS NULL FOR UPDATE;
    IF NOT FOUND OR v_expires <= clock_timestamp() THEN
        RETURN jsonb_build_object('released', FALSE);
    END IF;
    UPDATE nerode.porter_claims SET released_at = clock_timestamp()
    WHERE envelope_hash = p_envelope->>'envelope_hash' AND claim_token = p_claim_token
      AND released_at IS NULL;
    v_released := FOUND;
    RETURN jsonb_build_object('released', v_released);
END;
$$;
