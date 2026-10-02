-- Unified model, step 115: the OEIS conjecture -> attestation workflow.
--
-- Implements gap T4 of docs/reports/Trunkit_Erdos_AI_Capability_Fit.md, whose
-- instruction was "extend, don't invent": oeis-load and oeis-match already
-- exist, 92 already proposes candidates by cosine and confirms them by exact
-- prefix, 93 already certifies recurrences and 95 already certifies morphisms.
-- What was missing is the FLOW that runs them in order and records how the
-- steps relate. So this file introduces no new store, no new schema and no new
-- verification method. It is composition, and it is short on purpose.
--
-- THE CHAIN
--   1. candidate            cosine says two sequences look alike  (float_heuristic)
--   2. exact-prefix claim   they agree on the first N terms       (92, comp_sql)
--   3. certificate          the structural reason: a recurrence   (93) or an
--                           exact morphism (95)                   (comp_sql)
--   4. global theorem       "and this holds for ALL n"            (formal_external)
--
-- ============================================================================
-- THE ONE REAL DESIGN DECISION: STEP 4 IS NEVER DERIVED FROM STEPS 1-3.
-- ============================================================================
-- Every finite step above is a statement about a bounded prefix. 92's claim says
-- "exact prefix >= N", 93's says "over L terms", 95's checks "the common
-- prefix". None of them is evidence that the identity holds for all n, and a
-- thousand agreeing terms is still not a proof -- the standard cautionary case,
-- Polya's conjecture, survives to n ~ 9e8 and then fails.
--
-- So cert.derivation edges are recorded for the finite composition ONLY:
--
--     exact-prefix(A,B) + certificate(B)  |-  A matches R over min(N,L) terms
--
-- which is honest transitivity of finite agreement, and nothing else. The
-- global theorem is anchored as its own formal_external claim with NO premise
-- edge into the finite chain. If it were derived from them, then re-checking the
-- ledger would report a theorem as `valid` because twelve terms lined up, which
-- is precisely the laundering of a heuristic into a proof that the three-valued
-- discipline exists to prevent. The theorem stays `unverified` until a Lean
-- checker actually runs it -- that is T1's job, and 104's.
--
-- The finite evidence is still RECORDED on the theorem: its claim ids live in
-- the anchor's subject_ref, so a reader can walk from the conjecture to the
-- work that motivated it. Visible, queryable, and explicitly not support. The
-- direction is the whole point -- a proved theorem would IMPLY the prefix
-- agreement, never the other way round.
--
-- WHY THE CANDIDATE IS A CLAIM AT ALL. Cosine is float8 and 94's shield
-- downgrades any float_heuristic claim to `unverified` at insert time, so step 1
-- can never record `valid`. Recording it anyway is the point: the candidate is
-- how the conjecture was FOUND, which is exactly the provenance T5 wants to
-- attest, and a workflow that silently discarded its own heuristic origin would
-- hide the one step a sceptical reader most wants to see. It is carried, and it
-- is permanently unverifiable, and those are both correct.
--
-- Lives in `calx`, not a new schema: it introduces no object type of its own,
-- and the OEIS store (calx.seq_vector, calx.sequence_membership) is already here.
--
-- Idempotent; additive only.

-- ---------------------------------------------------------------------------
-- 1. the candidate -- how the conjecture was found
-- ---------------------------------------------------------------------------

-- A float_heuristic claim. 94's trigger guarantees it is never `valid`; that is
-- not a defect being tolerated, it is the shield working.
CREATE OR REPLACE FUNCTION calx.oeis_candidate_claim(
    p_query TEXT, p_candidate TEXT, p_kind TEXT DEFAULT 'logc'
) RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE v_stmt TEXT; v_probe TEXT; v_id BIGINT;
BEGIN
    v_stmt := format('sequence %s resembles %s by cosine (candidate only; %s)',
                     p_query, p_candidate, p_kind);
    -- The probe reports the score and always ok=false: a candidate is not a
    -- finding. Recording ok=true would make the verdict depend on 94 catching
    -- it, and a shield you rely on is a shield you have already misused.
    v_probe := format($q$
        SELECT false AS ok,
               jsonb_build_object(
                   'query', %1$L, 'candidate', %2$L,
                   'cosine', calx.vec_cosine(q.vec, c.vec),
                   'exact_prefix', calx.terms_prefix_agree(q.terms, c.terms),
                   'note', 'cosine proposes; exact terms decide'
               ) AS evidence
          FROM calx.seq_vector q, calx.seq_vector c
         WHERE q.seq_id = %1$L AND c.seq_id = %2$L
           AND q.vector_kind = %3$L AND c.vector_kind = %3$L
    $q$, p_query, p_candidate, p_kind);

    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind,
                            method, probe_sql, domain)
    VALUES ('oeis_candidate',
            jsonb_build_object('query', p_query, 'candidate', p_candidate,
                               'vector_kind', p_kind),
            v_stmt, 'computational', 'comp_sql', v_probe, 'float_heuristic')
    ON CONFLICT (statement) DO UPDATE SET probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_id;
    RETURN v_id;
