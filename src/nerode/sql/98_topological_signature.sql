-- =============================================================================
--  nerode — Step 98: DFA Betti signature (topological fingerprint)
--
--  A DFA transition graph is a 1-dimensional complex: states are vertices,
--  transitions are edges (self-loops and parallel edges count). Its homology is
--  fully determined by:
--      beta0 = connected components (undirected)
--      beta1 = E - V + beta0          (circuit rank — independent cycles)
--      chi   = V - E = beta0 - beta1  (beta_n = 0 for n >= 2)
--
--  This is the producer side of the LQLE topological bridge: Nerode computes the
--  signature from nerode.states / nerode.transitions (it owns the graph and the
--  minimize engine). The portable, untrusted-consumer re-check lives in the
--  cert_kernel `dfa_betti` schema (calx sql/94 + calx.kernel.check_dfa_betti):
--  the witness {V, edges, asserts:{beta0,beta1}} is re-verified with one
--  union-find pass, without trusting Nerode.
--
--  Honest scope: beta1 is NOT a minimization invariant — merging Nerode-
--  equivalent states changes the graph. We attest each DFA's OWN signature; the
--  minimal eigenform's signature (step 40) is the canonical fingerprint. We do
--  not claim "Betti preserved under minimize".
--
--  Functions:
--    nerode.betti_signature(automaton_id)  -> (V, E, beta0, beta1, chi)
--    nerode.dfa_edge_list(automaton_id)    -> JSONB [[from,to],...] (witness edges)
--    nerode.certify_betti(automaton_id)    -> issues cert.claim/certificate/witness
--                                             + submits the dfa_betti proof object
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Register cert.method
-- ---------------------------------------------------------------------------
INSERT INTO cert.method (name, claim_kind, checker_kind, description) VALUES
    ('nerode_betti',
     'structural', 'sql',
     'Betti signature (beta0, beta1, chi) of a DFA transition graph, computed '
     'from nerode.states/transitions. beta1 = E - V + beta0 (circuit rank). '
     'Producer side of the LQLE topological bridge; the portable re-check is the '
     'cert_kernel dfa_betti schema. Witness kind = betti.')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Extend the cert.witness kind constraint to include 'betti'.
-- Idempotent superset replace (same pattern as 00_bootstrap / 70_morphism).
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    ALTER TABLE cert.witness DROP CONSTRAINT IF EXISTS witness_kind_check;
    ALTER TABLE cert.witness DROP CONSTRAINT IF EXISTS cert_witness_kind_check;
    ALTER TABLE cert.witness
        ADD CONSTRAINT cert_witness_kind_check CHECK (kind IN (
            -- Trunkit kinds
            'term', 'trace', 'counterexample', 'hash_chain', 'kan_diagram',
            -- Nerode kinds
            'construction_record', 'computation_trace',
            'nerode_partition', 'bisimulation', 'state_map',
            -- topological bridge
            'betti'
        ));
EXCEPTION WHEN OTHERS THEN
    NULL;
END;
$$;

-- ---------------------------------------------------------------------------
-- nerode.dfa_edge_list(automaton_id) -> JSONB array of [from_state, to_state]
-- One element per transition row (self-loops and parallel symbols included), so
-- the edge list matches the multigraph the Betti numbers are computed over and
-- is directly usable as a dfa_betti witness.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.dfa_edge_list(p_automaton_id BIGINT)
RETURNS JSONB LANGUAGE sql STABLE AS $$
    SELECT COALESCE(
        jsonb_agg(jsonb_build_array(t.from_state, t.to_state) ORDER BY t.from_state, t.to_state),
        '[]'::jsonb)
    FROM nerode.transitions t
    WHERE t.automaton_id = p_automaton_id
      AND t.symbol IS NOT NULL;   -- DFA edges only (ignore epsilon)
$$;

-- ---------------------------------------------------------------------------
-- nerode.betti_signature(automaton_id) -> (V, E, beta0, beta1, chi)
-- beta0 via recursive union of the undirected transition graph.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.betti_signature(p_automaton_id BIGINT)
RETURNS TABLE (V INT, E INT, beta0 INT, beta1 INT, chi INT)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_V INT;
    v_E INT;
    v_comp INT;
BEGIN
    SELECT count(*)::int INTO v_V
      FROM nerode.states WHERE automaton_id = p_automaton_id;

    SELECT count(*)::int INTO v_E
      FROM nerode.transitions
     WHERE automaton_id = p_automaton_id AND symbol IS NOT NULL;

    IF v_V = 0 THEN
        RETURN QUERY SELECT 0, 0, 0, 0, 0;
        RETURN;
    END IF;

    -- Connected components of the undirected transition graph via iterative
    -- label propagation over a temp adjacency (small graphs; DFAs are tiny).
    WITH RECURSIVE
    edges AS (
        SELECT from_state AS a, to_state AS b
          FROM nerode.transitions
         WHERE automaton_id = p_automaton_id AND symbol IS NOT NULL
        UNION ALL
        SELECT to_state, from_state
          FROM nerode.transitions
         WHERE automaton_id = p_automaton_id AND symbol IS NOT NULL
    ),
    -- min reachable label per node (undirected reachability)
    nodes AS (
        SELECT state_id AS n FROM nerode.states WHERE automaton_id = p_automaton_id
    ),
    reach(n, lbl) AS (
        SELECT n, n FROM nodes
        UNION
        SELECT e.b, r.lbl
          FROM reach r JOIN edges e ON e.a = r.n
    ),
    comp AS (
        SELECT n, min(lbl) AS root FROM reach GROUP BY n
    )
    SELECT count(DISTINCT root)::int INTO v_comp FROM comp;

    v_comp := COALESCE(v_comp, v_V);

    RETURN QUERY SELECT
        v_V,
        v_E,
        v_comp,                       -- beta0
        v_E - v_V + v_comp,           -- beta1 = E - V + beta0
        v_V - v_E;                    -- chi
