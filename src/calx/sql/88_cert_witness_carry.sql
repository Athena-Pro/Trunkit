-- cert.witness_carry: fifth method tier.
-- Claims of this tier have probe_sql that returns THREE columns:
--   (ok BOOLEAN, evidence JSONB, witness JSONB)
-- The witness column is a structured proof term stored in cert.witness,
-- not just a verdict. Bridges comp_sql (verdict-only) and formal_external
-- (external artifact) for cases where the proof is computable in-DB.

INSERT INTO cert.method (name, claim_kind, checker_kind, description)
VALUES (
    'witness_carry',
    'computational',
    'sql',
    'Probe returns (ok, evidence, witness JSONB). The witness term is stored '
    'in cert.witness alongside the certificate, enabling consumer re-verification '
    'of the proof term directly without re-running the probe.'
)
ON CONFLICT (name) DO NOTHING;

-- Override cert.check() to handle witness_carry probes.
-- For all other methods the existing cert.check() logic is unchanged.
CREATE OR REPLACE FUNCTION cert.check_with_witness(p_claim_id BIGINT)
RETURNS BIGINT AS $$
DECLARE
    v_claim   cert.claim%ROWTYPE;
    v_ok      BOOLEAN;
    v_ev      JSONB;
    v_witness JSONB;
    v_under   JSONB;
    v_inf_id  TEXT;
    v_seq     INTEGER;
    v_cert_id BIGINT;
    v_wit_id  BIGINT;
BEGIN
    SELECT * INTO v_claim FROM cert.claim WHERE id = p_claim_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'claim % not found', p_claim_id;
    END IF;

    IF v_claim.method != 'witness_carry' THEN
        -- Delegate non-witness claims to standard cert.check().
        PERFORM cert.check(p_claim_id);
        SELECT id INTO v_cert_id FROM cert.certificate
         WHERE claim_id = p_claim_id ORDER BY seq DESC LIMIT 1;
        RETURN v_cert_id;
    END IF;

    -- Build valid_under snapshot.
    SELECT jsonb_build_object(
        'calx_schema_version', jsonb_build_object('major',1,'minor',0),
        'curry_entities', (SELECT count(*) FROM curry.constants) + (SELECT count(*) FROM curry.functions),
        'kan_objects',    (SELECT count(*) FROM kan.object)
    ) INTO v_under;

    -- Execute probe: expects (ok BOOLEAN, evidence JSONB, witness JSONB).
    BEGIN
        EXECUTE v_claim.probe_sql INTO v_ok, v_ev, v_witness;
    EXCEPTION WHEN OTHERS THEN
        v_ok      := FALSE;
        v_ev      := jsonb_build_object('error', SQLERRM);
        v_witness := NULL;
    END;

    -- Record provenance in curry.inferences.
    v_inf_id := gen_random_uuid()::text;
    INSERT INTO curry.inferences (
        inference_id, model_name, model_version,
        input_tokens, output_tokens, execution_timestamp,
        temperature_used, metadata
    ) VALUES (
        v_inf_id, 'cert-checker-model', 1,
        convert_to(
            jsonb_build_object('claim_id', p_claim_id, 'statement', v_claim.statement)::text,
            'UTF8'
        ),
        convert_to(COALESCE(v_ok::text, 'unverified'), 'UTF8'),
        now(), 0.0,
        jsonb_build_object('method', v_claim.method, 'claim_kind', v_claim.claim_kind,
                           'tier', 'witness_carry')
    );

    -- Append certificate (immutable, append-only).
    SELECT COALESCE(MAX(seq), 0) + 1 INTO v_seq
      FROM cert.certificate WHERE claim_id = p_claim_id;

    INSERT INTO cert.certificate (
        claim_id, seq, status, evidence, valid_under, checked_at, checker_inference_id
    ) VALUES (
        p_claim_id, v_seq,
        CASE WHEN v_ok IS TRUE THEN 'valid'
             WHEN v_ok IS FALSE THEN 'refuted'
             ELSE 'unverified' END,
        COALESCE(v_ev, '{}'::jsonb),
        v_under, now(), v_inf_id
    ) RETURNING id INTO v_cert_id;

    -- Store witness if probe returned one.
    IF v_witness IS NOT NULL THEN
        INSERT INTO cert.witness (certificate_id, kind, body, schema_version)
        VALUES (v_cert_id, 'term', v_witness, v_under)
        RETURNING id INTO v_wit_id;
    END IF;

    RETURN v_cert_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION cert.check_with_witness(BIGINT) IS
    'Check a claim and store its proof witness. For witness_carry claims the probe '
    'must return (ok BOOLEAN, evidence JSONB, witness JSONB); the witness is stored '
    'in cert.witness. Delegates all other method tiers to cert.check().';

-- Example witness_carry claim: calx valuation of 12 decomposes to stratified levels.
INSERT INTO cert.claim (
    subject_kind, subject_ref, statement, claim_kind, method, probe_sql
) VALUES (
    'calx_expr',
    '{"schema":"calx","n":12}'::jsonb,
    'n=12 has stratified valuation decomposition {2:2, 3:1} (p-adic level structure)',
    'computational',
    'witness_carry',
    $probe$
        SELECT
            (v2 = 2 AND v3 = 1) AS ok,
            jsonb_build_object('v2', v2, 'v3', v3, 'n', 12) AS evidence,
            jsonb_build_object(
                'kind',   'term',
                'levels', jsonb_build_object(
                    'prime_2', jsonb_build_object('valuation', v2, 'eps_level', v2),
                    'prime_3', jsonb_build_object('valuation', v3, 'eps_level', v3)
                ),
                'reconstruction', '2^2 * 3^1 = 12'
            ) AS witness
        FROM (
            SELECT
                COALESCE((SELECT exponent FROM calx.factorizations
                  WHERE n = 12 AND prime = 2), 0) AS v2,
                COALESCE((SELECT exponent FROM calx.factorizations
                  WHERE n = 12 AND prime = 3), 0) AS v3
        ) sub
    $probe$
) ON CONFLICT (statement) DO NOTHING;