END $$;

COMMENT ON FUNCTION calx.oeis_candidate_claim(TEXT, TEXT, TEXT) IS
    'Step 1: record that cosine proposed this pairing. Tagged float_heuristic, '
    'so 94 pins it at unverified forever -- carried as provenance, never as '
    'evidence.';

-- ---------------------------------------------------------------------------
-- 2. the finite composition -- the only place a derivation edge is earned
-- ---------------------------------------------------------------------------

-- Joins the exact-prefix claim (92) to a structural certificate (93 or 95) and
-- concludes the bounded statement that follows from both. The conclusion names
-- its own horizon, exactly as 93's claim does, so it can never be quoted as a
-- global identity.
CREATE OR REPLACE FUNCTION calx.oeis_compose_finite(
    p_prefix_claim BIGINT, p_cert_claim BIGINT, p_query TEXT, p_horizon INT
) RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE v_stmt TEXT; v_probe TEXT; v_id BIGINT; v_existing BIGINT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM cert.claim WHERE id = p_prefix_claim) THEN
        RAISE EXCEPTION 'calx.oeis_compose_finite: no prefix claim %', p_prefix_claim;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM cert.claim WHERE id = p_cert_claim) THEN
        RAISE EXCEPTION 'calx.oeis_compose_finite: no certificate claim %', p_cert_claim;
    END IF;

    v_stmt := format(
        'sequence %s is structurally explained over %s terms '
        '[prefix #%s + certificate #%s]',
        p_query, p_horizon, p_prefix_claim, p_cert_claim);

    -- Re-derivable rather than asserted: the probe re-reads both premises'
    -- standing, so the composite cannot outlive them even outside 102.
    v_probe := format($q$
        SELECT bool_and(s.effective_status = 'valid') AS ok,
               jsonb_build_object(
                   'premises', jsonb_agg(jsonb_build_object(
                       'claim_id', s.claim_id, 'status', s.effective_status)),
                   'horizon', %3$s,
                   'note', 'bounded: says nothing about n beyond the horizon'
               ) AS evidence
          FROM cert.standing s WHERE s.claim_id IN (%1$s, %2$s)
    $q$, p_prefix_claim, p_cert_claim, p_horizon);

    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind,
                            method, probe_sql, domain)
    VALUES ('oeis_finite_composition',
            jsonb_build_object('query', p_query, 'horizon', p_horizon,
                               'prefix_claim', p_prefix_claim,
                               'certificate_claim', p_cert_claim),
            v_stmt, 'computational', 'comp_sql', v_probe, 'exact_int')
    ON CONFLICT (statement) DO UPDATE SET probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_id;

    SELECT id INTO v_existing FROM cert.derivation
     WHERE conclusion_id = v_id
       AND premise_ids @> ARRAY[p_prefix_claim, p_cert_claim]
       AND rule = 'oeis_finite_agreement';
    IF v_existing IS NULL THEN
        INSERT INTO cert.derivation (conclusion_id, premise_ids, rule)
        VALUES (v_id, ARRAY[p_prefix_claim, p_cert_claim], 'oeis_finite_agreement');
    END IF;

    RETURN v_id;
END $$;

COMMENT ON FUNCTION calx.oeis_compose_finite(BIGINT, BIGINT, TEXT, INT) IS
    'Step 3->composition: exact prefix + structural certificate |- bounded '
    'agreement over min(N,L) terms. The only derivation edge this layer '
    'records, because it is the only inference that actually holds.';

-- ---------------------------------------------------------------------------
-- 3. the global theorem -- anchored, never derived
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION calx.oeis_anchor_theorem(
    p_statement TEXT, p_query TEXT, p_evidence_claims BIGINT[] DEFAULT '{}',
    p_locator TEXT DEFAULT NULL
) RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE v_id BIGINT;
BEGIN
    -- probe_sql NULL: harness-driven, exactly like every other formal_external
    -- claim (41). There is no in-DB probe for "for all n", and pretending
    -- otherwise is the failure mode this whole file is arranged against.
    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind,
                            method, probe_sql, domain)
    VALUES ('oeis_theorem',
            jsonb_build_object(
                'query', p_query,
                'locator', p_locator,
                -- Recorded as motivation, NOT as premises. There is deliberately
                -- no cert.derivation row pairing these with the statement.
                'finite_evidence', to_jsonb(p_evidence_claims),
                'note', 'finite evidence motivates this claim; it does not support it'),
            p_statement, 'formal', 'formal_external', NULL, 'unspecified')
    ON CONFLICT (statement) DO UPDATE
        SET subject_ref = EXCLUDED.subject_ref
    RETURNING id INTO v_id;
    RETURN v_id;
END $$;

COMMENT ON FUNCTION calx.oeis_anchor_theorem(TEXT, TEXT, BIGINT[], TEXT) IS
    'Step 4: anchor the global statement as formal_external, with the finite '
    'chain recorded in subject_ref as motivation only. No derivation edge -- a '
    'bounded prefix never implies "for all n", so the theorem stays unverified '
    'until an external checker runs it.';

