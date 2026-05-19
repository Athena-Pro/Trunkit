-- cert.derivation: proof composition DAG.
-- Records that a conclusion claim is valid because a set of premise claims
-- are valid under a named inference rule. Enables chaining proofs without
-- re-running every underlying computation.

CREATE TABLE IF NOT EXISTS cert.derivation (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conclusion_id BIGINT   NOT NULL REFERENCES cert.claim(id),
    premise_ids   BIGINT[] NOT NULL,
    rule          TEXT     NOT NULL,  -- 'modus_ponens' | 'kan_lift' | 'calx_compute' | 'stratified_lift' | ...
    asserted_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS cert_derivation_conclusion_idx ON cert.derivation (conclusion_id);
CREATE INDEX IF NOT EXISTS cert_derivation_premises_idx   ON cert.derivation USING GIN (premise_ids);

COMMENT ON TABLE cert.derivation IS
    'Proof composition DAG. conclusion_id is valid because all premise_ids are valid '
    'under the named rule. Rules: '
    'modus_ponens — P, P→Q ⊢ Q (encoded as two premise claim IDs); '
    'kan_lift — conclusion follows by a functor lift recorded in kan; '
    'calx_compute — conclusion is a deterministic calx evaluation; '
    'stratified_lift — conclusion follows from stratified arithmetic valuation argument.';

-- Validate that all premises have a valid latest certificate.
CREATE OR REPLACE FUNCTION cert.derivation_valid(p_derivation_id BIGINT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB) AS $$
DECLARE
    v_deriv  cert.derivation%ROWTYPE;
    v_pid    BIGINT;
    v_status TEXT;
    v_bad    BIGINT[] := '{}';
BEGIN
    SELECT * INTO v_deriv FROM cert.derivation WHERE id = p_derivation_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT FALSE, jsonb_build_object('error', 'derivation not found');
        RETURN;
    END IF;

    FOREACH v_pid IN ARRAY v_deriv.premise_ids LOOP
        SELECT ce.status INTO v_status
          FROM cert.certificate ce
         WHERE ce.claim_id = v_pid
         ORDER BY ce.seq DESC LIMIT 1;

        IF v_status IS DISTINCT FROM 'valid' THEN
            v_bad := v_bad || v_pid;
        END IF;
    END LOOP;

    IF cardinality(v_bad) = 0 THEN
        RETURN QUERY SELECT TRUE,
            jsonb_build_object(
                'rule', v_deriv.rule,
                'premises_checked', cardinality(v_deriv.premise_ids),
                'all_valid', TRUE
            );
    ELSE
        RETURN QUERY SELECT FALSE,
            jsonb_build_object(
                'rule', v_deriv.rule,
                'invalid_premises', v_bad
            );
    END IF;
END;
$$ LANGUAGE plpgsql;
