-- =============================================================================

--  nerode — Step A5: Carried certificates for extremal payoffs  [Phase 4]
--
--  A payoff_trace proves attainment only. It does not prove that no better
--  word exists, nor that the accepting language is empty. Extremal claims
--  remain unverified without a separately checked bound/DP certificate.
--
--  Depends on: A0..A4, nerode.certify (10_cert.sql). Idempotent.
-- =============================================================================

INSERT INTO cert.method(name,claim_kind,checker_kind,description) VALUES
 ('nerode_extremal_best','structural','sql','Candidate best payoff; path alone does not prove optimality'),
 ('nerode_extremal_worst','structural','sql','Candidate worst payoff; path alone does not prove optimality'),
 ('nerode_payoff_attainment','structural','sql','Replay an explicit deterministic payoff witness')
ON CONFLICT(name) DO NOTHING;

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
    IF p_witness->>'feasible' IS DISTINCT FROM 'true'
       OR p_witness->>'value' IS NULL
       OR jsonb_typeof(p_witness->'history') IS DISTINCT FROM 'array'
       OR p_witness->>'supported'='false' THEN
        RETURN QUERY SELECT NULL::boolean, NULL::numeric;
        RETURN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM nerode.automata WHERE id=p_product AND type='WFFA')
       OR (SELECT count(*) FROM nerode.states WHERE automaton_id=p_product)>256
       OR (SELECT count(*) FROM nerode.transitions WHERE automaton_id=p_product)>16384 THEN
        RETURN QUERY SELECT NULL::BOOLEAN,NULL::NUMERIC;
        RETURN;
    END IF;

    v_claimed := (p_witness->>'value')::numeric;
    v_hist    := COALESCE(p_witness->'history', '[]'::jsonb);
    IF (p_witness->>'horizon')::INTEGER IS NULL
       OR (p_witness->>'horizon')::INTEGER NOT BETWEEN 0 AND 4096
       OR jsonb_array_length(v_hist)>(p_witness->>'horizon')::INTEGER THEN
        RETURN QUERY SELECT FALSE, NULL::NUMERIC;
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(v_hist) x
                WHERE jsonb_typeof(x)<>'array' OR jsonb_array_length(x)<>2) THEN
        RETURN QUERY SELECT NULL::BOOLEAN,NULL::NUMERIC;
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(v_hist) x
                WHERE jsonb_typeof(x->0)<>'string' OR jsonb_typeof(x->1)<>'number') THEN
        RETURN QUERY SELECT NULL::BOOLEAN,NULL::NUMERIC;
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM jsonb_array_elements(v_hist) x WHERE (x->>1)::NUMERIC<0) THEN
        RETURN QUERY SELECT FALSE,NULL::NUMERIC;
        RETURN;
    END IF;
    -- The replay primitive chooses the first enabled edge; require a unique
    -- initial state and no competing transitions so this is a deterministic
    -- word witness, not an arbitrary accepting run of a weighted NFA.
    IF (SELECT count(*) FROM nerode.wffa_terminal
         WHERE automaton_id=p_product AND role='initial')<>1
       OR EXISTS (SELECT 1 FROM nerode.wffa_terminal
                   WHERE automaton_id=p_product AND weight IS NULL)
       OR EXISTS (SELECT 1 FROM nerode.transitions WHERE automaton_id=p_product
                   GROUP BY from_state,symbol HAVING count(*)>1)
       OR EXISTS (SELECT 1 FROM nerode.transitions t
                   JOIN nerode.automata m ON m.id=t.automaton_id
                   JOIN nerode.alphabets a ON a.id=m.alphabet_id
                   WHERE m.id=p_product AND (t.symbol IS NULL OR NOT t.symbol=ANY(a.symbols))) THEN
        RETURN QUERY SELECT NULL::BOOLEAN,NULL::NUMERIC;
        RETURN;
    END IF;
    v_val     := nerode.wffa_path_value(p_product, v_hist);
    IF v_val IS DISTINCT FROM v_claimed THEN
        RETURN QUERY SELECT FALSE,v_val;
    ELSIF p_witness->>'claim_scope'='attainment' THEN
        RETURN QUERY SELECT TRUE,v_val;
    ELSE
        -- Equality for one word establishes a feasible objective value only.
        RETURN QUERY SELECT NULL::BOOLEAN,v_val;
    END IF;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION nerode.replay_payoff_trace(BIGINT, JSONB) IS
    'Replay a deterministic payoff trace. ok=true only for explicit attainment '
    'scope; extremality and infeasibility are unverified without independent '
    'bound certificates. A false feasible value returns false.';

