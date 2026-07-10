-- Certificate lifecycle, step 100: revocation, validity windows, signer identity.
--
-- Turns a certificate from a permanent ledger entry into a *revocable,
-- expirable, identity-bound* attestation (the SEB / aiAuthZ gap from the
-- 2026-06/07 arXiv reviews), without touching the append-only discipline:
--
--   * REVOCATION is an appended event in its own append-only table, never a
--     mutation of the certificate row. Revoking targets ONE certificate (by
--     id); re-attesting a claim appends a fresh certificate seq which is not
--     revoked — that is the re-keying/epoch semantics.
--   * VALIDITY WINDOWS live inside `valid_under` (keys `valid_from` /
--     `valid_until`, ISO timestamps). `valid_under` is already committed to
--     by the ledger row_hash and already travels in bundles, so windows are
--     tamper-evident with NO change to the hash recipe. Set
--     `SET trunkit.cert_ttl = '30 days'` before cert.check() to stamp one.
--   * SIGNER IDENTITY is captured at INSERT from the session GUC
--     `trunkit.signer` (falling back to current_user). This is an *identity
--     claim* recorded in the provenance trail, not a cryptographic proof —
--     Ed25519 per-record signatures remain design B1 in SECURITY.md.
--
-- Three-valued honesty: a revoked or expired certificate is NOT a refutation
-- of the claim. cert.standing exposes `effective_status` ('revoked' /
-- 'expired' when applicable); cert.verify stops counting witnesses that ride
-- on revoked certificates (=> UNVERIFIED, never a fake VALID).
--
-- Idempotent; additive only.

-- ---------------------------------------------------------------------------
-- 0. shared helpers
-- ---------------------------------------------------------------------------

-- Same law as the local ledger overlay; harmless double-definition.
CREATE OR REPLACE FUNCTION cert.reject_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'cert.%: append-only — % is not allowed', TG_TABLE_NAME, TG_OP;
END $$;

-- Who is acting in this session: GUC `trunkit.signer` if set, else the DB role.
CREATE OR REPLACE FUNCTION cert.signer_identity()
RETURNS TEXT LANGUAGE sql STABLE AS $$
    SELECT COALESCE(NULLIF(current_setting('trunkit.signer', true), ''), current_user)
$$;

-- ---------------------------------------------------------------------------
-- 1. signer identity on certificates (additive; outside the v1 hash preimage,
--    which is domain-tagged 'trunkit-cert-v1' and must stay reproducible)
-- ---------------------------------------------------------------------------

ALTER TABLE cert.certificate ADD COLUMN IF NOT EXISTS signer_id TEXT;

CREATE OR REPLACE FUNCTION cert.stamp_signer()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.signer_id := COALESCE(NEW.signer_id, cert.signer_identity());
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS cert_certificate_stamp_signer ON cert.certificate;
CREATE TRIGGER cert_certificate_stamp_signer BEFORE INSERT ON cert.certificate
    FOR EACH ROW EXECUTE FUNCTION cert.stamp_signer();

-- ---------------------------------------------------------------------------
-- 2. revocation — an appended event, one per certificate
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS cert.revocation (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    certificate_id BIGINT NOT NULL UNIQUE REFERENCES cert.certificate(id),
    reason         TEXT NOT NULL,
    evidence       JSONB NOT NULL DEFAULT '{}'::jsonb,
    revoked_by     TEXT NOT NULL DEFAULT cert.signer_identity(),
    revoked_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS cert_revocation_append_only ON cert.revocation;
CREATE TRIGGER cert_revocation_append_only BEFORE UPDATE OR DELETE ON cert.revocation
    FOR EACH ROW EXECUTE FUNCTION cert.reject_mutation();

-- Revoke one certificate. Idempotent: revoking twice returns the first event.
CREATE OR REPLACE FUNCTION cert.revoke(
    p_certificate_id BIGINT, p_reason TEXT, p_evidence JSONB DEFAULT '{}'::jsonb)
RETURNS cert.revocation LANGUAGE plpgsql AS $$
DECLARE v_row cert.revocation%ROWTYPE;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM cert.certificate WHERE id = p_certificate_id) THEN
        RAISE EXCEPTION 'cert.revoke: no certificate %', p_certificate_id;
    END IF;
    SELECT * INTO v_row FROM cert.revocation WHERE certificate_id = p_certificate_id;
    IF FOUND THEN
        RETURN v_row;   -- already revoked; revocation is monotone
    END IF;
    INSERT INTO cert.revocation (certificate_id, reason, evidence)
    VALUES (p_certificate_id, p_reason, COALESCE(p_evidence, '{}'::jsonb))
    RETURNING * INTO v_row;
    RETURN v_row;
