-- Unified model, step 104: tool-attestation axiom registry + auto-derivation.
--
-- First implementation slice of docs/DESIGN_TOOL_ATTESTATION_TIER.md (the
-- EG-VAR pattern, arXiv:2607.12650): empirical facts enter formal Lean
-- proofs as axioms named
--
--     trunkit_att_<claim_id>_<sha256_16>
--
-- where <claim_id> is a ledger claim attesting the fact and <sha256_16> is
-- the first 16 hex chars of SHA-256 of the axiom's statement text. Axiom
-- names are foreign keys: cert.attestation is the table they resolve
-- against, and a witness that declares axiom_tier='tool_attested' is bound
-- at attach time into a cert.derivation row (rule 'tool_attestation') whose
-- premises are the attesting claims. From there the existing machinery does
-- all the work: the deep check (102) degrades the formal claim when an
-- attestation is revoked or expires, signer independence (103) applies to
-- the attesting tools, and export bundles already carry the derivation.
--
-- Prover-side discipline is strict (unregistered or mismatched axioms
-- REJECT the witness attach — the ledger never holds an unbound
-- tool_attested witness); consumer-side re-checking stays three-valued
-- (a consumer that cannot resolve a binding reads the claim as unverified,
-- never refuted — that lives in the kernel_verify extension, a later slice).
--
-- The witness-kind constraint (84) is deliberately untouched: the tier is
-- declared in the witness BODY, not in a new kind.
--
-- Idempotent; additive only.

-- ---------------------------------------------------------------------------
-- 1. the registry: axiom name -> attesting claim
-- ---------------------------------------------------------------------------

-- Canonical name for an attestation axiom. IMMUTABLE: the single source of
-- truth for the naming schema on the SQL side (AxiomAudit.lean carries the
-- matching regex on the Lean side).
CREATE OR REPLACE FUNCTION cert.attestation_axiom_name(
    p_claim_id BIGINT, p_stmt_sha256 TEXT)
RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
    SELECT format('trunkit_att_%s_%s', p_claim_id, left(p_stmt_sha256, 16))
$$;