-- ---------------------------------------------------------------------------
-- 4. the driver
-- ---------------------------------------------------------------------------

-- Runs the chain and returns it, one row per step, in order. Every underlying
-- function is idempotent, so re-running an attestation re-uses claims rather
-- than duplicating them.
CREATE OR REPLACE FUNCTION calx.oeis_attest(
    p_query TEXT, p_candidate TEXT,
    p_recurrence_id BIGINT DEFAULT NULL, p_morphism_id BIGINT DEFAULT NULL,
    p_min_prefix INT DEFAULT 8, p_kind TEXT DEFAULT 'logc',
    p_theorem TEXT DEFAULT NULL, p_locator TEXT DEFAULT NULL
) RETURNS TABLE (step INT, phase TEXT, claim_id BIGINT, note TEXT)
LANGUAGE plpgsql AS $$
DECLARE
    v_cand BIGINT; v_prefix BIGINT; v_cert BIGINT;
    v_comp BIGINT; v_thm BIGINT; v_horizon INT;
BEGIN
    IF p_recurrence_id IS NOT NULL AND p_morphism_id IS NOT NULL THEN
        RAISE EXCEPTION 'calx.oeis_attest: pass a recurrence OR a morphism, not both';
    END IF;

    v_cand := calx.oeis_candidate_claim(p_query, p_candidate, p_kind);
    step := 1; phase := 'candidate'; claim_id := v_cand;
    note := 'float_heuristic — can never be valid';
    RETURN NEXT;

    v_prefix := calx.oeis_cosine_match_claim(p_query, p_candidate, p_min_prefix, p_kind);
    step := 2; phase := 'exact_prefix'; claim_id := v_prefix;
    note := format('exact agreement >= %s terms', p_min_prefix);
    RETURN NEXT;

    IF p_recurrence_id IS NOT NULL THEN
        v_cert := cert.recurrence_claim(p_recurrence_id);
        SELECT array_length(terms, 1) INTO v_horizon
          FROM cert.recurrence WHERE id = p_recurrence_id;
        step := 3; phase := 'recurrence'; claim_id := v_cert;
        note := format('regenerates %s terms exactly', v_horizon);
        RETURN NEXT;
    ELSIF p_morphism_id IS NOT NULL THEN
        v_cert := cert.morphism_claim(p_morphism_id);
        SELECT least(array_length(src_terms, 1), array_length(dst_terms, 1))
          INTO v_horizon FROM cert.morphism WHERE id = p_morphism_id;
        step := 3; phase := 'morphism'; claim_id := v_cert;
        note := format('exact map over %s common terms', v_horizon);
        RETURN NEXT;
    END IF;

    IF v_cert IS NOT NULL THEN
        -- The horizon is the weaker of the two finite witnesses. Taking the
        -- larger would claim reach the prefix match never had.
        v_horizon := least(v_horizon, p_min_prefix);
        v_comp := calx.oeis_compose_finite(v_prefix, v_cert, p_query, v_horizon);
        step := 4; phase := 'finite_composition'; claim_id := v_comp;
        note := format('bounded by min(prefix, certificate) = %s terms', v_horizon);
        RETURN NEXT;
    END IF;

    IF p_theorem IS NOT NULL THEN
        v_thm := calx.oeis_anchor_theorem(
            p_theorem, p_query,
            array_remove(ARRAY[v_prefix, v_cert, v_comp], NULL), p_locator);
        step := 5; phase := 'global_theorem'; claim_id := v_thm;
        note := 'formal_external — anchored, NOT derived from the finite chain';
        RETURN NEXT;
    END IF;
END $$;

COMMENT ON FUNCTION calx.oeis_attest(TEXT, TEXT, BIGINT, BIGINT, INT, TEXT, TEXT, TEXT) IS
    'T4 driver: candidate -> exact prefix -> recurrence/morphism certificate -> '
    'bounded composition -> optional anchored theorem. Idempotent; re-running '
    'reuses claims. Composition only -- every verification belongs to 92/93/95.';

-- ---------------------------------------------------------------------------
-- 5. the chain, readable
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW calx.oeis_attestation AS
SELECT c.subject_ref->>'query'          AS query,
       c.subject_kind                   AS phase,
       c.id                             AS claim_id,
       c.domain,
       s.effective_status,
       -- Makes the central rule auditable rather than merely documented: a
       -- global theorem with premises would show support_premises > 0.
       (SELECT count(*) FROM cert.derivation d WHERE d.conclusion_id = c.id)
                                        AS support_premises,
       c.statement
  FROM cert.claim c
  LEFT JOIN cert.standing s ON s.claim_id = c.id
 WHERE c.subject_kind IN ('oeis_candidate', 'oeis_cosine_match',
                          'oeis_finite_composition', 'oeis_theorem');

COMMENT ON VIEW calx.oeis_attestation IS
    'Every claim the T4 workflow mints, with its domain, standing and premise '
    'count. An oeis_theorem row must always show support_premises = 0: the '
    'global statement is anchored, never derived from finite agreement.';
