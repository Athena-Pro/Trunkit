-- =============================================================================
--  nerode — Step B3: Proof-carrying gate decisions (Porter policy gate)  [Phase 5]
--
--  Records a policy_gate decision as a cert.claim + certificate + witness, so a
--  consumer re-derives ALLOW/REVISE/BLOCK from the pinned ledger snapshot and
--  the policy predicates with no trust in the producer (ContractGuard,
--  arXiv:2606.18550: "the gate is only as honest as its contracts" — so pin the
--  inputs). Two SEB borrows (arXiv:2606.20520): a live-state drift check and a
--  ledger_hash validity anchor make an ALLOW a short-lived, revocable capability.
--
--  Verdict → cert status:  ALLOW→valid, BLOCK→refuted, REVISE→unverified.
--
--  Depends on: B0/B1/B2 + cert schema (10_cert / calx cert). Idempotent.
--  NOTE: starter file — not yet executed against a live database.
-- =============================================================================

INSERT INTO cert.method (name, claim_kind, checker_kind, description)
VALUES (
    'policy_gate',
    'computational',
    'sql',
    'Policy-gate decision over the session ledger (arXiv:2606.20529). '
    'Re-verifiable by replaying the predicates against the pinned ledger snapshot.'
)
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Internal: recompute the verdict for a tool over a GIVEN ledger+args snapshot
-- (not the live session). Shared by replay_gate. Mirrors policy_gate's logic.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.gate_verdict_over(
    p_tool TEXT, p_ledger JSONB, p_args JSONB)
RETURNS TEXT AS $$
DECLARE
    v_rule RECORD;
    v_ok   BOOLEAN;
    v_block BOOLEAN := FALSE;
    v_soft  BOOLEAN := FALSE;
BEGIN
    FOR v_rule IN
        SELECT predicate_sql, effect FROM nerode.policy_rule
        WHERE tool = p_tool AND enabled ORDER BY id
    LOOP
        v_ok := nerode.eval_policy(v_rule.predicate_sql, p_ledger, p_args);
        IF v_ok IS TRUE THEN
            CONTINUE;
        ELSIF v_ok IS FALSE AND v_rule.effect = 'block' THEN
            v_block := TRUE;
        ELSE
            v_soft := TRUE;
        END IF;
    END LOOP;

    RETURN CASE WHEN v_block THEN 'BLOCK'
                WHEN v_soft  THEN 'REVISE'
                ELSE 'ALLOW' END;
END;
$$ LANGUAGE plpgsql STABLE;

-- ---------------------------------------------------------------------------
-- nerode.replay_gate(witness) → BOOLEAN
--   Consumer re-verification: recompute the verdict from the PINNED ledger +
--   args (in the witness) against the policy predicates, and confirm it equals
--   the recorded verdict. Pure replay; no INSERT; no live-session dependence.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.replay_gate(p_witness JSONB)
RETURNS BOOLEAN
LANGUAGE sql STABLE AS $$
    SELECT nerode.gate_verdict_over(
               p_witness->>'tool',
               COALESCE(p_witness->'ledger', '{}'::jsonb),
               COALESCE(p_witness->'args',   '{}'::jsonb)
           ) = (p_witness->>'verdict');
$$;

COMMENT ON FUNCTION nerode.replay_gate(JSONB) IS
    'Re-verify a gate_decision witness: recompute the verdict from the pinned '
    'ledger+args against the predicates and confirm it matches. Pure replay.';

-- ---------------------------------------------------------------------------
-- nerode.gate_drift(session, witness) → BOOLEAN   (SEB live-state drift)
--   TRUE iff the live session ledger differs from the snapshot the decision was
--   made on ⇒ the decision is stale and the action must be re-gated.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.gate_drift(p_session_id TEXT, p_witness JSONB)
RETURNS BOOLEAN
LANGUAGE sql STABLE AS $$
    SELECT nerode.ledger_hash(p_session_id) IS DISTINCT FROM (p_witness->>'ledger_hash');
$$;

COMMENT ON FUNCTION nerode.gate_drift(TEXT, JSONB) IS
    'SEB live-state drift: TRUE if the live ledger no longer matches the snapshot '
    'the decision was made on ⇒ re-gate before acting.';

-- ---------------------------------------------------------------------------
-- nerode.certify_gate(session, tool, args) → BIGINT (cert claim id)
--   Run the gate and record the decision as a proof-carrying claim.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.certify_gate(
    p_session_id TEXT, p_tool TEXT, p_args JSONB DEFAULT '{}')
RETURNS BIGINT AS $$
DECLARE
    v_verdict  TEXT;
    v_feedback TEXT;
    v_witness  JSONB;
    v_status   TEXT;
    v_statement TEXT;
    v_claim_id BIGINT;
    v_cert_id  BIGINT;
    v_seq      INTEGER;
    v_probe    TEXT;
BEGIN
    SELECT verdict, feedback, witness
    INTO   v_verdict, v_feedback, v_witness
    FROM   nerode.policy_gate(p_session_id, p_tool, p_args);

    v_status := CASE v_verdict
                    WHEN 'ALLOW'  THEN 'valid'
                    WHEN 'BLOCK'  THEN 'refuted'
                    ELSE 'unverified' END;        -- REVISE

    -- Distinct decisions (different args/ledger) are distinct claims; identical
    -- re-gates re-attest (append a certificate seq), like close_session.
    v_statement := 'gate:' || p_session_id || ':' || p_tool || ':'
                   || md5(COALESCE(p_args::text,'') || (v_witness->>'ledger_hash'));

    v_probe := format('SELECT nerode.replay_gate(%L::jsonb)', v_witness);

    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
    VALUES (
        'policy_gate',
        jsonb_build_object('session_id', p_session_id, 'tool', p_tool, 'args', p_args),
        v_statement, 'computational', 'policy_gate', v_probe)
    ON CONFLICT (statement) DO UPDATE SET subject_ref = EXCLUDED.subject_ref, probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_claim_id;

    SELECT COALESCE(max(seq),0)+1 INTO v_seq FROM cert.certificate WHERE claim_id = v_claim_id;

    INSERT INTO cert.certificate (claim_id, seq, status, evidence, valid_under)
    VALUES (
        v_claim_id, v_seq, v_status,
        jsonb_build_object('verdict', v_verdict, 'feedback', v_feedback,
                           'rules_evaluated', v_witness->'rules_evaluated'),
        jsonb_build_object('nerode_schema_version', 1,
                           'ledger_hash', v_witness->>'ledger_hash',
                           'decided_at',  v_witness->>'decided_at'))
    RETURNING id INTO v_cert_id;

    INSERT INTO cert.witness (certificate_id, kind, body, schema_version)
    VALUES (v_cert_id, 'gate_decision', v_witness,
            jsonb_build_object('nerode_schema_version', 1));

    RETURN v_claim_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.certify_gate(TEXT, TEXT, JSONB) IS
    'Run the policy gate and record the decision as a proof-carrying cert claim '
    '(ALLOW→valid, BLOCK→refuted, REVISE→unverified) with a replayable '
    'gate_decision witness and a ledger_hash drift anchor. Returns the claim id.';
