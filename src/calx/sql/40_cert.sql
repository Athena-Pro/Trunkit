-- Unified model, step 40: the `cert` pillar — attestation.
--
-- Binds a claim to a checking method, stores immutable version-pinned
-- attestations with provenance, and (by re-checking) detects staleness.
-- NOT a theorem prover: proving lives in TEL/Lean/Agda; `cert` records that a
-- claim was accepted by a method under stated assumptions, re-checkably.
--
-- Each pillar answers one question:
--   calx  : what is true of the integers          (data)
--   curry : what did we compute, reproducibly      (provenance)
--   kan   : how do structures relate               (theory)
--   TEL   : what happens when we run it             (execution)
--   cert  : is this claim attested, by what method, under what assumptions,
--           and is it still valid                   (attestation)
--
-- Idempotent: CREATE ... IF NOT EXISTS / OR REPLACE; seeds guarded.

CREATE SCHEMA IF NOT EXISTS cert;

-- How a claim_kind is checked (descriptive tiering).
CREATE TABLE IF NOT EXISTS cert.method (
    name         TEXT PRIMARY KEY,
    claim_kind   TEXT NOT NULL,           -- computational|structural|formal|empirical
    checker_kind TEXT NOT NULL,           -- sql|curry_fn|kan_fn|external_cmd
    description  TEXT
);

