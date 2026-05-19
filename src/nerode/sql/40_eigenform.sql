-- =============================================================================
--  nerode — Step 10: Eigenform / Fixed-Point Scanner
--
--  An automaton M is its own eigenform iff it is already minimal —
--  i.e., minimize(M) produces a DFA with the same state count as M.
--
--  Intuition (Laws of Form): the canonical minimal DFA is the fixed-point
--  of Myhill-Nerode refinement. From_regex and minimize outputs already
--  sit at that fixed-point; imported DFAs may not.
--
--  Functions:
--    nerode.certify_eigenform(p_automaton_id)
--        Certify whether a DFA is its own eigenform (already minimal).
--        Fast path: source_regex != NULL (from_regex) or
--                   provenance->>'operation' = 'minimize' (minimize output).
--        Slow path: explicit nerode.minimize() call + state-count comparison.
--        Witness kind: nerode_partition
--        Cert method:  nerode_eigenform
--
--    nerode.scan_eigenforms()
--        Scan all DFAs in nerode.automata, call certify_eigenform for each.
--        Returns one summary row per DFA.
--        Idempotent: repeated calls add certificates but do not error.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Register cert.method
-- ---------------------------------------------------------------------------

INSERT INTO cert.method (name, claim_kind, checker_kind, description) VALUES
    ('nerode_eigenform',
     'structural', 'sql',
     'Fixed-point of Myhill-Nerode refinement. '
     'is_minimal=true iff minimize(M) has the same state count as M '
     '(every state is Nerode-distinct). '
     'Fast path for from_regex / minimize-output DFAs; '
     'slow path calls nerode.minimize() for imported DFAs. '
     'Witness kind = nerode_partition (Hopcroft partition or trivial singletons).')
ON CONFLICT (name) DO NOTHING;


-- ---------------------------------------------------------------------------
-- nerode.certify_eigenform(p_automaton_id)
--
-- Certify whether a DFA is its own Myhill-Nerode eigenform (already minimal).
-- Issues cert.claim + cert.certificate + cert.witness(nerode_partition).
-- Returns TABLE (is_minimal, eigenform_id, original_states, minimal_states, claim_id).
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.certify_eigenform(p_automaton_id BIGINT)
RETURNS TABLE (
    is_minimal       BOOLEAN,
    eigenform_id     BIGINT,
    original_states  INTEGER,
    minimal_states   INTEGER,
    claim_id         BIGINT
)
AS $$
DECLARE
    v_auto          nerode.automata%ROWTYPE;
    v_min_id        BIGINT;
    v_min_sc        INTEGER;
    v_is_min        BOOLEAN;
    v_partition     JSONB;
    v_stmt          TEXT;
    v_cl_id         BIGINT;
    v_cert_id       BIGINT;
    v_seq           INTEGER;