END $$;

-- Revoke the LATEST certificate of a claim (the common prover gesture).
CREATE OR REPLACE FUNCTION cert.revoke_claim(
    p_claim_id BIGINT, p_reason TEXT, p_evidence JSONB DEFAULT '{}'::jsonb)
RETURNS cert.revocation LANGUAGE plpgsql AS $$
DECLARE v_cert_id BIGINT;
BEGIN
    SELECT id INTO v_cert_id FROM cert.certificate
     WHERE claim_id = p_claim_id ORDER BY seq DESC LIMIT 1;
    IF v_cert_id IS NULL THEN
        RAISE EXCEPTION 'cert.revoke_claim: claim % has no certificate', p_claim_id;
    END IF;
    RETURN cert.revoke(v_cert_id, p_reason, p_evidence);
END $$;

-- ---------------------------------------------------------------------------
-- 3. standing with lifecycle: appended columns only (view contract preserved)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW cert.standing AS
SELECT DISTINCT ON (cl.id)
       cl.id            AS claim_id,
       cl.statement,
       cl.claim_kind,
       cl.method,
       ce.seq,
       COALESCE(ce.status, 'unchecked') AS status,
       ce.checked_at,
       ce.evidence,
       ce.valid_under,
       ce.signer_id,
       rv.revoked_at,
       rv.reason        AS revoke_reason,
       (ce.valid_under ->> 'valid_from')::timestamptz  AS valid_from,
       (ce.valid_under ->> 'valid_until')::timestamptz AS valid_until,
       CASE
           WHEN ce.id IS NULL                    THEN 'unchecked'
           WHEN rv.certificate_id IS NOT NULL    THEN 'revoked'
           WHEN ce.status = 'valid'
                AND (ce.valid_under ->> 'valid_until') IS NOT NULL
                AND (ce.valid_under ->> 'valid_until')::timestamptz < now()
                                                 THEN 'expired'
           ELSE ce.status
       END AS effective_status
  FROM cert.claim cl
  LEFT JOIN cert.certificate ce ON ce.claim_id = cl.id
  LEFT JOIN cert.revocation  rv ON rv.certificate_id = ce.id
 ORDER BY cl.id, ce.seq DESC NULLS LAST;

-- ---------------------------------------------------------------------------
-- 4. cert.verify: witnesses on revoked certificates no longer count.
--    (Base = step 86; the local kernel overlay re-overrides with its superset.)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION cert.verify(p_claim_id BIGINT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB, witness JSONB) AS $$
DECLARE
    v_claim   cert.claim%ROWTYPE;
    v_ok      BOOLEAN;
    v_ev      JSONB;
    v_witness JSONB;
    v_life    JSONB;
