-- Step 119: certificate commitments identify claim meaning, not just its ID.
-- New certificates pin a content-addressed immutable version in valid_under,
-- which is already included in the optional ledger's certificate row hash.
-- Existing certificates are not rewritten or retrospectively declared bound.

CREATE OR REPLACE FUNCTION cert.claim_canonical_json(j JSONB)
RETURNS TEXT LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE t TEXT; result TEXT;
BEGIN
    IF j IS NULL THEN RETURN 'null'; END IF;
    t := jsonb_typeof(j);
    IF t = 'object' THEN
        SELECT COALESCE('{' || string_agg(
            to_json(k)::text || ':' || cert.claim_canonical_json(j -> k),
            ',' ORDER BY octet_length(k), convert_to(k, 'UTF8')) || '}', '{}')
          INTO result FROM jsonb_object_keys(j) AS k;
        RETURN result;
    ELSIF t = 'array' THEN
        SELECT COALESCE('[' || string_agg(cert.claim_canonical_json(e.value), ','
            ORDER BY e.ord) || ']', '[]') INTO result
          FROM jsonb_array_elements(j) WITH ORDINALITY AS e(value, ord);
        RETURN result;
    ELSIF t = 'number' THEN
        RETURN trim_scale((j #>> '{}')::numeric)::text;
    ELSE
        RETURN j::text;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION cert.claim_content(p cert.claim)
RETURNS JSONB LANGUAGE sql IMMUTABLE AS $$
    SELECT jsonb_build_object(
        'id', p.id, 'subject_kind', p.subject_kind, 'subject_ref', p.subject_ref,
        'statement', p.statement, 'claim_kind', p.claim_kind, 'method', p.method,
        'probe_sql', p.probe_sql, 'domain', p.domain)
$$;

CREATE OR REPLACE FUNCTION cert.claim_digest(p JSONB)
RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
    SELECT encode(sha256(convert_to(
        'trunkit-claim-v1' || chr(31) || cert.claim_canonical_json(jsonb_build_object(
            'id', p->'id', 'subject_kind', p->'subject_kind', 'subject_ref', p->'subject_ref',
            'statement', p->'statement', 'claim_kind', p->'claim_kind', 'method', p->'method',
            'probe_sql', p->'probe_sql', 'domain', p->'domain')), 'UTF8')), 'hex')
$$;

CREATE TABLE IF NOT EXISTS cert.claim_version (
    claim_id BIGINT NOT NULL REFERENCES cert.claim(id),
    digest TEXT NOT NULL,
    content JSONB NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (claim_id, digest),
    CHECK (digest = cert.claim_digest(content))
);

CREATE OR REPLACE FUNCTION cert.reject_claim_version_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'cert.claim_version is append-only; issue a new certificate for a new version';
END $$;

DROP TRIGGER IF EXISTS claim_version_append_only ON cert.claim_version;
CREATE TRIGGER claim_version_append_only BEFORE UPDATE OR DELETE ON cert.claim_version
    FOR EACH ROW EXECUTE FUNCTION cert.reject_claim_version_mutation();
DROP TRIGGER IF EXISTS claim_version_no_truncate ON cert.claim_version;
CREATE TRIGGER claim_version_no_truncate BEFORE TRUNCATE ON cert.claim_version
    FOR EACH STATEMENT EXECUTE FUNCTION cert.reject_claim_version_mutation();

CREATE OR REPLACE FUNCTION cert.bind_certificate_claim()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE claim_row cert.claim%ROWTYPE; content JSONB; digest TEXT;
BEGIN
    -- Hold the version stable while its certificate is issued.
    SELECT * INTO STRICT claim_row FROM cert.claim WHERE id = NEW.claim_id FOR SHARE;
    content := cert.claim_content(claim_row);
    digest := cert.claim_digest(content);
    INSERT INTO cert.claim_version (claim_id, digest, content)
        VALUES (NEW.claim_id, digest, content) ON CONFLICT DO NOTHING;
    IF jsonb_typeof(COALESCE(NEW.valid_under, '{}'::jsonb)) <> 'object' THEN
        RAISE EXCEPTION 'certificate valid_under must be a JSON object';
    END IF;
    NEW.valid_under := COALESCE(NEW.valid_under, '{}'::jsonb) || jsonb_build_object(
        'claim_digest_kind', 'trunkit-claim-v1', 'claim_digest', digest);
    RETURN NEW;
END $$;

-- PostgreSQL orders BEFORE triggers by name. Stamp the commitment before the
-- local ledger's cert_certificate_hash, without altering old hash recipes.
DROP TRIGGER IF EXISTS ab_certificate_claim_binding ON cert.certificate;
CREATE TRIGGER ab_certificate_claim_binding BEFORE INSERT ON cert.certificate
    FOR EACH ROW EXECUTE FUNCTION cert.bind_certificate_claim();

COMMENT ON TABLE cert.claim_version IS
    'Immutable semantic claim versions, addressed by digest. New certificates '
    'pin their exact version in valid_under; legacy certificates remain unbound.';