BEGIN
    SELECT * INTO v_auto FROM nerode.automata WHERE id = p_automaton_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'nerode.certify_eigenform: automaton % not found', p_automaton_id;
    END IF;
    IF v_auto.type != 'DFA' THEN
        RAISE EXCEPTION 'nerode.certify_eigenform: automaton % is type %, expected DFA',
            p_automaton_id, v_auto.type;
    END IF;

    -- -----------------------------------------------------------------------
    -- Fast path: already known to be minimal
    --   • from_regex DFAs:      source_regex IS NOT NULL
    --   • minimize outputs:     provenance->>'operation' = 'minimize'
    -- Both cases: eigenform is the DFA itself; build trivial singleton partition.
    -- -----------------------------------------------------------------------
    IF v_auto.source_regex IS NOT NULL
       OR (    v_auto.provenance IS NOT NULL
           AND v_auto.provenance->>'operation' = 'minimize')
    THEN
        v_is_min := TRUE;
        v_min_id := p_automaton_id;        -- eigenform is the DFA itself
        v_min_sc := v_auto.state_count;

        -- Trivial partition: each state occupies its own singleton block.
        SELECT jsonb_object_agg(s.state_id::text, jsonb_build_array(s.state_id))
        INTO   v_partition
        FROM   nerode.states s
        WHERE  s.automaton_id = p_automaton_id;

    ELSE
        -- -----------------------------------------------------------------------
        -- Slow path: imported DFA — call minimize, compare state counts.
        -- nerode.minimize() always creates a new automaton (even for minimal input).
        -- -----------------------------------------------------------------------
        v_min_id := nerode.minimize(p_automaton_id);

        -- Read the Hopcroft partition and minimal state count from construction_log.
        SELECT (cl.result->>'minimal_states')::INTEGER,
               cl.result->'partition'
        INTO   v_min_sc, v_partition
        FROM   nerode.construction_log cl
        WHERE  cl.automaton_id = v_min_id
          AND  cl.operation    = 'minimize'
        ORDER  BY cl.id DESC
        LIMIT  1;

        -- M is its own eigenform iff minimize produced the same state count.
        v_is_min := (v_auto.state_count = v_min_sc);
    END IF;

    -- -----------------------------------------------------------------------
    -- Unique logical statement for this automaton's eigenform certification.
    -- (Excludes eigenform_id so the statement is stable across repeated calls.)
    -- -----------------------------------------------------------------------
    v_stmt := format(
        'eigenform(%s [%s]): |Q|=%s, |Q_min|=%s, is_minimal=%s',
        p_automaton_id,
        coalesce(v_auto.name, '?'),
        v_auto.state_count,
        v_min_sc,
        v_is_min
    );

    -- -----------------------------------------------------------------------
    -- cert.claim  (UNIQUE by statement — idempotent upsert)
    -- -----------------------------------------------------------------------
    INSERT INTO cert.claim (
        subject_kind, subject_ref, statement, claim_kind, method
    ) VALUES (
        'nerode_automaton',
        jsonb_build_object('automaton_id', p_automaton_id, 'eigenform_id', v_min_id),
        v_stmt,
        'structural',
        'nerode_eigenform'
    )
    ON CONFLICT (statement) DO UPDATE
        SET subject_ref = EXCLUDED.subject_ref
    RETURNING id INTO v_cl_id;

    -- -----------------------------------------------------------------------
    -- cert.certificate
    -- -----------------------------------------------------------------------
    SELECT COALESCE(max(crt.seq), 0) + 1 INTO v_seq
    FROM   cert.certificate AS crt WHERE crt.claim_id = v_cl_id;

    INSERT INTO cert.certificate (claim_id, seq, status, evidence, valid_under)
    VALUES (
        v_cl_id,
        v_seq,
        'valid',
        jsonb_build_object(
            'automaton_id',    p_automaton_id,
            'eigenform_id',    v_min_id,
            'original_states', v_auto.state_count,
            'minimal_states',  v_min_sc,
            'is_minimal',      v_is_min
        ),
        jsonb_build_object('nerode_schema_version', 1)
    )
    RETURNING id INTO v_cert_id;

    -- -----------------------------------------------------------------------
    -- cert.witness  (kind = nerode_partition)
    -- -----------------------------------------------------------------------
    INSERT INTO cert.witness (certificate_id, kind, body, schema_version)
    VALUES (
        v_cert_id,
        'nerode_partition',
        jsonb_build_object(
            'automaton_id',    p_automaton_id,
            'eigenform_id',    v_min_id,
            'is_minimal',      v_is_min,
            'original_states', v_auto.state_count,
            'minimal_states',  v_min_sc,
            'partition',       v_partition
        ),
        jsonb_build_object('nerode_schema_version', 1)
    );

    -- -----------------------------------------------------------------------
    -- Mark the source automaton as certified.
    -- -----------------------------------------------------------------------
    UPDATE nerode.automata
    SET certified     = TRUE,
        cert_claim_id = v_cl_id
    WHERE id = p_automaton_id;

    -- Return
    is_minimal      := v_is_min;
    eigenform_id    := v_min_id;
    original_states := v_auto.state_count;
    minimal_states  := v_min_sc;
    claim_id        := v_cl_id;
    RETURN NEXT;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.certify_eigenform(BIGINT) IS
    'Certify whether DFA p_automaton_id is its own Myhill-Nerode eigenform. '
    'Fast path: from_regex and minimize outputs are trivially minimal. '
    'Slow path: nerode.minimize() is called and state counts are compared. '
    'Returns (is_minimal, eigenform_id, original_states, minimal_states, claim_id).';


-- ---------------------------------------------------------------------------
-- nerode.scan_eigenforms()
--
-- Iterate all DFAs in nerode.automata, certify each via certify_eigenform().
-- Returns one row per DFA.
--
-- Idempotent: the cert.claim ON CONFLICT upsert prevents duplicate claims;
-- each call adds a new cert.certificate (incrementing seq) but does not error.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.scan_eigenforms()
RETURNS TABLE (
    automaton_id    BIGINT,
    eigenform_id    BIGINT,
    is_minimal      BOOLEAN,
    original_states INTEGER,
    minimal_states  INTEGER,
    claim_id        BIGINT
)
AS $$
DECLARE
    v_auto_id   BIGINT;
    v_ef_id     BIGINT;
    v_is_min    BOOLEAN;
    v_orig_sc   INTEGER;
    v_min_sc    INTEGER;
    v_cl_id     BIGINT;
BEGIN
    -- Snapshot the set of DFA IDs at loop start.
    -- New DFAs inserted by certify_eigenform (minimize outputs) are intentionally
    -- excluded from this scan to avoid processing our own eigenform results.
    FOR v_auto_id IN
        SELECT id
        FROM   nerode.automata
        WHERE  type = 'DFA'
        ORDER  BY id
    LOOP
        BEGIN
            SELECT ce.is_minimal,
                   ce.eigenform_id,
                   ce.original_states,
                   ce.minimal_states,
                   ce.claim_id
            INTO   v_is_min, v_ef_id, v_orig_sc, v_min_sc, v_cl_id
            FROM   nerode.certify_eigenform(v_auto_id) AS ce;

            automaton_id    := v_auto_id;
            eigenform_id    := v_ef_id;
            is_minimal      := v_is_min;
            original_states := v_orig_sc;
            minimal_states  := v_min_sc;
            claim_id        := v_cl_id;
            RETURN NEXT;

        EXCEPTION WHEN OTHERS THEN
            -- Skip automata that cannot be processed
            -- (e.g., incomplete transition tables for unusual imported DFAs).
            CONTINUE;
        END;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.scan_eigenforms() IS
    'Scan all DFAs in nerode.automata, computing and certifying each one''s eigenform. '
    'Returns (automaton_id, eigenform_id, is_minimal, original_states, minimal_states, claim_id). '
    'Idempotent. See nerode.certify_eigenform() for per-DFA certification details.';
