-- cert step 90: agentic vocabulary — record Agentic-Redux-style decisions
-- in the cert ledger without enforcing them.
--
-- Trunkit acts as a witness to agentic workflows, not as an adjudication gate.
-- An external meta-agent (or human counselor) makes decisions; this layer
-- records them as immutable, hash-chained certificates that can be audited
-- and exported alongside proof bundles.
--
-- Two new method tiers:
--   domain_invariant_decl  — record that an invariant was declared for a domain
--   agent_adjudication     — record a meta-agent decision on a proposal
--
-- Two helper functions:
--   cert.declare_invariant(domain, name, statement, footprint, kind)
--   cert.record_decision(agent_id, session_id, proposal, decision, reason, invariants)
--
-- Idempotent.

-- ---- New method tiers -------------------------------------------------------
INSERT INTO cert.method (name, claim_kind, checker_kind, description) VALUES
    ('domain_invariant_decl', 'agentic', 'sql',
     'Records that a domain invariant was declared (BFO-grounded or otherwise). '
     'Provenance-only: an external system enforces it; Trunkit witnesses the declaration.'),
    ('agent_adjudication', 'agentic', 'sql',
     'Records a meta-agent adjudication decision (approved/rejected/escalated) on a '
     'sub-agent proposal. probe_sql is NULL; evidence carries the full decision record. '
     'Trunkit is a witness and audit ledger, not the adjudication gate.')
ON CONFLICT (name) DO NOTHING;

-- ---- cert.declare_invariant -------------------------------------------------
-- Record that a domain declared an invariant. Provenance-only: Trunkit witnesses
-- the declaration; enforcement is the responsibility of the external meta-agent.
--
-- p_domain    : name of the problem domain (e.g. 'udt_compliance')
-- p_name      : short invariant identifier (e.g. 'MAINTAINER_RATE_LIMIT')
-- p_statement : human-readable invariant statement
-- p_footprint : JSONB array of state component names this invariant reads
-- p_kind      : 'local' (one worker) or 'cross_cutting' (spans workers; meta enforces)
--
-- Returns the new certificate row (status always 'unverified': declared, not proven).
CREATE OR REPLACE FUNCTION cert.declare_invariant(
    p_domain     TEXT,
    p_name       TEXT,
    p_statement  TEXT,
    p_footprint  JSONB DEFAULT '[]'::jsonb,
    p_kind       TEXT  DEFAULT 'cross_cutting'
)
RETURNS cert.certificate
LANGUAGE plpgsql AS $$
DECLARE
    v_claim_id  BIGINT;
    v_seq       INTEGER;
    v_inf       TEXT;
    v_cert      cert.certificate%ROWTYPE;
    v_full_stmt TEXT;