END;
$$;

-- ---------------------------------------------------------------------------
-- nerode.certify_betti(automaton_id)
-- Compute the signature, issue cert.claim/certificate/witness(betti), and submit
-- the matching dfa_betti proof object so a consumer can re-verify independently.
-- Returns TABLE (V, E, beta0, beta1, chi, claim_id).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.certify_betti(p_automaton_id BIGINT)
RETURNS TABLE (V INT, E INT, beta0 INT, beta1 INT, chi INT, claim_id BIGINT)
LANGUAGE plpgsql AS $$
DECLARE
    v_auto   nerode.automata%ROWTYPE;
    v_sig    RECORD;
    v_edges  JSONB;
    v_stmt   TEXT;
    v_cl_id  BIGINT;
    v_seq    INT;
    v_cert_id BIGINT;
    v_witness JSONB;
BEGIN
    SELECT * INTO v_auto FROM nerode.automata WHERE id = p_automaton_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'nerode.certify_betti: automaton % not found', p_automaton_id;
    END IF;

    SELECT * INTO v_sig FROM nerode.betti_signature(p_automaton_id);
    v_edges := nerode.dfa_edge_list(p_automaton_id);

    v_stmt := format(
        'betti(%s [%s]): V=%s, E=%s, b0=%s, b1=%s, chi=%s',
        p_automaton_id, coalesce(v_auto.name, '?'),
        v_sig.V, v_sig.E, v_sig.beta0, v_sig.beta1, v_sig.chi
    );

    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method)
    VALUES (
        'nerode_automaton',
        jsonb_build_object('automaton_id', p_automaton_id),
        v_stmt, 'structural', 'nerode_betti'
    )
    ON CONFLICT (statement) DO UPDATE SET subject_ref = EXCLUDED.subject_ref
    RETURNING id INTO v_cl_id;

    SELECT COALESCE(max(crt.seq), 0) + 1 INTO v_seq
      FROM cert.certificate AS crt WHERE crt.claim_id = v_cl_id;

    INSERT INTO cert.certificate (claim_id, seq, status, evidence, valid_under)
    VALUES (
        v_cl_id, v_seq, 'valid',
        jsonb_build_object(
            'automaton_id', p_automaton_id,
            'V', v_sig.V, 'E', v_sig.E,
            'beta0', v_sig.beta0, 'beta1', v_sig.beta1, 'euler_char', v_sig.chi),
        jsonb_build_object('nerode_schema_version', 1)
    )
    RETURNING id INTO v_cert_id;

    -- Witness body IS a dfa_betti proof object: a consumer re-checks it with
    -- calx.kernel.check_dfa_betti (or cert.kernel_dfa_betti) without trusting us.
    v_witness := jsonb_build_object(
        'schema', 'dfa_betti',
        'V', v_sig.V,
        'edges', v_edges,
        'asserts', jsonb_build_object('beta0', v_sig.beta0, 'beta1', v_sig.beta1)
    );

    INSERT INTO cert.witness (certificate_id, kind, body, schema_version)
    VALUES (v_cert_id, 'betti', v_witness, jsonb_build_object('nerode_schema_version', 1));

    V := v_sig.V; E := v_sig.E; beta0 := v_sig.beta0;
    beta1 := v_sig.beta1; chi := v_sig.chi; claim_id := v_cl_id;
    RETURN NEXT;
END;
$$;

COMMENT ON FUNCTION nerode.certify_betti(BIGINT) IS
    'Certify the DFA Betti signature (beta0,beta1,chi) of an automaton. Issues '
    'cert.claim/certificate + a betti witness that IS a dfa_betti proof object, '
    'so a consumer re-verifies it independently via the cert_kernel dfa_betti '
    'schema. beta1 = E - V + beta0; not a minimization invariant (see step 98 header).';

-- ---------------------------------------------------------------------------
-- nerode.scan_betti() — certify every DFA's Betti signature. Idempotent.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.scan_betti()
RETURNS TABLE (automaton_id BIGINT, V INT, E INT, beta0 INT, beta1 INT, chi INT, claim_id BIGINT)
LANGUAGE plpgsql AS $$
DECLARE r RECORD; c RECORD;
BEGIN
    FOR r IN SELECT id FROM nerode.automata WHERE type = 'DFA' ORDER BY id LOOP
        SELECT * INTO c FROM nerode.certify_betti(r.id);
        automaton_id := r.id; V := c.V; E := c.E;
        beta0 := c.beta0; beta1 := c.beta1; chi := c.chi; claim_id := c.claim_id;
        RETURN NEXT;
    END LOOP;
END;
$$;