BEGIN
    SELECT * INTO v_claim FROM cert.claim WHERE id = p_claim_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT FALSE,
            jsonb_build_object('error', format('claim %s not found', p_claim_id)),
            NULL::JSONB;
        RETURN;
    END IF;

    -- Latest stored witness, skipping any that ride on a revoked certificate.
    SELECT w.body INTO v_witness
      FROM cert.witness w
      JOIN cert.certificate ce ON ce.id = w.certificate_id
     WHERE ce.claim_id = p_claim_id
       AND NOT EXISTS (SELECT 1 FROM cert.revocation r WHERE r.certificate_id = ce.id)
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
        -- Formal/empirical: witness presence is the verdict; no witness means
        -- UNVERIFIED (NULL), never refuted — absence of evidence is not
        -- refutation.
        v_ok := CASE WHEN v_witness IS NOT NULL THEN TRUE END;
        v_ev := COALESCE(v_witness, jsonb_build_object(
            'note', 'no probe_sql and no stored witness; formal attestation required'
        ));
    END IF;

    -- Surface lifecycle state of the LATEST certificate (informational; probe
    -- replay is fresh evidence and stands on its own).
    SELECT jsonb_build_object('revoked_at', rv.revoked_at, 'reason', rv.reason)
      INTO v_life
      FROM cert.certificate ce
      JOIN cert.revocation rv ON rv.certificate_id = ce.id
     WHERE ce.claim_id = p_claim_id
     ORDER BY ce.seq DESC LIMIT 1;
    IF v_life IS NOT NULL THEN
        v_ev := v_ev || jsonb_build_object('lifecycle', v_life || '{"state":"revoked"}'::jsonb);
    END IF;

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

            v_ok := COALESCE(v_ok, TRUE) AND COALESCE(v_deriv_ok, TRUE);
            v_ev := v_ev || jsonb_build_object('derivation', v_deriv_ev);
        END;
    END IF;

    RETURN QUERY SELECT v_ok, v_ev, v_witness;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 5. export_bundle v1 + lifecycle (the ledger overlay's v2 carries the same
--    keys; a bundle consumer treats `revocation` as verdict-degrading)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION cert.export_bundle(p_claim_ids BIGINT[])
RETURNS JSONB AS $$
WITH
latest_cert AS (
    SELECT DISTINCT ON (claim_id)
           claim_id, id AS cert_id, seq, status, evidence, valid_under,
           checked_at, signer_id
      FROM cert.certificate
     WHERE claim_id = ANY(p_claim_ids)
     ORDER BY claim_id, seq DESC
),
latest_witness AS (
    SELECT DISTINCT ON (w.certificate_id)
           w.certificate_id, w.kind AS witness_kind, w.body AS witness_body
      FROM cert.witness w
      JOIN latest_cert lc ON lc.cert_id = w.certificate_id
     ORDER BY w.certificate_id, w.id DESC
),
derivations AS (
    SELECT d.conclusion_id, d.premise_ids, d.rule, d.asserted_at
      FROM cert.derivation d
     WHERE d.conclusion_id = ANY(p_claim_ids)
),
artifacts AS (
    SELECT a.claim_id, a.kind AS artifact_kind, a.path,
           a.sha256, a.checker_cmd, a.registered_at
      FROM cert.artifact a
     WHERE a.claim_id = ANY(p_claim_ids)
),
revocations AS (
    SELECT r.certificate_id, r.reason, r.revoked_by, r.revoked_at
      FROM cert.revocation r
      JOIN latest_cert lc ON lc.cert_id = r.certificate_id
)
SELECT jsonb_build_object(
    'trunk_bundle_version', 1,
    'exported_at', now(),
    'claims', jsonb_agg(
        jsonb_build_object(
            'claim',       to_jsonb(cl),
            'certificate', jsonb_build_object(
                               'seq',         lc.seq,
                               'status',      lc.status,
                               'evidence',    lc.evidence,
                               'valid_under', lc.valid_under,
                               'checked_at',  lc.checked_at,
                               'signer_id',   lc.signer_id
                           ),
            'witness',     CASE WHEN lw.witness_kind IS NOT NULL
                           THEN jsonb_build_object('kind', lw.witness_kind, 'body', lw.witness_body)
                           ELSE NULL END,
            'derivation',  CASE WHEN d.conclusion_id IS NOT NULL
                           THEN jsonb_build_object(
                                    'premise_ids', to_jsonb(d.premise_ids),
                                    'rule',        d.rule,
                                    'asserted_at', d.asserted_at
                                )
                           ELSE NULL END,
            'artifact',    CASE WHEN a.claim_id IS NOT NULL
                           THEN jsonb_build_object(
                                    'kind',         a.artifact_kind,
                                    'path',         a.path,
                                    'sha256',       a.sha256,
                                    'checker_cmd',  a.checker_cmd,
                                    'registered_at', a.registered_at
                                )
                           ELSE NULL END,
            'revocation',  CASE WHEN r.certificate_id IS NOT NULL
                           THEN jsonb_build_object(
                                    'reason',     r.reason,
                                    'revoked_by', r.revoked_by,
                                    'revoked_at', r.revoked_at
                                )
                           ELSE NULL END
        )
    )
)
FROM cert.claim cl
JOIN latest_cert  lc ON lc.claim_id = cl.id
LEFT JOIN latest_witness lw ON lw.certificate_id = lc.cert_id
LEFT JOIN derivations     d  ON d.conclusion_id   = cl.id
LEFT JOIN artifacts       a  ON a.claim_id        = cl.id
LEFT JOIN revocations     r  ON r.certificate_id  = lc.cert_id
WHERE cl.id = ANY(p_claim_ids);
$$ LANGUAGE sql STABLE;

COMMENT ON TABLE cert.revocation IS
    'Append-only revocation events, one per certificate. Revoking never mutates '
    'the certificate row; re-attesting appends a fresh (unrevoked) seq. '
    'A revoked certificate reads as effective_status=''revoked'' in cert.standing '
    'and its witnesses stop counting in cert.verify — three-valued honesty: '
    'revocation is loss of trust, not refutation of the claim.';