-- A proposition about something in the model. `probe_sql` returns exactly one
-- row (ok BOOLEAN, evidence JSONB); NULL probe_sql => formal/empirical-pending.
CREATE TABLE IF NOT EXISTS cert.claim (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_kind TEXT NOT NULL,
    subject_ref  JSONB NOT NULL,
    statement    TEXT NOT NULL UNIQUE,
    claim_kind   TEXT NOT NULL,
    method       TEXT NOT NULL REFERENCES cert.method(name),
    probe_sql    TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only attestations. Re-checking NEVER mutates — it appends a new seq,
-- the same immutable-audit discipline as Curry constants / kan_self_report.
CREATE TABLE IF NOT EXISTS cert.certificate (
    id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    claim_id             BIGINT NOT NULL REFERENCES cert.claim(id) ON DELETE CASCADE,
    seq                  INTEGER NOT NULL,
    status               TEXT NOT NULL,   -- valid|refuted|unverified|error
    evidence             JSONB NOT NULL DEFAULT '{}'::jsonb,
    valid_under          JSONB NOT NULL DEFAULT '{}'::jsonb,
    checked_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    checker_inference_id TEXT REFERENCES curry.inferences(inference_id),
    UNIQUE (claim_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_cert_certificate_claim
    ON cert.certificate (claim_id, seq DESC);

-- Provenance model for the checker (its runs are recorded as curry.inferences).
INSERT INTO curry.model_versions
    (model_name, version, checkpoint_hash, temperature, top_p, max_tokens)
VALUES ('cert-checker-model', 1, 'cert-v1', 0.0, 1.0, 0)
ON CONFLICT (model_name, version) DO NOTHING;

-- Check one claim: run its probe, version-pin, record provenance, append cert.
CREATE OR REPLACE FUNCTION cert.check(p_claim_id BIGINT)
RETURNS cert.certificate
LANGUAGE plpgsql AS $$
DECLARE
    v_claim       cert.claim%ROWTYPE;
    v_ok          BOOLEAN;
    v_evidence    JSONB;
    v_status      TEXT;
    v_seq         INTEGER;
    v_inf         TEXT;
    v_valid_under JSONB;
    v_cert        cert.certificate%ROWTYPE;
BEGIN
    SELECT * INTO v_claim FROM cert.claim WHERE id = p_claim_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'cert.check: no claim %', p_claim_id;
    END IF;

    -- Version-pin snapshot: what this attestation is valid *under*.
    v_valid_under := jsonb_build_object(
        'calx_schema_version',
            (SELECT convert_from(value, 'UTF8')::jsonb
               FROM curry.constants
              WHERE id = 'calx_schema_version'
              ORDER BY version DESC LIMIT 1),
        'curry_entities',
            (SELECT count(*) FROM curry.constants)
          + (SELECT count(*) FROM curry.functions),
        'kan_objects', (SELECT count(*) FROM kan.object)
    );

    IF v_claim.probe_sql IS NULL THEN
        v_status   := 'unverified';
        v_evidence := jsonb_build_object('note', 'no probe; external/empirical pending');
    ELSE
        BEGIN
            EXECUTE v_claim.probe_sql INTO v_ok, v_evidence;
            v_status := CASE
                WHEN v_ok IS TRUE  THEN 'valid'
                WHEN v_ok IS FALSE THEN 'refuted'
                ELSE 'unverified'
            END;
        EXCEPTION WHEN OTHERS THEN
            v_status   := 'error';
            v_evidence := jsonb_build_object('error', SQLERRM);
        END;
    END IF;

    -- Provenance: the check itself is a recorded curry inference.
    v_inf := gen_random_uuid()::text;
    INSERT INTO curry.inferences
        (inference_id, model_name, model_version, input_tokens,
         output_tokens, temperature_used, seed, metadata)
    VALUES (
        v_inf, 'cert-checker-model', 1,
        jsonb_build_object('claim_id', p_claim_id,
                           'statement', v_claim.statement)::text,
        convert_to(v_status, 'UTF8'), 0.0, 0,
        jsonb_build_object('method', v_claim.method,
                           'claim_kind', v_claim.claim_kind)
    );

    SELECT COALESCE(MAX(seq), 0) + 1 INTO v_seq
      FROM cert.certificate WHERE claim_id = p_claim_id;

    INSERT INTO cert.certificate
        (claim_id, seq, status, evidence, valid_under, checker_inference_id)
    VALUES (p_claim_id, v_seq, v_status,
            COALESCE(v_evidence, '{}'::jsonb), v_valid_under, v_inf)
    RETURNING * INTO v_cert;

    RETURN v_cert;
END
$$;

-- Re-check every claim; returns the resulting status per claim.
CREATE OR REPLACE FUNCTION cert.check_all()
RETURNS TABLE (claim_id BIGINT, statement TEXT, status TEXT)
LANGUAGE sql AS $$
    SELECT c.id, c.statement, (cert.check(c.id)).status
      FROM cert.claim c
     ORDER BY c.id;
$$;

-- Latest attestation per claim.
CREATE OR REPLACE VIEW cert.standing AS
SELECT DISTINCT ON (cl.id)
       cl.id            AS claim_id,
       cl.statement,
       cl.claim_kind,
       cl.method,
       ce.seq,
       ce.status,
       ce.checked_at,
       ce.evidence,
       ce.valid_under
  FROM cert.claim cl
  JOIN cert.certificate ce ON ce.claim_id = cl.id
 ORDER BY cl.id, ce.seq DESC;

-- ---- Seed the four method tiers --------------------------------------------
INSERT INTO cert.method (name, claim_kind, checker_kind, description) VALUES
    ('comp_sql',         'computational', 'sql',
     'Run a calx/Curry expression and compare; fully in-DB, reproducible.'),
    ('struct_kan',       'structural',    'kan_fn',
     'Wrap an existing kan invariant checker (naturality/triangle/faithful).'),
    ('formal_external',  'formal',        'external_cmd',
     'Backed by an external Lean/Agda/TEL artifact (hash + checker command).'),
    ('empirical_corpus', 'empirical',     'sql',
     'Provenance only: claim asserted in a corpus document (not a proof).')
ON CONFLICT (name) DO NOTHING;

-- ---- Seed worked-example claims across all four tiers ----------------------
INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT v.subject_kind, v.subject_ref::jsonb, v.statement, v.claim_kind, v.method, v.probe_sql
FROM (VALUES
    ('calx_expr',
     '{"schema":"calx","expr":"aliquot_step(28)"}',
     'calx.aliquot_step(28) = 28 (28 is a perfect number)',
     'computational', 'comp_sql',
     'SELECT (calx.aliquot_step(28) = 28) AS ok,
             jsonb_build_object(''got'', calx.aliquot_step(28), ''expected'', 28) AS evidence'),

    ('curry_function',
     '{"name":"calx_aliquot_step","version":1}',
     'curry function calx_aliquot_step@v1 is declared pure',
     'computational', 'comp_sql',
     'SELECT bool_and(is_pure) AS ok,
             jsonb_build_object(''is_pure'', bool_and(is_pure)) AS evidence
        FROM curry.functions WHERE name=''calx_aliquot_step'' AND version=1'),

    ('kan_functor',
     '{"functor":"curry_to_calx"}',
     'kan functor curry_to_calx maps exactly 19 objects',
     'structural', 'struct_kan',
     'SELECT (count(*) = 19) AS ok,
             jsonb_build_object(''mapped'', count(*)) AS evidence
        FROM kan.functor_object_map WHERE functor=''curry_to_calx'''),

    ('kan_functor',
     '{"functor":"kan_self"}',
     'kan functor kan_self is an identity endofunctor',
     'structural', 'struct_kan',
     'SELECT (count(*) = 0) AS ok,
             jsonb_build_object(''non_identity_pairs'', count(*)) AS evidence
        FROM kan.functor_object_map
       WHERE functor=''kan_self'' AND src_object <> tgt_object'),

    ('curry_constant',
     '{"id":"kan_self_report","tip":true}',
     'latest kan_self_report has coverage = 1.0 (model self-validates)',
     'structural', 'struct_kan',
     'SELECT ((convert_from(value,''UTF8'')::jsonb->>''coverage'')::float = 1.0) AS ok,
             jsonb_build_object(''coverage'',
                 convert_from(value,''UTF8'')::jsonb->''coverage'') AS evidence
        FROM curry.constants WHERE id=''kan_self_report''
        ORDER BY version DESC LIMIT 1'),

    ('corpus_document',
     '{"slug":"lit_displayed_type_theory"}',
     'corpus contains lit_displayed_type_theory (Kolomatskaia–Shulman)',
     'empirical', 'empirical_corpus',
     'SELECT (count(*) = 1) AS ok,
             jsonb_build_object(''present'', count(*)) AS evidence
        FROM kan.corpus_document WHERE slug=''lit_displayed_type_theory''')
) AS v(subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
WHERE NOT EXISTS (SELECT 1 FROM cert.claim c WHERE c.statement = v.statement);
