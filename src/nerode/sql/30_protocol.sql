-- =============================================================================
--  nerode — Step 09: Protocol equivalence + arithmetic irreducibility
--
--  This module provides the top-level "protocol equivalence check" that
--  combines structural DFA equivalence (via nerode.certify_equivalence)
--  with arithmetic irreducibility (via calx.arithmetic_facts when available).
--
--  Functions:
--    nerode.certify_prime_dfa(p_automaton_id)
--        Certify whether a DFA's state count is prime.
--        Cross-tool: uses calx.arithmetic_facts() when the calx schema is
--        installed (Trunkit co-deployment); falls back to self-contained
--        primality via nerode.calx_state_facts().
--        Witness kind: nerode_partition
--        Cert method:  nerode_arithmetic_prime
--
--    nerode.protocol_equivalence_check(p_id1, p_id2)
--        One-shot cross-tool protocol:
--          1. Certify equivalence of two DFAs (nerode.certify_equivalence).
--          2. Gather calx arithmetic facts for both state counts.
--          3. If equivalent, certify prime state counts for automata whose
--             |Q| is prime (cross-schema claims bridging Nerode and calx).
--        Returns a comprehensive JSONB summary.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Register cross-tool cert.method
-- ---------------------------------------------------------------------------

INSERT INTO cert.method (name, claim_kind, checker_kind, description) VALUES
    ('nerode_arithmetic_prime',
     'structural', 'sql',
     'Cross-tool claim: DFA state count primality certified jointly by '
     'nerode.calx_state_facts() and calx.arithmetic_facts(). '
     'Witness kind = nerode_partition (Nerode classes are the states).')
ON CONFLICT (name) DO NOTHING;


-- ---------------------------------------------------------------------------
-- nerode.certify_prime_dfa(p_automaton_id)
--
-- Issue a cert.claim recording whether an automaton's state count is prime.
-- Evidence comes from nerode.calx_state_facts(), which delegates to
-- calx.arithmetic_facts() when the calx schema is present.
--
-- Returns TABLE (is_prime BOOLEAN, state_count INTEGER,
--                claim_id BIGINT, calx_facts JSONB).
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.certify_prime_dfa(p_automaton_id BIGINT)
RETURNS TABLE (
    is_prime    BOOLEAN,
    state_count INTEGER,
    claim_id    BIGINT,
    calx_facts  JSONB
)
AS $$
DECLARE
    v_facts     JSONB;
    v_is_prime  BOOLEAN;
    v_n         INTEGER;
    v_name      TEXT;
    v_stmt      TEXT;
    v_cl_id     BIGINT;
    v_cert_id   BIGINT;
    v_seq       INTEGER;
BEGIN
    -- Gather arithmetic facts (delegates to calx when available)
    v_facts    := nerode.calx_state_facts(p_automaton_id);
    v_is_prime := (v_facts->>'is_prime')::BOOLEAN;
    v_n        := (v_facts->>'state_count')::INTEGER;
    v_name     := v_facts->>'name';

    -- Statement encodes automaton identity and primality verdict
    v_stmt := format(
        'automaton %s (%s): |Q| = %s is %s',
        p_automaton_id,
        v_name,
        v_n,
        CASE WHEN v_is_prime THEN 'prime' ELSE 'composite' END
    );

    -- Upsert cert.claim (idempotent on statement text)
    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method)
    VALUES (
        'nerode_automaton',
        jsonb_build_object('automaton_id', p_automaton_id),
        v_stmt,
        'structural',
        'nerode_arithmetic_prime'
    )
    ON CONFLICT (statement) DO UPDATE SET subject_ref = EXCLUDED.subject_ref
    RETURNING id INTO v_cl_id;

    -- cert.certificate
    SELECT COALESCE(max(crt.seq), 0) + 1 INTO v_seq
    FROM cert.certificate AS crt WHERE crt.claim_id = v_cl_id;

    INSERT INTO cert.certificate (claim_id, seq, status, evidence, valid_under)
    VALUES (
        v_cl_id, v_seq, 'valid',
        v_facts,
        jsonb_build_object(
            'nerode_schema_version', 1,
            'calx_available', (v_facts->>'calx_available')::BOOLEAN
        )
    )
    RETURNING id INTO v_cert_id;

    -- cert.witness — kind = nerode_partition
    -- The Nerode equivalence classes ARE the states of the minimal DFA;
    -- certifying the count is prime connects Nerode's structural result
    -- with calx's arithmetic facts.
    INSERT INTO cert.witness (certificate_id, kind, body, schema_version)
    VALUES (
        v_cert_id,
        'nerode_partition',
        v_facts || jsonb_build_object(
            'claim',       'state_count_primality',
            'is_prime',    v_is_prime,
            'state_count', v_n
        ),
        jsonb_build_object('nerode_schema_version', 1)
    );

    RETURN QUERY SELECT v_is_prime, v_n, v_cl_id, v_facts;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.certify_prime_dfa(BIGINT) IS
    'Issue a cert.claim recording whether an automaton''s state count is prime. '
    'Cross-tool: uses calx.arithmetic_facts() when available via nerode.calx_state_facts(). '
    'Witness kind = nerode_partition (Nerode classes = states; count primality is the claim). '
    'Returns (is_prime, state_count, claim_id, calx_facts).';


