-- Unified model, step 97: ledger self-topology (tool-on-tool Betti self-audit).
--
-- Trunkit's `dfa_betti` kernel (step 94) computes Betti numbers of any graph.
-- Here Trunkit points it at ITS OWN ledger: the cert/curry entanglement graph
-- (step 95) is extracted as a graph and its Betti signature is certified, with
-- the witness BEING a dfa_betti proof object so a consumer re-checks beta1
-- independently — the kernel measuring the system that contains it.
--
-- The entanglement graph (vertices = ledger records, edges = hash references):
--   prev_hash  : cert -> its predecessor cert        (the chain / time axis)
--   provenance : cert -> its curry inference          (the curry axis)
--   premise    : conclusion cert -> each premise cert (the derivation axis)
--
-- Topological reading (DFA-graph Betti, step 98 conventions):
--   beta1 = E - V + beta0 = number of INDEPENDENT entanglement cycles.
--   A pure hash CHAIN has beta1 = 0 (truncatable from an end). Each derivation
--   and each shared-provenance reuse adds a cycle: beta1 is a direct measure of
--   how web-like — and thus how tamper-resistant — the ledger actually is.
--
-- HONESTY: this certifies the *measured* beta1 of the live ledger, not an
-- aspirational threshold. On a fresh, derivation-free ledger beta1 may be 0;
-- that is reported truthfully, not dressed up.
--
-- Idempotent. Requires step 94 (cert.kernel_dfa_betti) + step 95 (ledger hashes).

-- ---------------------------------------------------------------------------
-- Ensure the 'betti' witness kind is permitted on a Trunkit-only DB (Nerode's
-- step 98 also adds it, but step 97 must be self-sufficient). Idempotent
-- superset replace — keeps every previously-allowed kind.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    ALTER TABLE cert.witness DROP CONSTRAINT IF EXISTS witness_kind_check;
    ALTER TABLE cert.witness DROP CONSTRAINT IF EXISTS cert_witness_kind_check;
    ALTER TABLE cert.witness
        ADD CONSTRAINT cert_witness_kind_check CHECK (kind IN (
            'term', 'trace', 'counterexample', 'hash_chain', 'kan_diagram',
            'construction_record', 'computation_trace',
            'nerode_partition', 'bisimulation', 'state_map',
            'betti'
        ));
EXCEPTION WHEN OTHERS THEN
    NULL;
END;
$$;

-- ---------------------------------------------------------------------------
-- cert.ledger_graph() -> JSONB {"V": n, "edges": [[i,j],...]}
-- A dfa_betti-ready proof object built from the live entanglement graph.
-- Vertices are densely re-indexed 0..V-1 over all referenced ledger records.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION cert.ledger_graph()
RETURNS JSONB LANGUAGE sql STABLE AS $$
WITH
-- raw directed references between ledger records, as text node keys
refs AS (
    -- prev_hash chain: cert -> predecessor cert (match hash to row_hash)
    SELECT format('c%s', c.id) AS a,
           format('c%s', p.id) AS b
      FROM cert.certificate c
      JOIN cert.certificate p ON p.row_hash = c.prev_hash
     WHERE c.prev_hash IS NOT NULL
    UNION ALL
    -- provenance: cert -> its curry inference
    SELECT format('c%s', c.id),
           format('i:%s', c.checker_inference_id)
      FROM cert.certificate c
     WHERE c.checker_inference_id IS NOT NULL
    UNION ALL
    -- derivation premises: conclusion's latest cert -> each premise's latest cert
    SELECT format('c%s', cc.id),
           format('c%s', pc.id)
      FROM cert.derivation d
      JOIN LATERAL (
          SELECT id FROM cert.certificate
           WHERE claim_id = d.conclusion_id ORDER BY seq DESC LIMIT 1
      ) cc ON TRUE
      CROSS JOIN LATERAL unnest(d.premise_ids) AS prem(pid)
      JOIN LATERAL (
          SELECT id FROM cert.certificate
           WHERE claim_id = prem.pid ORDER BY seq DESC LIMIT 1
      ) pc ON TRUE
),
nodes AS (
    SELECT a AS k FROM refs UNION SELECT b FROM refs
),
idx AS (
    SELECT k, (row_number() OVER (ORDER BY k)) - 1 AS i FROM nodes
)
SELECT jsonb_build_object(
    'schema', 'dfa_betti',
    'V', (SELECT count(*) FROM idx),
    'edges', COALESCE(
        (SELECT jsonb_agg(jsonb_build_array(ia.i, ib.i))
           FROM refs r
           JOIN idx ia ON ia.k = r.a
           JOIN idx ib ON ib.k = r.b),
        '[]'::jsonb)
);
$$;

