-- cert.witness: structured proof terms stored alongside certificates.
-- Every certificate can carry a witness that a consumer can inspect or replay
-- independently of re-running the original probe.

CREATE TABLE IF NOT EXISTS cert.witness (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    certificate_id BIGINT NOT NULL REFERENCES cert.certificate(id) ON DELETE CASCADE,
    kind           TEXT   NOT NULL CHECK (kind IN ('term','trace','counterexample','hash_chain','kan_diagram')),
    body           JSONB  NOT NULL,
    schema_version JSONB  NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS cert_witness_certificate_id_idx ON cert.witness (certificate_id);

COMMENT ON TABLE cert.witness IS
    'Structured proof witnesses attached to certificates. '
    'kind=term: explicit proof term; '
    'kind=trace: step-by-step computation trace; '
    'kind=counterexample: refutation witness; '
    'kind=hash_chain: formal-artifact provenance (sha256+cmd+exit); '
    'kind=kan_diagram: categorical diagram satisfying the claim.';

-- Helper: attach a witness to the latest certificate for a claim.
CREATE OR REPLACE FUNCTION cert.attach_witness(
    p_claim_id BIGINT,
    p_kind     TEXT,
    p_body     JSONB
) RETURNS BIGINT AS $$
DECLARE
    v_cert_id BIGINT;
    v_wit_id  BIGINT;
BEGIN
    SELECT id INTO v_cert_id
      FROM cert.certificate
     WHERE claim_id = p_claim_id
     ORDER BY seq DESC LIMIT 1;

    IF v_cert_id IS NULL THEN
        RAISE EXCEPTION 'No certificate found for claim_id=%', p_claim_id;
    END IF;

    INSERT INTO cert.witness (certificate_id, kind, body, schema_version)
    VALUES (
        v_cert_id, p_kind, p_body,
        jsonb_build_object(
            'calx_schema_version', (SELECT jsonb_build_object('major',1,'minor',0)),
            'kan_objects', (SELECT count(*) FROM kan.object),
            'curry_entities', (SELECT count(*) FROM curry.constants) + (SELECT count(*) FROM curry.functions)
        )
    )
    RETURNING id INTO v_wit_id;

    RETURN v_wit_id;
END;
$$ LANGUAGE plpgsql;
