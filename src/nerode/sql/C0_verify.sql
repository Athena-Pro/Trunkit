-- =============================================================================
--  nerode — Step C0: Unified witness-kind re-verification  [Phase 6]
--
--  nerode.verify(claim_id) is the nerode-side analogue of calx cert.verify
--  (86_cert_verify.sql): side-effect-free, no INSERTs, safe for untrusted
--  callers. It teaches the verifier the witness kinds the new layers emit, so a
--  consumer re-checks them through one path:
--
--      payoff_trace    → nerode.replay_payoff_trace(product, witness)   [A4/A5]
--      gate_decision   → nerode.replay_gate(witness)                    [B2/B3]
--      ledger_snapshot → nerode.replay_ledger(witness)                  [B4]
--      trace (session) → re-run probe_sql (DFA replay)                  [93]
--      else            → probe_sql if present, else "witness exists"
--
--  Why here and not trunk's cert.verify: these claims live in the NERODE database
--  (separate instance, port 5435); the replay functions are nerode functions.
--  Trunk's cert.verify stays the verifier for trunk-domain (calx/kan) claims —
--  the two-database trust isolation (see README "Why two databases?") means each
--  side re-verifies its own ledger.
--
--  Returns (ok, evidence, witness):
--    ok = TRUE   the witness faithfully re-verifies the recorded claim
--    ok = FALSE  the witness does NOT support the claim (stale/tampered) ⇒ refuted
--    ok = NULL   indeterminate (missing dependency, e.g. product dropped) ⇒ unverified
--
--  Depends on: A4/A5 (replay_payoff_trace), B2/B3 (replay_gate), B4 (replay_ledger),
--  cert stub (00_bootstrap). Idempotent.
--  NOTE: starter file — not yet executed against a live database.
-- =============================================================================

CREATE OR REPLACE FUNCTION nerode.verify(p_claim_id BIGINT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB, witness JSONB) AS $$
DECLARE
    v_claim    cert.claim%ROWTYPE;
    v_kind     TEXT;
    v_witness  JSONB;
    v_status   TEXT;
    v_ok       BOOLEAN;
    v_ev       JSONB;
    v_product  BIGINT;
    v_rec      NUMERIC;
BEGIN
    SELECT * INTO v_claim FROM cert.claim WHERE id = p_claim_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT FALSE,
            jsonb_build_object('error', format('claim %s not found', p_claim_id)),
            NULL::jsonb;
        RETURN;
    END IF;

    -- Latest witness (kind + body) and the recorded certificate status.
    SELECT w.kind, w.body, ce.status
      INTO v_kind, v_witness, v_status
      FROM cert.witness w
      JOIN cert.certificate ce ON ce.id = w.certificate_id
     WHERE ce.claim_id = p_claim_id
     ORDER BY ce.seq DESC
     LIMIT 1;

    -- ── Dispatch by witness kind ──────────────────────────────────────────
    BEGIN
        IF v_kind = 'payoff_trace' THEN
            v_product := COALESCE(
                NULLIF(v_witness->>'product_id','')::bigint,
                (v_claim.subject_ref->>'automaton_id')::bigint);
            SELECT r.ok, r.recomputed INTO v_ok, v_rec
              FROM nerode.replay_payoff_trace(v_product, v_witness) r;
            v_ev := jsonb_build_object('kind','payoff_trace',
                       'product_id', v_product,
                       'claimed', v_witness->'value', 'recomputed', v_rec);

        ELSIF v_kind = 'gate_decision' THEN
            v_ok := nerode.replay_gate(v_witness);
            v_ev := jsonb_build_object('kind','gate_decision',
                       'verdict', v_witness->>'verdict',
                       'recorded_status', v_status);

        ELSIF v_kind = 'ledger_snapshot' THEN
            v_ok := nerode.replay_ledger(v_witness);
            v_ev := jsonb_build_object('kind','ledger_snapshot',
                       'ledger_hash', v_witness->>'ledger_hash',
                       'entries', jsonb_array_length(COALESCE(v_witness->'entries','[]'::jsonb)));

        ELSIF v_claim.probe_sql IS NOT NULL THEN
            -- trace (session_close) and any other probe-backed claim.
            EXECUTE v_claim.probe_sql INTO v_ok;
            v_ev := jsonb_build_object('kind', COALESCE(v_kind,'probe'),
                       'recorded_status', v_status, 'via','probe_sql');

        ELSE
            -- No probe and unknown kind: attest only that a witness exists.
            v_ok := (v_witness IS NOT NULL);
            v_ev := jsonb_build_object('kind', COALESCE(v_kind,'none'),
                       'note','no probe_sql and no kind dispatcher; witness-existence only');
        END IF;
    EXCEPTION WHEN OTHERS THEN
        -- Missing dependency (e.g. product dropped) ⇒ indeterminate, not refuted.
        v_ok := NULL;
        v_ev := jsonb_build_object('kind', COALESCE(v_kind,'none'), 'error', SQLERRM);
    END;

    RETURN QUERY SELECT v_ok, v_ev, v_witness;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.verify(BIGINT) IS
    'Side-effect-free re-verification of a nerode cert claim, dispatching by '
    'witness kind (payoff_trace/gate_decision/ledger_snapshot) to the matching '
    'replay function, else probe_sql. ok=TRUE re-verifies, FALSE=refuted, '
    'NULL=unverified. No INSERTs — safe for consumer-side use.';

-- ---------------------------------------------------------------------------
-- nerode.verify_status(claim_id) → TEXT   convenience: 'valid'|'refuted'|'unverified'
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.verify_status(p_claim_id BIGINT)
RETURNS TEXT
LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN ok IS TRUE  THEN 'valid'
        WHEN ok IS FALSE THEN 'refuted'
        ELSE 'unverified'
    END
    FROM nerode.verify(p_claim_id);
$$;
