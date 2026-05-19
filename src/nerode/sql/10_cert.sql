-- =============================================================================
--  nerode — Step 06: Certification API
--
--  nerode.certify()          — issue a claim + certificate + witness
--  nerode.cert_snapshot()    — certification status of all automata
--  nerode.certify_run()      — certify a membership query result
--  nerode.certify_equivalence() — certify an equivalence check result
-- =============================================================================

-- ---------------------------------------------------------------------------
-- nerode.certify()
-- Generic certification entry point.
-- Registers a cert.claim + cert.certificate + cert.witness for any operation.
-- Returns the claim id.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.certify(
    p_automaton_id  BIGINT,
    p_operation     TEXT,           -- from_regex | minimize | product | complement | run | equivalent
    p_evidence      JSONB,          -- top-level evidence for the certificate
    p_witness_kind  TEXT,           -- nerode_partition | bisimulation | computation_trace | construction_record
    p_witness_body  JSONB           -- witness payload
)
RETURNS BIGINT AS $$
DECLARE
    v_cl_id   BIGINT;
    v_cert_id BIGINT;
    v_seq     INTEGER;
    v_stmt    TEXT;
BEGIN
    v_stmt := format(
        'nerode.%s on automaton %s certified at %s',
        p_operation, p_automaton_id, now()
    );

    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method)
    VALUES (
        'nerode_automaton',
        jsonb_build_object('automaton_id', p_automaton_id, 'operation', p_operation),
        v_stmt,
        'structural',
        'nerode_' || p_operation
    )
    ON CONFLICT (statement) DO UPDATE SET subject_ref = EXCLUDED.subject_ref
    RETURNING id INTO v_cl_id;

    SELECT COALESCE(max(seq), 0) + 1 INTO v_seq
    FROM cert.certificate WHERE claim_id = v_cl_id;

    INSERT INTO cert.certificate (claim_id, seq, status, evidence, valid_under)
    VALUES (
        v_cl_id, v_seq, 'valid',
        p_evidence,
        jsonb_build_object('nerode_schema_version', 1)
    )
    RETURNING id INTO v_cert_id;

    INSERT INTO cert.witness (certificate_id, kind, body, schema_version)
    VALUES (
        v_cert_id,
        p_witness_kind,
        p_witness_body,
        jsonb_build_object('nerode_schema_version', 1)
    );

    -- Mark automaton as certified if this is a construction-level operation
    IF p_operation IN ('from_regex', 'minimize', 'product', 'complement') THEN
        UPDATE nerode.automata
        SET certified = TRUE, cert_claim_id = v_cl_id
        WHERE id = p_automaton_id;
    END IF;

    RETURN v_cl_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.certify(BIGINT, TEXT, JSONB, TEXT, JSONB) IS
    'Issue a cert.claim + cert.certificate + cert.witness for a nerode operation. '
    'Returns the claim id. Marks the automaton certified if operation is structural.';

-- ---------------------------------------------------------------------------
-- nerode.certify_run()
-- Certify a membership query.
-- Calls nerode.run() and immediately issues a computation_trace certificate.
-- Returns (accept, claim_id).
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.certify_run(
    p_automaton_id BIGINT,
    p_input        TEXT
)
RETURNS TABLE (accept BOOLEAN, claim_id BIGINT)
AS $$
DECLARE
    v_acc      BOOLEAN;
    v_evidence JSONB;
    v_witness  JSONB;
    v_cl_id    BIGINT;
BEGIN
    SELECT r.accept, r.evidence, r.cert_witness
    INTO v_acc, v_evidence, v_witness
    FROM nerode.run(p_automaton_id, p_input) AS r;

    v_cl_id := nerode.certify(
        p_automaton_id,
        'run',
        v_evidence,
        'computation_trace',
        v_witness
    );

    RETURN QUERY SELECT v_acc, v_cl_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.certify_run(BIGINT, TEXT) IS
    'Run DFA simulation and issue a cert.witness of kind computation_trace. '
    'Returns (accept BOOLEAN, claim_id BIGINT).';

-- ---------------------------------------------------------------------------
-- nerode.certify_equivalence()
-- Test and certify equivalence of two DFAs.
-- Returns (equivalent BOOLEAN, claim_id BIGINT).
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.certify_equivalence(
    p_id1 BIGINT,
    p_id2 BIGINT
)
RETURNS TABLE (equivalent BOOLEAN, claim_id BIGINT)
AS $$
DECLARE
    v_eq      BOOLEAN;
    v_witness JSONB;
    v_cl_id   BIGINT;
    v_wkind   TEXT;
    v_stmt    TEXT;
    v_cert_id BIGINT;
    v_seq     INTEGER;
BEGIN
    SELECT e.equivalent, e.witness
    INTO v_eq, v_witness
    FROM nerode.equivalent(p_id1, p_id2) AS e;

    v_wkind := CASE WHEN v_eq THEN 'bisimulation' ELSE 'counterexample' END;

    v_stmt := format(
        'nerode.equivalent(%s, %s) = %s at %s',
        p_id1, p_id2, v_eq, now()
    );

    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method)
    VALUES (
        'nerode_automaton_pair',
        jsonb_build_object('automaton_id1', p_id1, 'automaton_id2', p_id2),
        v_stmt,
        'relational',
        'nerode_equivalence'
    )
    ON CONFLICT (statement) DO UPDATE SET subject_ref = EXCLUDED.subject_ref
    RETURNING id INTO v_cl_id;

    SELECT COALESCE(max(seq), 0) + 1 INTO v_seq
    FROM cert.certificate AS ce WHERE ce.claim_id = v_cl_id;

    INSERT INTO cert.certificate (claim_id, seq, status, evidence, valid_under)
    VALUES (
        v_cl_id, v_seq, 'valid',
        jsonb_build_object(
            'automaton_id1', p_id1,
            'automaton_id2', p_id2,
            'equivalent',    v_eq
        ),
        jsonb_build_object('nerode_schema_version', 1)
    )
    RETURNING id INTO v_cert_id;

    INSERT INTO cert.witness (certificate_id, kind, body, schema_version)
    VALUES (
        v_cert_id,
        v_wkind,
        v_witness,
        jsonb_build_object('nerode_schema_version', 1)
    );

    RETURN QUERY SELECT v_eq, v_cl_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.certify_equivalence(BIGINT, BIGINT) IS
    'Test language equivalence and issue a cert.witness '
    '(kind=bisimulation if equivalent, counterexample if not). '
    'Returns (equivalent BOOLEAN, claim_id BIGINT).';

-- ---------------------------------------------------------------------------
-- nerode.cert_snapshot()
-- JSONB summary of all automata and their certification status.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.cert_snapshot()
RETURNS JSONB AS $$
SELECT jsonb_build_object(
    'snapshot_at', now(),
    'automata', (
        SELECT jsonb_agg(jsonb_build_object(
            'id',           au.id,
            'name',         au.name,
            'type',         au.type,
            'state_count',  au.state_count,
            'certified',    au.certified,
            'cert_claim_id',au.cert_claim_id,
            'source_regex', au.source_regex,
            'created_at',   au.created_at
        ) ORDER BY au.id)
        FROM nerode.automata au
    ),
    'total',       (SELECT count(*) FROM nerode.automata),
    'certified',   (SELECT count(*) FROM nerode.automata WHERE certified = TRUE),
    'uncertified', (SELECT count(*) FROM nerode.automata WHERE certified = FALSE)
);
$$ LANGUAGE sql;

COMMENT ON FUNCTION nerode.cert_snapshot() IS
    'Return a JSONB snapshot of all automata with certification status counts.';