-- ---------------------------------------------------------------------------
-- nerode.certify_extremal(product, sense, horizon) → BIGINT (cert claim id)
--   Compute the extremal bound (A4) and record it as a cert.claim +
--   certificate + witness via the existing nerode.certify entry point.
-- ---------------------------------------------------------------------------
-- Record the right status at creation; certificate rows remain append-only.
CREATE OR REPLACE FUNCTION nerode.record_payoff_claim(
    p_product BIGINT,p_operation TEXT,p_witness JSONB,p_status TEXT)
RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE v_claim BIGINT; v_certificate BIGINT; v_seq INTEGER;
BEGIN
    IF p_operation NOT IN ('extremal_best','extremal_worst','payoff_attainment')
       OR p_status NOT IN ('valid','refuted','unverified') THEN
        RAISE EXCEPTION 'record_payoff_claim: unsupported operation or status';
    END IF;
    INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method)
    VALUES ('nerode_automaton',jsonb_build_object('automaton_id',p_product,'operation',p_operation,
            'value',p_witness->'value','horizon',p_witness->'horizon','sense',p_witness->'sense'),
        format('nerode.%s on automaton %s certified at %s',p_operation,p_product,clock_timestamp()),
        'structural','nerode_'||p_operation)
    RETURNING id INTO v_claim;
    SELECT COALESCE(max(seq),0)+1 INTO v_seq FROM cert.certificate WHERE claim_id=v_claim;
    INSERT INTO cert.certificate(claim_id,seq,status,evidence,valid_under)
    VALUES(v_claim,v_seq,p_status,
        jsonb_build_object('value',p_witness->'value','sense',p_witness->'sense',
                           'horizon',p_witness->'horizon'),
        jsonb_build_object('nerode_schema_version',1))
    RETURNING id INTO v_certificate;
    INSERT INTO cert.witness(certificate_id,kind,body,schema_version)
    VALUES(v_certificate,'payoff_trace',p_witness,jsonb_build_object('nerode_schema_version',1));
    RETURN v_claim;
END $$;

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

    -- The producer computed a candidate extremum, but the portable proof is
    -- only a trace. Do not advertise independently verified optimality.
    v_claim := nerode.record_payoff_claim(p_product,'extremal_'||p_sense,v_witness,'unverified');
    RETURN v_claim;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION nerode.certify_payoff_attainment(
    p_product BIGINT,p_word JSONB,p_horizon INTEGER DEFAULT 64)
RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE v_value NUMERIC; v_claim BIGINT; v_ok BOOLEAN;
        v_witness JSONB;
BEGIN
    IF p_horizon IS NULL OR p_horizon NOT BETWEEN 0 AND 4096
       OR jsonb_typeof(p_word) IS DISTINCT FROM 'array'
       OR jsonb_array_length(p_word)>p_horizon THEN
        RAISE EXCEPTION 'certify_payoff_attainment: invalid word or horizon';
    END IF;
    v_value := nerode.wffa_path_value(p_product,p_word);
    v_witness := jsonb_build_object('kind','payoff_trace','claim_scope','attainment',
        'product_id',p_product,'value',v_value,'horizon',p_horizon,
        'feasible',v_value IS NOT NULL,'history',p_word);
    SELECT r.ok INTO v_ok FROM nerode.replay_payoff_trace(p_product,v_witness) r;
    v_claim := nerode.record_payoff_claim(p_product,'payoff_attainment',v_witness,
        CASE WHEN v_ok IS TRUE THEN 'valid' WHEN v_ok IS FALSE THEN 'refuted' ELSE 'unverified' END);
    RETURN v_claim;
END $$;

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
