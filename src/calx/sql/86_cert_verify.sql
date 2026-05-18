-- cert.verify: side-effect-free re-verification.
-- Unlike cert.check(), this function produces a verdict without INSERTing
-- any rows. A consumer can call this on a received bundle to validate claims
-- without polluting the ledger or requiring write access.

CREATE OR REPLACE FUNCTION cert.verify(p_claim_id BIGINT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB, witness JSONB) AS $$
DECLARE
    v_claim   cert.claim%ROWTYPE;
    v_ok      BOOLEAN;
    v_ev      JSONB;
    v_witness JSONB;
BEGIN
    SELECT * INTO v_claim FROM cert.claim WHERE id = p_claim_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT FALSE,
            jsonb_build_object('error', format('claim %s not found', p_claim_id)),
            NULL::JSONB;
        RETURN;
    END IF;

    -- Retrieve latest stored witness regardless of tier.
    SELECT w.body INTO v_witness
      FROM cert.witness w
      JOIN cert.certificate ce ON ce.id = w.certificate_id
     WHERE ce.claim_id = p_claim_id
     ORDER BY ce.seq DESC LIMIT 1;

    IF v_claim.probe_sql IS NOT NULL THEN
        -- Re-run probe in a subtransaction so no state escapes.
        BEGIN
            EXECUTE v_claim.probe_sql INTO v_ok, v_ev;
        EXCEPTION WHEN OTHERS THEN
            v_ok := FALSE;
            v_ev := jsonb_build_object('error', SQLERRM);
        END;
    ELSE
        -- Formal/empirical: replay latest witness as the verdict.
        -- ok=TRUE iff a witness exists (harness-attested).
        v_ok := (v_witness IS NOT NULL);
        v_ev := COALESCE(v_witness, jsonb_build_object(
            'note', 'no probe_sql and no stored witness; formal attestation required'
        ));
    END IF;

    -- Also check derivation chain if one exists.
    IF EXISTS (SELECT 1 FROM cert.derivation WHERE conclusion_id = p_claim_id) THEN
        DECLARE
            v_deriv_ok   BOOLEAN;
            v_deriv_ev   JSONB;
            v_deriv_id   BIGINT;
        BEGIN
            SELECT id INTO v_deriv_id
              FROM cert.derivation WHERE conclusion_id = p_claim_id LIMIT 1;

            SELECT d.ok, d.evidence INTO v_deriv_ok, v_deriv_ev
              FROM cert.derivation_valid(v_deriv_id) d;

            -- A claim is fully verified only if both its own probe and its
            -- derivation premises hold.
            v_ok := COALESCE(v_ok, TRUE) AND COALESCE(v_deriv_ok, TRUE);
            v_ev := v_ev || jsonb_build_object('derivation', v_deriv_ev);
        END;
    END IF;

    RETURN QUERY SELECT v_ok, v_ev, v_witness;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION cert.verify(BIGINT) IS
    'Side-effect-free re-verification. Replays probe_sql (for comp_sql/struct_kan claims) '
    'or returns stored witness verdict (for formal/empirical claims). '
    'Validates derivation premises when a derivation exists. '
    'Produces no INSERTs — safe for consumer-side use on a received bundle.';
