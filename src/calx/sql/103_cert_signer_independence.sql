-- Unified model, step 103: signer independence over derivation premises.
--
-- The institutional-attestation pattern (arXiv:2606.26298, 2026-06): a
-- consequential conclusion should rest on preconditions that are EACH
-- attested by a separate authoritative source, and never solely on the
-- say-so of the party asserting the conclusion. Step 100 gave certificates a
-- signer identity; step 102 gave derivations transitive soundness. This step
-- adds the multi-party check on top of both:
--
--   * cert.derivation_independent(derivation_id, min_distinct) — (ok,
--     evidence): every premise's latest certificate stands effectively
--     valid AND carries a signer, the valid premises are attested by at
--     least min_distinct distinct signers (default: one per distinct
--     premise, i.e. pairwise-independent), and none of them is the signer
--     of the conclusion's own certificate (no self-attestation). The check
--     reads cert.standing, so revocation and expiry degrade independence
--     exactly as they degrade validity.
--   * cert.independence_claim(derivation_id, min_distinct) — wraps the
--     check in a re-checkable comp_sql claim, so the independence property
--     itself is certified, re-verified by cert.check / cert.verify, and
--     travels in export bundles (pattern of 101).
--
-- Signer identity remains an identity CLAIM recorded in provenance, not a
-- cryptographic proof (Ed25519 per-record signatures stay design B1 in
-- SECURITY.md); this check makes the recorded identities load-bearing so
-- B1 has something to harden. Idempotent; additive only.

-- ---------------------------------------------------------------------------
-- 1. the independence check
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION cert.derivation_independent(
    p_derivation_id BIGINT, p_min_distinct INT DEFAULT NULL)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_deriv       cert.derivation%ROWTYPE;
    v_conc_signer TEXT;
    v_required    INT;
    v_rows        JSONB;
    v_distinct    INT;
    v_invalid     INT;
    v_unsigned    INT;
    v_self        BOOLEAN;
BEGIN
    SELECT * INTO v_deriv FROM cert.derivation WHERE id = p_derivation_id;
    IF NOT FOUND THEN
        ok := FALSE;
        evidence := jsonb_build_object('error', 'derivation not found',
                                       'id', p_derivation_id);
        RETURN NEXT; RETURN;
    END IF;

    -- Signer of the conclusion's own latest certificate, if it has one.
    -- (In the gate posture — checking BEFORE acting — the conclusion is
    -- typically uncertified and the self-attestation clause is vacuous.)
    SELECT s.signer_id INTO v_conc_signer
      FROM cert.standing s WHERE s.claim_id = v_deriv.conclusion_id;

    WITH premise AS (
        SELECT DISTINCT p.premise_id
          FROM unnest(v_deriv.premise_ids) AS p(premise_id)
    ),
    graded AS (
        SELECT pr.premise_id, s.signer_id,
               COALESCE(s.effective_status, 'missing') AS effective_status
          FROM premise pr
          LEFT JOIN cert.standing s ON s.claim_id = pr.premise_id
    )
    SELECT jsonb_agg(jsonb_build_object(
               'claim_id', premise_id,
               'signer',   signer_id,
               'status',   effective_status) ORDER BY premise_id),
           count(DISTINCT signer_id) FILTER (WHERE effective_status = 'valid'),
           count(*) FILTER (WHERE effective_status IS DISTINCT FROM 'valid'),
           count(*) FILTER (WHERE signer_id IS NULL),
           bool_or(v_conc_signer IS NOT NULL
                   AND signer_id IS NOT DISTINCT FROM v_conc_signer)
      INTO v_rows, v_distinct, v_invalid, v_unsigned, v_self
      FROM graded;

    v_required := COALESCE(
        p_min_distinct,
        (SELECT count(DISTINCT x) FROM unnest(v_deriv.premise_ids) AS x));

    ok := (v_invalid = 0)
          AND (v_unsigned = 0)
          AND (v_distinct >= v_required)
          AND NOT COALESCE(v_self, FALSE);
    evidence := jsonb_build_object(
        'premises',          COALESCE(v_rows, '[]'::jsonb),
        'distinct_signers',  v_distinct,
        'required',          v_required,
        'invalid_premises',  v_invalid,
        'unsigned_premises', v_unsigned,
        'conclusion_signer', v_conc_signer,
        'self_attested',     COALESCE(v_self, FALSE));
    RETURN NEXT;
END $$;

COMMENT ON FUNCTION cert.derivation_independent(BIGINT, INT) IS
    'Multi-party attestation check (arXiv:2606.26298 pattern): the premises '
    'of a derivation must each stand effectively valid under certificates '
    'from at least min_distinct distinct signers (default: pairwise '
    'distinct), none of whom signed the conclusion''s own certificate. '
    'Unsigned (pre-step-100) or non-valid premises fail the check.';

-- ---------------------------------------------------------------------------
-- 2. the re-checkable claim wrapper (pattern of 101)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION cert.independence_claim(
    p_derivation_id BIGINT, p_min_distinct INT DEFAULT NULL)
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE
    v_deriv cert.derivation%ROWTYPE;
    v_stmt  TEXT; v_probe TEXT; v_id BIGINT;
BEGIN
    SELECT * INTO v_deriv FROM cert.derivation WHERE id = p_derivation_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no derivation %', p_derivation_id;
    END IF;
    v_stmt := format(
        'premises of derivation #%s (rule %s) are independently attested by %s distinct signers',
        v_deriv.id, v_deriv.rule,
        COALESCE(p_min_distinct::text, 'pairwise'));
    v_probe := format(
        'SELECT ok, evidence FROM cert.derivation_independent(%s, %s)',
        v_deriv.id, COALESCE(p_min_distinct::text, 'NULL::int'));
    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
    VALUES ('derivation',
            jsonb_build_object('derivation_id', v_deriv.id,
                               'conclusion_id', v_deriv.conclusion_id),
            v_stmt, 'computational', 'comp_sql', v_probe)
    ON CONFLICT (statement) DO UPDATE SET probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_id;
    RETURN v_id;
END $$;

COMMENT ON FUNCTION cert.independence_claim(BIGINT, INT) IS
    'Wraps cert.derivation_independent in a re-checkable comp_sql claim, so '
    'signer independence is itself certified and travels in export bundles.';