-- ---------------------------------------------------------------------------
-- nerode.protocol_equivalence_check(p_id1, p_id2)
--
-- Full cross-tool protocol for language equivalence with arithmetic annotation.
--
-- Steps:
--   1. nerode.certify_equivalence(p_id1, p_id2)
--        → issues cert.claim + cert.certificate + cert.witness
--        → witness.kind = 'bisimulation' if equivalent, 'counterexample' if not
--   2. nerode.calx_state_facts() for each automaton
--        → arithmetic enrichment (state count primality, factorization, calx data)
--   3. If equivalent AND state count is prime: nerode.certify_prime_dfa()
--        → cross-schema joint claim bridging Nerode and calx
--
-- Returns JSONB summary:
--   {
--     "equivalent":         bool,
--     "witness_kind":       "bisimulation" | "counterexample",
--     "equiv_claim_id":     bigint,
--     "distinguishing_string": string (only when not equivalent),
--     "automaton1": {
--       "id", "name", "state_count", "is_prime",
--       "calx_available", "arithmetic_claim_id"
--     },
--     "automaton2": { ... same ... },
--     "witness":            jsonb (raw BFS witness)
--   }
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.protocol_equivalence_check(p_id1 BIGINT, p_id2 BIGINT)
RETURNS JSONB
AS $$
DECLARE
    v_eq          BOOLEAN;
    v_eq_cl_id    BIGINT;
    v_facts1      JSONB;
    v_facts2      JSONB;
    v_prime1      BOOLEAN;
    v_prime2      BOOLEAN;
    v_sc1         INTEGER;
    v_sc2         INTEGER;
    v_cross1_id   BIGINT := NULL;
    v_cross2_id   BIGINT := NULL;
    v_witness     JSONB;
    v_result      JSONB;
BEGIN
    -- Step 1: Certify equivalence (issues claim + certificate + witness)
    SELECT ce.equivalent, ce.claim_id
    INTO v_eq, v_eq_cl_id
    FROM nerode.certify_equivalence(p_id1, p_id2) AS ce;

    -- Step 2: Retrieve the witness body from the cert tables
    --   (avoids re-running the BFS; certify_equivalence already stored it)
    SELECT w.body INTO v_witness
    FROM cert.certificate AS crt
    JOIN cert.witness w ON w.certificate_id = crt.id
    WHERE crt.claim_id = v_eq_cl_id
    ORDER BY crt.seq DESC
    LIMIT 1;

    -- Step 3: Arithmetic facts for each automaton
    v_facts1 := nerode.calx_state_facts(p_id1);
    v_facts2 := nerode.calx_state_facts(p_id2);

    v_prime1 := (v_facts1->>'is_prime')::BOOLEAN;
    v_prime2 := (v_facts2->>'is_prime')::BOOLEAN;
    v_sc1    := (v_facts1->>'state_count')::INTEGER;
    v_sc2    := (v_facts2->>'state_count')::INTEGER;

    -- Step 4: Cross-tool arithmetic claims for equivalent DFAs with prime state counts
    -- Only issued when the DFAs recognise the same language (equivalent=true).
    IF v_eq THEN
        IF v_prime1 THEN
            SELECT cp.claim_id INTO v_cross1_id
            FROM nerode.certify_prime_dfa(p_id1) AS cp;
        END IF;
        IF v_prime2 THEN
            SELECT cp.claim_id INTO v_cross2_id
            FROM nerode.certify_prime_dfa(p_id2) AS cp;
        END IF;
    END IF;

    -- Build comprehensive JSONB result
    v_result := jsonb_build_object(
        'equivalent',       v_eq,
        'witness_kind',     CASE WHEN v_eq THEN 'bisimulation' ELSE 'counterexample' END,
        'equiv_claim_id',   v_eq_cl_id,
        'automaton1', jsonb_build_object(
            'id',                  p_id1,
            'name',                v_facts1->>'name',
            'state_count',         v_sc1,
            'is_prime',            v_prime1,
            'calx_available',      (v_facts1->>'calx_available')::BOOLEAN,
            'arithmetic_claim_id', v_cross1_id
        ),
        'automaton2', jsonb_build_object(
            'id',                  p_id2,
            'name',                v_facts2->>'name',
            'state_count',         v_sc2,
            'is_prime',            v_prime2,
            'calx_available',      (v_facts2->>'calx_available')::BOOLEAN,
            'arithmetic_claim_id', v_cross2_id
        ),
        'witness', v_witness
    );

    -- Hoist distinguishing string for non-equivalent pairs (convenience field)
    IF NOT v_eq AND v_witness IS NOT NULL THEN
        v_result := v_result || jsonb_build_object(
            'distinguishing_string', v_witness->>'distinguishing_string'
        );
    END IF;

    RETURN v_result;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.protocol_equivalence_check(BIGINT, BIGINT) IS
    'Full cross-tool protocol: certify DFA equivalence, gather calx arithmetic facts, '
    'and (if equivalent) certify prime state counts. '
    'Returns a JSONB summary: equivalent/witness_kind/equiv_claim_id, '
    'per-automaton state count + primality + arithmetic_claim_id, '
    'and the raw BFS witness. Non-equivalent pairs include distinguishing_string at top level.';
