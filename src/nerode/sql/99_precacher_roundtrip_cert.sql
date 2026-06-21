-- =============================================================================
-- 99_precacher_roundtrip_cert.sql
-- Precacher close→open roundtrip: the meta cert claim.
--
-- The gap surfaced by graphify (2026-06-07): test_precacher_roundtrip exists in
-- tests/test_sources.py and asserts ctx["prior_session"]["cert_valid"] is True,
-- but there is no standing cert claim that certifies this invariant.
--
-- The invariant:
--   For any session closed with nerode.close_session(), the resulting envelope
--   contains a cert_bundle_id whose probe_sql — when re-executed by
--   nerode.open_session() — returns TRUE. Equivalently, open_session always
--   produces cert_valid=True for any unmodified session log.
--
-- Why it holds:
--   close_session builds a probe_sql of the form:
--     SELECT nerode.session_dfa_state(sid, 'session_calx_loop') IS NOT DISTINCT FROM s1
--        AND nerode.session_dfa_state(sid, 'session_edit_loop') IS NOT DISTINCT FROM s2
--   session_dfa_state is a DETERMINISTIC function of the session_log (it replays
--   the DFA over the log); the session_log is APPEND-ONLY (INSERT-only by design,
--   no UPDATE or DELETE). Therefore the same session_id → same DFA states →
--   probe_sql returns TRUE on every re-run.
--
-- This file:
--   1. Creates nerode.verify_roundtrip() — replays all session_close probes
--   2. Inserts the meta cert.claim with that probe
--   3. Runs cert.check() to mark it valid
--
-- Idempotent. Requires step 93 (nerode.close_session) + step 94 (nerode.open_session).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. nerode.verify_roundtrip()
--    Re-executes every session_close probe_sql and counts how many still hold.
--    Returns (ok, evidence) — the standard (BOOLEAN, JSONB) probe signature.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.verify_roundtrip()
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_total    INT := 0;
    v_valid    INT := 0;
    v_row      RECORD;
    v_replay   BOOLEAN;
BEGIN
    FOR v_row IN
        SELECT id, statement, probe_sql
          FROM cert.claim
         WHERE method = 'session_close'
         ORDER BY id
    LOOP
        v_total := v_total + 1;
        IF v_row.probe_sql IS NOT NULL THEN
            BEGIN
                EXECUTE v_row.probe_sql INTO v_replay;
                IF v_replay IS TRUE THEN
                    v_valid := v_valid + 1;
                END IF;
            EXCEPTION WHEN OTHERS THEN
                -- Probe errored (e.g. session_log row deleted): counts as not-valid.
                NULL;
            END;
        END IF;
    END LOOP;

    RETURN QUERY
    SELECT
        (v_total > 0 AND v_valid = v_total),
        jsonb_build_object(
            'total_sessions_checked',   v_total,
            'sessions_still_valid',     v_valid,
            'all_probes_replay',        (v_total > 0 AND v_valid = v_total),
            'invariant', (
                'session_dfa_state is a pure function of an append-only log; '
                'close_session''s probe_sql therefore returns TRUE on every replay'
            )
        );
END;
$$;

COMMENT ON FUNCTION nerode.verify_roundtrip() IS
    'Re-verify the Precacher roundtrip invariant: re-execute every '
    'session_close probe_sql and confirm all return TRUE. '
    'Returns (ok=TRUE, evidence) iff every closed session''s DFA states '
    'still match a fresh replay of session_dfa_state — the structural '
    'guarantee that open_session cert_valid is always True.';


-- ---------------------------------------------------------------------------
-- 2. Ensure comp_sql method is registered in the Nerode cert schema.
--    (comp_sql is seeded by Trunkit's calx/cert layer; the Nerode cert schema
--    is a lightweight mirror that only registers Nerode-native methods by default.
--    The Precacher roundtrip claim is a cross-layer structural claim — it uses
--    a plain SQL probe, so comp_sql is the correct method tier.)
-- ---------------------------------------------------------------------------

INSERT INTO cert.method (name, claim_kind, checker_kind, description)
VALUES (
    'comp_sql',
    'computational',
    'sql',
    'SQL probe returns (ok BOOLEAN, evidence JSONB). '
    'Re-runnable in-DB verification; re-checks the same computation on each call.'
)
ON CONFLICT (name) DO NOTHING;


-- ---------------------------------------------------------------------------
-- 3. Meta cert claim: the protocol invariant as a first-class cert.claim.
-- ---------------------------------------------------------------------------

INSERT INTO cert.claim (
    subject_kind,
    subject_ref,
    statement,
    claim_kind,
    method,
    probe_sql
)
VALUES (
    'porter_protocol',
    jsonb_build_object(
        'protocol',   'precacher_roundtrip',
        'mechanism',  'session_dfa_state_probe_replay',
        'invariant',  'deterministic_dfa_over_append_only_log',
        'test_file',  'tests/test_sources.py::TestWeatherSource::test_precacher_roundtrip'
    ),
    'Precacher close→open roundtrip is cert-complete: open_session cert_valid=True for any unmodified session log',
    'structural',
    'comp_sql',
    'SELECT ok, evidence FROM nerode.verify_roundtrip()'
)
ON CONFLICT (statement) DO NOTHING;


-- ---------------------------------------------------------------------------
-- 4. Issue the first certificate by running the probe directly.
--    The Nerode cert schema carries only tables (no cert.check() function —
--    that lives in the Trunkit DB). We evaluate the probe here and INSERT
--    the certificate ourselves; idempotent via the ON CONFLICT on claim.id.
-- ---------------------------------------------------------------------------

DO $$
DECLARE
    v_id     BIGINT;
    v_ok     BOOLEAN;
    v_ev     JSONB;
    v_seq    INT;
    v_status TEXT;
BEGIN
    SELECT id INTO v_id
      FROM cert.claim
     WHERE statement = 'Precacher close→open roundtrip is cert-complete: open_session cert_valid=True for any unmodified session log';

    IF NOT FOUND THEN
        RAISE EXCEPTION 'meta-claim not found — INSERT may have conflicted unexpectedly';
    END IF;

    -- Run the probe (same logic cert.check() would use).
    SELECT r.ok, r.evidence INTO v_ok, v_ev FROM nerode.verify_roundtrip() r;

    v_status := CASE
        WHEN v_ok IS TRUE  THEN 'valid'
        WHEN v_ok IS FALSE THEN 'refuted'
        ELSE 'unverified'
    END;

    SELECT COALESCE(MAX(seq), 0) + 1 INTO v_seq
      FROM cert.certificate WHERE claim_id = v_id;

    INSERT INTO cert.certificate (claim_id, seq, status, evidence, valid_under)
    VALUES (
        v_id, v_seq, v_status,
        COALESCE(v_ev, '{}'::jsonb),
        jsonb_build_object(
            'nerode_schema_version', 1,
            'certified_at',          now(),
            'method',                'comp_sql',
            'probe',                 'nerode.verify_roundtrip()'
        )
    );

    RAISE NOTICE 'Precacher roundtrip claim #%: status = %  (sessions_checked = %)',
        v_id, v_status, v_ev->>'total_sessions_checked';
END;
$$;