COMMENT ON FUNCTION cert.ledger_graph() IS
    'The cert/curry entanglement graph (step 95) as a dfa_betti proof object: '
    'vertices = ledger records, edges = prev_hash/provenance/premise references. '
    'Feed to cert.kernel_dfa_betti to measure the ledger''s Betti signature.';

-- ---------------------------------------------------------------------------
-- cert.certify_ledger_betti() -> the self-audit claim.
-- Measures the live ledger's Betti signature with the dfa_betti kernel and
-- records a claim + certificate + dfa_betti witness (re-verifiable by any
-- consumer via cert.kernel_dfa_betti / calx.kernel.check_dfa_betti).
-- Returns (V, E, beta0, beta1, euler_char, claim_id).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION cert.certify_ledger_betti()
RETURNS TABLE (V INT, E INT, beta0 INT, beta1 INT, euler_char INT, claim_id BIGINT)
LANGUAGE plpgsql AS $$
DECLARE
    v_graph  JSONB;
    v_ok     BOOLEAN;
    v_ev     JSONB;
    v_V INT; v_E INT; v_b0 INT; v_b1 INT; v_chi INT;
    v_stmt   TEXT;
    v_cl_id  BIGINT;
    v_seq    INT;
    v_inf    TEXT;
    v_cert_id BIGINT;
    v_witness JSONB;
BEGIN
    v_graph := cert.ledger_graph();

    -- Measure with the independent kernel (tool-on-tool).
    SELECT k.ok, k.evidence INTO v_ok, v_ev FROM cert.kernel_dfa_betti(v_graph) k;
    v_V   := (v_ev->>'V')::INT;
    v_E   := (v_ev->>'E')::INT;
    v_b0  := (v_ev->>'beta0')::INT;
    v_b1  := (v_ev->>'beta1')::INT;
    v_chi := (v_ev->>'euler_char')::INT;

    -- Statement excludes the counts so it is stable across re-checks; the
    -- measured signature lives in evidence/witness and is re-derived each run.
    v_stmt := 'cert ledger entanglement graph Betti signature (self-audit)';

    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
    VALUES ('cert_ledger',
            jsonb_build_object('graph', 'cert/curry entanglement'),
            v_stmt, 'structural', 'cert_kernel', NULL)
    ON CONFLICT (statement) DO UPDATE SET subject_ref = EXCLUDED.subject_ref
    RETURNING id INTO v_cl_id;

    -- Provenance row for this self-check.
    v_inf := gen_random_uuid()::text;
    INSERT INTO curry.inferences
        (inference_id, model_name, model_version, input_tokens,
         output_tokens, temperature_used, seed, metadata)
    VALUES (v_inf, 'cert-checker-model', 1,
            jsonb_build_object('claim_id', v_cl_id, 'statement', v_stmt)::text,
            convert_to('valid', 'UTF8'), 0.0, 0,
            jsonb_build_object('method', 'cert_kernel', 'schema', 'dfa_betti',
                               'self_audit', true));

    SELECT COALESCE(max(ce.seq), 0) + 1 INTO v_seq
      FROM cert.certificate ce WHERE ce.claim_id = v_cl_id;

    INSERT INTO cert.certificate (claim_id, seq, status, evidence, valid_under, checker_inference_id)
    VALUES (v_cl_id, v_seq,
            CASE WHEN v_ok IS FALSE THEN 'refuted' ELSE 'valid' END,
            v_ev || jsonb_build_object('beta1_meaning', 'independent entanglement cycles'),
            jsonb_build_object('measured', true), v_inf)
    RETURNING id INTO v_cert_id;

    -- Witness IS the graph as a dfa_betti proof object, with the measured
    -- signature asserted — a consumer re-runs the kernel and must get the same.
    v_witness := v_graph || jsonb_build_object(
        'asserts', jsonb_build_object('beta0', v_b0, 'beta1', v_b1));
    INSERT INTO cert.witness (certificate_id, kind, body, schema_version)
    VALUES (v_cert_id, 'betti', v_witness, jsonb_build_object('calx_schema_version', 1));

    V := v_V; E := v_E; beta0 := v_b0; beta1 := v_b1; euler_char := v_chi; claim_id := v_cl_id;
    RETURN NEXT;
END;
$$;

COMMENT ON FUNCTION cert.certify_ledger_betti() IS
    'Self-audit: measure the live cert/curry entanglement graph''s Betti '
    'signature with the dfa_betti kernel and record it as a re-verifiable claim '
    '(witness IS a dfa_betti proof object). beta1 = independent entanglement '
    'cycles; a pure chain has beta1 = 0. Reports the measured value honestly.';