CREATE TABLE IF NOT EXISTS cert.attestation (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    axiom_name    TEXT NOT NULL UNIQUE
                  CHECK (axiom_name ~ '^trunkit_att_[0-9]+_[0-9a-f]{16}$'),
    claim_id      BIGINT NOT NULL REFERENCES cert.claim(id),
    stmt_sha256   TEXT NOT NULL CHECK (stmt_sha256 ~ '^[0-9a-f]{64}$'),
    registered_by TEXT NOT NULL DEFAULT cert.signer_identity(),
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS cert_attestation_claim_idx ON cert.attestation (claim_id);

DROP TRIGGER IF EXISTS cert_attestation_append_only ON cert.attestation;
CREATE TRIGGER cert_attestation_append_only BEFORE UPDATE OR DELETE ON cert.attestation
    FOR EACH ROW EXECUTE FUNCTION cert.reject_mutation();

COMMENT ON TABLE cert.attestation IS
    'Tool-attestation axiom registry (docs/DESIGN_TOOL_ATTESTATION_TIER.md). '
    'One row per attestation axiom a formal proof may depend on: axiom_name '
    'is trunkit_att_<claim_id>_<sha256_16>, resolving to the ledger claim '
    'that attests the empirical fact. Append-only: a name can never be '
    're-bound (the stmt-hash suffix makes re-binding a different name). '
    'Withdrawal of trust is cert.revoke on the attesting claim''s '
    'certificate, which the deep derivation check propagates.';

-- Register an attestation axiom for an effectively-valid claim.
-- Idempotent: re-registering the same (claim, stmt hash) returns the row.
CREATE OR REPLACE FUNCTION cert.register_attestation(
    p_claim_id BIGINT, p_stmt_sha256 TEXT)
RETURNS cert.attestation
LANGUAGE plpgsql AS $$
DECLARE
    v_name   TEXT := cert.attestation_axiom_name(p_claim_id, p_stmt_sha256);
    v_row    cert.attestation%ROWTYPE;
    v_status TEXT;
BEGIN
    SELECT * INTO v_row FROM cert.attestation WHERE axiom_name = v_name;
    IF FOUND THEN
        IF v_row.stmt_sha256 IS DISTINCT FROM p_stmt_sha256 THEN
            -- Same 16-char prefix, different full hash: refuse the collision.
            RAISE EXCEPTION 'cert.register_attestation: name % already bound to a different statement hash', v_name;
        END IF;
        RETURN v_row;
    END IF;

    v_status := cert.effective_status(p_claim_id);
    IF v_status IS DISTINCT FROM 'valid' THEN
        RAISE EXCEPTION 'cert.register_attestation: claim % stands % — an attestation axiom needs an effectively valid certificate',
            p_claim_id, COALESCE(v_status, 'missing');
    END IF;

    INSERT INTO cert.attestation (axiom_name, claim_id, stmt_sha256)
    VALUES (v_name, p_claim_id, p_stmt_sha256)
    RETURNING * INTO v_row;
    RETURN v_row;
END $$;

-- ---------------------------------------------------------------------------
-- 2. witness binding: tool_attested witnesses auto-assert the derivation
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION cert.bind_attested_witness()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_claim    BIGINT;
    v_entry    JSONB;
    v_att      cert.attestation%ROWTYPE;
    v_premises BIGINT[] := '{}';
BEGIN
    IF NEW.body IS NULL
       OR NEW.body ->> 'axiom_tier' IS DISTINCT FROM 'tool_attested' THEN
        RETURN NEW;   -- kernel_pure and legacy witnesses pass through untouched
    END IF;

    SELECT ce.claim_id INTO v_claim
      FROM cert.certificate ce WHERE ce.id = NEW.certificate_id;

    IF jsonb_typeof(NEW.body -> 'attestation_axioms') IS DISTINCT FROM 'array'
       OR jsonb_array_length(NEW.body -> 'attestation_axioms') = 0 THEN
        RAISE EXCEPTION 'tool_attested witness on claim % requires a non-empty attestation_axioms array', v_claim;
    END IF;

    FOR v_entry IN SELECT * FROM jsonb_array_elements(NEW.body -> 'attestation_axioms') LOOP
        SELECT * INTO v_att FROM cert.attestation
         WHERE axiom_name = v_entry ->> 'name';
        IF NOT FOUND THEN
            RAISE EXCEPTION 'attestation axiom % is not registered (cert.register_attestation first)',
                v_entry ->> 'name';
        END IF;
        IF v_att.claim_id IS DISTINCT FROM (v_entry ->> 'claim_id')::BIGINT
           OR v_att.stmt_sha256 IS DISTINCT FROM v_entry ->> 'stmt_sha256' THEN
            RAISE EXCEPTION 'attestation binding mismatch for %: witness says (claim %, sha %), registry says (claim %, sha %)',
                v_entry ->> 'name', v_entry ->> 'claim_id', v_entry ->> 'stmt_sha256',
                v_att.claim_id, v_att.stmt_sha256;
        END IF;
        v_premises := v_premises || v_att.claim_id;
    END LOOP;

    -- Canonical premise order; idempotent across witness re-attachment.
    SELECT array_agg(DISTINCT p ORDER BY p) INTO v_premises FROM unnest(v_premises) p;

    IF NOT EXISTS (SELECT 1 FROM cert.derivation
                    WHERE conclusion_id = v_claim
                      AND rule = 'tool_attestation'
                      AND premise_ids = v_premises) THEN
        INSERT INTO cert.derivation (conclusion_id, premise_ids, rule)
        VALUES (v_claim, v_premises, 'tool_attestation');
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS cert_witness_bind_attested ON cert.witness;
CREATE TRIGGER cert_witness_bind_attested AFTER INSERT ON cert.witness
    FOR EACH ROW EXECUTE FUNCTION cert.bind_attested_witness();

COMMENT ON FUNCTION cert.bind_attested_witness() IS
    'Witness-attach hook for the tool-attestation tier: a witness whose body '
    'declares axiom_tier=''tool_attested'' must list attestation_axioms that '
    'each resolve exactly (name, claim_id, stmt_sha256) against '
    'cert.attestation, and the attach auto-asserts the cert.derivation row '
    '(rule tool_attestation) binding the formal claim to its empirical '
    'premises. Rejection is prover-side discipline; consumers stay '
    'three-valued.';
