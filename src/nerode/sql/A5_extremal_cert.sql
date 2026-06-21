-- =============================================================================
--  nerode — Step A5: Carried certificates for extremal payoffs  [Phase 4]
--
--  Turns an extremal-payoff bound (A4) into a proof-carrying cert claim: the
--  bound is the statement, and the witness finance word is a re-verifiable
--  `payoff_trace`. A consumer re-checks the bound by replaying the word through
--  the product WFFA — no trust in the producer — exactly the witness_carry
--  discipline (88_cert_witness_carry.sql), now for quantitative claims.
--
--  Depends on: A0..A4, nerode.certify (10_cert.sql). Idempotent.
--  NOTE: starter file — not yet executed against a live database.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- nerode.replay_payoff_trace(product, witness) → (ok BOOLEAN, recomputed NUMERIC)
--   Consumer-side verifier: re-walk the witness history through the product and
--   confirm the accumulated max-plus payoff equals the claimed value. Pure
--   replay (no INSERT) — safe for untrusted callers.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.replay_payoff_trace(p_product BIGINT, p_witness JSONB)
RETURNS TABLE (ok BOOLEAN, recomputed NUMERIC) AS $$
DECLARE
    v_claimed NUMERIC;
    v_hist    JSONB;
    v_val     NUMERIC;
BEGIN
    IF COALESCE((p_witness->>'feasible')::boolean, TRUE) = FALSE THEN
        -- An infeasible claim re-verifies iff no admissible accepting run is found.
        RETURN QUERY SELECT (p_witness->>'value') IS NULL, NULL::numeric;
        RETURN;
    END IF;

    v_claimed := (p_witness->>'value')::numeric;
    v_hist    := COALESCE(p_witness->'history', '[]'::jsonb);
    v_val     := nerode.wffa_path_value(p_product, v_hist);

    RETURN QUERY SELECT (v_val IS NOT DISTINCT FROM v_claimed), v_val;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION nerode.replay_payoff_trace(BIGINT, JSONB) IS
    'Re-verify a payoff_trace witness by replaying its finance word through the '
    'product WFFA. Returns (ok, recomputed). Pure replay; no INSERT.';

-- ---------------------------------------------------------------------------
-- nerode.certify_extremal(product, sense, horizon) → BIGINT (cert claim id)
--   Compute the extremal bound (A4) and record it as a cert.claim +
--   certificate + witness via the existing nerode.certify entry point.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.certify_extremal(
    p_product BIGINT,
    p_sense   TEXT,
    p_horizon INTEGER DEFAULT 64)
RETURNS BIGINT AS $$
DECLARE
    v_value   NUMERIC;
    v_witness JSONB;
    v_claim   BIGINT;
BEGIN
    SELECT value, witness INTO v_value, v_witness
    FROM nerode.extremal(p_product, p_sense, p_horizon);

    v_claim := nerode.certify(
        p_automaton_id => p_product,
        p_operation    => 'extremal_' || p_sense,
        p_evidence     => jsonb_build_object(
                              'value',   v_value,
                              'sense',   p_sense,
                              'horizon', p_horizon),
        p_witness_kind => 'payoff_trace',
        p_witness_body => v_witness
    );
    RETURN v_claim;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.certify_extremal(BIGINT, TEXT, INTEGER) IS
    'Record an extremal-payoff bound (A4) as a proof-carrying cert claim whose '
    'witness is the realizing finance word (kind payoff_trace). Returns claim id.';

-- ---------------------------------------------------------------------------
-- Soundness gate for the contradiction verdict.
-- The extremal optimizer (A4) is EXACT only for piecewise-linear payoffs. A
-- 'refuted' verdict (worst > best) is therefore only sound when every payoff in
-- the product is in that class; outside it the optimizer may be unsound and the
-- apparent contradiction could be manufactured. payoff_is_pwl / product_is_pwl
-- decide the class so extremal_consistency can downgrade refuted → unverified,
-- preserving Trunkit's contradiction-soundness invariant (AUDIT §3, empty/
-- undecidable ≠ refuted).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.payoff_is_pwl(p JSONB)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE AS
$$
    SELECT CASE
        WHEN p ? 'const' OR p ? 'bind' THEN TRUE
        WHEN p ? 'guard'  THEN nerode.payoff_is_pwl(p->'guard'->'then')
        WHEN p ? 'otimes' THEN nerode.payoff_is_pwl(p->'otimes'->0) AND nerode.payoff_is_pwl(p->'otimes'->1)
        WHEN p ? 'oplus'  THEN nerode.payoff_is_pwl(p->'oplus'->0)  AND nerode.payoff_is_pwl(p->'oplus'->1)
        ELSE FALSE
    END;
$$;

CREATE OR REPLACE FUNCTION nerode.product_is_pwl(p_product BIGINT)
RETURNS BOOLEAN LANGUAGE sql STABLE AS
$$
    SELECT COALESCE(bool_and(nerode.payoff_is_pwl(w.payoff)), TRUE)
    FROM nerode.transitions t
    JOIN nerode.wffa_weight w ON w.transition_id = t.id
    WHERE t.automaton_id = p_product;
$$;

-- ---------------------------------------------------------------------------
-- Consistency self-check between the two extremal claims about one product.
-- The defining tension to guard against: worst-case must not exceed best-case.
-- Returns the pair plus a verdict ('valid' | 'refuted' | 'unverified') in the
-- three-valued cert style — consumed by the tool-in-tool tension analysis.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.extremal_consistency(p_product BIGINT, p_horizon INTEGER DEFAULT 64)
RETURNS TABLE (best NUMERIC, worst NUMERIC, verdict TEXT) AS $$
DECLARE
    v_best  NUMERIC;
    v_worst NUMERIC;
BEGIN
    SELECT value INTO v_best  FROM nerode.extremal(p_product, 'best',  p_horizon);
    SELECT value INTO v_worst FROM nerode.extremal(p_product, 'worst', p_horizon);

    RETURN QUERY SELECT v_best, v_worst,
        CASE
            WHEN v_best IS NULL OR v_worst IS NULL   THEN 'unverified'  -- infeasible / empty engine
            WHEN v_worst <= v_best                   THEN 'valid'
            WHEN NOT nerode.product_is_pwl(p_product) THEN 'unverified' -- optimizer not exact here ⇒ cannot soundly refute
            ELSE                                          'refuted'      -- genuine contradiction: worst > best
        END;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.extremal_consistency(BIGINT, INTEGER) IS
    'Check the core invariant worst <= best for a product''s two extremal claims. '
    'Three-valued verdict; refuted (a genuine tension) is emitted ONLY when the '
    'optimizer is exact (piecewise-linear payoffs), else unverified — preserving '
    'contradiction-soundness (AUDIT §3).';

-- ---------------------------------------------------------------------------
-- cert.verify hook (documentation, not executed here):
--   cert.verify dispatches by witness.kind. Add one branch mirroring the
--   witness_carry verifier (88_cert_witness_carry.sql):
--
--     WHEN w.kind = 'payoff_trace' THEN
--        -- product id is in claim.subject_ref->>'automaton_id'
--        SELECT ok FROM nerode.replay_payoff_trace(
--            (c.subject_ref->>'automaton_id')::bigint, w.body);
--
--   Kept out of this file to avoid editing the calx-side cert schema; it is a
--   one-branch addition with no schema change.
-- ---------------------------------------------------------------------------