BEGIN
    v_full_stmt := format(
        'domain %s declares invariant %s (%s): %s',
        p_domain, p_name, p_kind, p_statement
    );

    INSERT INTO cert.claim
        (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
    VALUES (
        'domain_invariant',
        jsonb_build_object(
            'domain',    p_domain,
            'invariant', p_name,
            'kind',      p_kind,
            'footprint', p_footprint
        ),
        v_full_stmt,
        'agentic', 'domain_invariant_decl', NULL
    )
    ON CONFLICT (statement) DO NOTHING;

    SELECT id INTO v_claim_id FROM cert.claim WHERE statement = v_full_stmt;

    v_inf := gen_random_uuid()::text;
    INSERT INTO curry.inferences
        (inference_id, model_name, model_version, input_tokens,
         output_tokens, temperature_used, seed, metadata)
    VALUES (
        v_inf, 'cert-checker-model', 1,
        jsonb_build_object('domain', p_domain, 'invariant', p_name)::text,
        convert_to('declared', 'UTF8'), 0.0, 0,
        jsonb_build_object('method', 'domain_invariant_decl', 'claim_kind', 'agentic')
    );

    SELECT COALESCE(MAX(seq), 0) + 1 INTO v_seq
      FROM cert.certificate WHERE claim_id = v_claim_id;

    INSERT INTO cert.certificate
        (claim_id, seq, status, evidence, valid_under, checker_inference_id)
    VALUES (
        v_claim_id, v_seq, 'unverified',
        jsonb_build_object(
            'domain',    p_domain,
            'invariant', p_name,
            'kind',      p_kind,
            'footprint', p_footprint,
            'note',      'declared; enforcement is external to Trunkit'
        ),
        '{}'::jsonb,
        v_inf
    )
    RETURNING * INTO v_cert;

    RETURN v_cert;
END
$$;

-- ---- cert.record_decision ---------------------------------------------------
-- One-shot helper: create a claim + immediate certificate for an agentic
-- adjudication decision. The caller (meta-agent or thin adapter) supplies
-- what happened; Trunkit appends it to the hash-chained ledger.
--
-- p_agent_id   : sub-agent identifier (e.g. 'LabOrderAgent')
-- p_session_id : session / run identifier
-- p_proposal   : JSONB description of what was proposed; should include 'action'
-- p_decision   : 'approved' | 'rejected' | 'escalated'
-- p_reason     : human-readable reason (e.g. which invariant would have been violated)
-- p_invariants : JSONB array of invariant names that were checked
--
-- Decision maps to cert status:
--   approved  → valid      (proposal accepted; state committed)
--   rejected  → refuted    (proposal denied; an invariant would have been violated)
--   escalated → unverified (deposited to counselor queue; awaiting human decision)
--
-- Returns the new certificate row.
CREATE OR REPLACE FUNCTION cert.record_decision(
    p_agent_id   TEXT,
    p_session_id TEXT,
    p_proposal   JSONB,
    p_decision   TEXT,
    p_reason     TEXT  DEFAULT NULL,
    p_invariants JSONB DEFAULT '[]'::jsonb
)
RETURNS cert.certificate
LANGUAGE plpgsql AS $$
DECLARE
    v_status    TEXT;
    v_claim_id  BIGINT;
    v_seq       INTEGER;
    v_inf       TEXT;
    v_cert      cert.certificate%ROWTYPE;
    v_statement TEXT;
BEGIN
    IF p_decision NOT IN ('approved', 'rejected', 'escalated') THEN
        RAISE EXCEPTION
            'cert.record_decision: p_decision must be approved|rejected|escalated, got %',
            p_decision;
    END IF;

    v_status := CASE p_decision
        WHEN 'approved'  THEN 'valid'
        WHEN 'rejected'  THEN 'refuted'
        WHEN 'escalated' THEN 'unverified'
    END;

    v_statement := format(
        'agent %s session %s proposed %s — decision: %s',
        p_agent_id,
        p_session_id,
        COALESCE(p_proposal->>'action', p_proposal::text),
        p_decision
    );

    INSERT INTO cert.claim
        (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
    VALUES (
        'agentic_proposal',
        jsonb_build_object(
            'agent_id',   p_agent_id,
            'session_id', p_session_id,
            'proposal',   p_proposal
        ),
        v_statement,
        'agentic', 'agent_adjudication', NULL
    )
    ON CONFLICT (statement) DO NOTHING;

    SELECT id INTO v_claim_id FROM cert.claim WHERE statement = v_statement;

    v_inf := gen_random_uuid()::text;
    INSERT INTO curry.inferences
        (inference_id, model_name, model_version, input_tokens,
         output_tokens, temperature_used, seed, metadata)
    VALUES (
        v_inf, 'cert-checker-model', 1,
        jsonb_build_object(
            'agent_id',   p_agent_id,
            'session_id', p_session_id,
            'proposal',   p_proposal
        )::text,
        convert_to(p_decision, 'UTF8'), 0.0, 0,
        jsonb_build_object('method', 'agent_adjudication', 'claim_kind', 'agentic')
    );

    SELECT COALESCE(MAX(seq), 0) + 1 INTO v_seq
      FROM cert.certificate WHERE claim_id = v_claim_id;

    INSERT INTO cert.certificate
        (claim_id, seq, status, evidence, valid_under, checker_inference_id)
    VALUES (
        v_claim_id, v_seq, v_status,
        jsonb_build_object(
            'decision',           p_decision,
            'reason',             p_reason,
            'invariants_checked', p_invariants,
            'proposal',           p_proposal,
            'agent_id',           p_agent_id,
            'session_id',         p_session_id
        ),
        '{}'::jsonb,
        v_inf
    )
    RETURNING * INTO v_cert;

    RETURN v_cert;
END
$$;
