-- Unified model, step 107: goal-level content hashing.
--
-- THE GAP THIS CLOSES. The derivation DAG (85, 102) is CLAIM-granular: its
-- finest edge is "claim B was used to prove claim A". Everything inside a
-- proof -- the subgoals, the gaps, the lemma that two unrelated proofs both
-- happen to need -- is invisible to it. So the ledger cannot answer three
-- questions it obviously should:
--
--   * has this exact goal already been proved, anywhere, by anyone?
--   * do these two claims secretly rest on the same unproved thing?
--   * is this proof circular -- does its remaining gap restate its target?
--
-- AlphaProof Nexus (arXiv:2605.22763) answers the first with a global goal
-- cache keyed by "a deep hash of the exact formal Lean context and target"
-- (their goal_id), shared across every sketch in the population. This layer
-- is that cache, made durable and queryable, plus the two questions their
-- cache does not answer.
--
-- THE THIRD QUESTION IS THE INTERESTING ONE. Their documented failure mode #1
-- is an agent that "offloaded a problem's core difficulty into a single sorry
-- within a helper lemma that reiterated the target statement in a slightly
-- different form", and they report that "explicitly prompting against this
-- behavior failed to prevent it". Of course it did -- it is a property of the
-- artifact, not of the intent, and you cannot ask a model not to have a
-- property. With goals content-hashed it is a string comparison: if an open
-- gap hashes to the claim's own target, the proof proves nothing. That is
-- cert.goal_noncircular below, and it is a REFUTATION, not a warning.
--
-- WHAT A HASH IS OVER. The digest covers the normalised hypothesis context
-- and the target, and nothing else -- not the proof, not the file, not the
-- declaration name. Two goals with the same digest are interchangeable
-- obligations. Normalisation is the whole risk: the recipe lives in
-- calx.goalhash (Python) so that every producer agrees, and digest_kind
-- records which recipe ran, because a syntactic normalisation and an
-- elaborated one must never compare equal by accident.
--
-- Honest limits, stated once. A syntactic digest is sound for EQUALITY
-- (equal digests mean the same normalised text) and incomplete for
-- IDENTITY (two goals that differ only up to definitional unfolding or
-- alpha-renaming of binders past the normaliser will hash apart). So a
-- circularity hit is real; a circularity miss proves nothing. The same
-- asymmetry governs the cache: a hit is reusable, a miss just means try.
-- Idempotent; additive only.

INSERT INTO cert.method (name, claim_kind, checker_kind, description)
VALUES ('comp_sql', 'computational', 'sql', 'in-DB probe returning (ok, evidence)')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 1. The goal registry
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS cert.goal (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    goal_sha256   TEXT NOT NULL CHECK (goal_sha256 ~ '^[0-9a-f]{64}$'),
    digest_kind   TEXT NOT NULL DEFAULT 'syntactic'
                  CHECK (digest_kind IN ('syntactic', 'elaborated')),
    -- Kept for diagnosis: a cache hit you cannot read is a cache hit you
    -- cannot trust.
    context_text  TEXT NOT NULL DEFAULT '',
    target_text   TEXT NOT NULL,
    toolchain     TEXT NOT NULL DEFAULT '',
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (goal_sha256, digest_kind)
);

DROP TRIGGER IF EXISTS cert_goal_append_only ON cert.goal;
CREATE TRIGGER cert_goal_append_only BEFORE UPDATE OR DELETE ON cert.goal
    FOR EACH ROW EXECUTE FUNCTION cert.reject_mutation();

-- Where a goal shows up, and how it fared there. One goal, many occurrences:
-- that multiplicity IS the shared-subgoal structure.
CREATE TABLE IF NOT EXISTS cert.goal_occurrence (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    goal_id     BIGINT NOT NULL REFERENCES cert.goal(id) ON DELETE CASCADE,
    claim_id    BIGINT NOT NULL REFERENCES cert.claim(id) ON DELETE CASCADE,
    -- target  : the thing the claim is about
    -- subgoal : an intermediate obligation inside the proof
    -- gap     : an OPEN obligation (a `sorry`) the proof does not discharge
    role        TEXT NOT NULL CHECK (role IN ('target', 'subgoal', 'gap')),
    -- Their rater rubric distinguishes "good gaps (routine/technical)" from
    -- "bad gaps (miracles/strategic)". Recorded, never inferred: the ledger
    -- has no business judging which is which, but it must be able to report
    -- what the producer declared.
    gap_kind    TEXT CHECK (gap_kind IN ('routine', 'strategic', 'unclassified')),
    status      TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ('proved', 'disproved', 'open')),
    decl_name   TEXT NOT NULL DEFAULT '',
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    recorded_by TEXT NOT NULL DEFAULT cert.signer_identity(),
    UNIQUE (goal_id, claim_id, role, decl_name)
);

CREATE INDEX IF NOT EXISTS goal_occurrence_goal_idx ON cert.goal_occurrence (goal_id);
CREATE INDEX IF NOT EXISTS goal_occurrence_claim_idx ON cert.goal_occurrence (claim_id, role);

CREATE OR REPLACE FUNCTION cert.register_goal(
    p_goal_sha256 TEXT,
    p_target_text TEXT,
    p_context_text TEXT DEFAULT '',
    p_digest_kind TEXT DEFAULT 'syntactic',
    p_toolchain   TEXT DEFAULT ''
) RETURNS cert.goal
LANGUAGE plpgsql AS $$
DECLARE v_row cert.goal%ROWTYPE;
BEGIN
    INSERT INTO cert.goal (goal_sha256, digest_kind, context_text, target_text, toolchain)
    VALUES (p_goal_sha256, p_digest_kind, p_context_text, p_target_text, p_toolchain)
    ON CONFLICT (goal_sha256, digest_kind) DO NOTHING
    RETURNING * INTO v_row;
    IF v_row.id IS NULL THEN
        SELECT * INTO v_row FROM cert.goal
         WHERE goal_sha256 = p_goal_sha256 AND digest_kind = p_digest_kind;
    END IF;
    RETURN v_row;
END
$$;

CREATE OR REPLACE FUNCTION cert.record_goal_occurrence(
    p_goal_id   BIGINT,
    p_claim_id  BIGINT,
    p_role      TEXT,
    p_status    TEXT DEFAULT 'open',
    p_decl_name TEXT DEFAULT '',
    p_gap_kind  TEXT DEFAULT NULL
) RETURNS cert.goal_occurrence
LANGUAGE plpgsql AS $$
DECLARE v_row cert.goal_occurrence%ROWTYPE;
BEGIN
    IF p_role = 'gap' AND p_status = 'proved' THEN
        RAISE EXCEPTION 'a gap is by definition not proved (goal %, claim %)',
            p_goal_id, p_claim_id;
    END IF;
    INSERT INTO cert.goal_occurrence
        (goal_id, claim_id, role, status, decl_name, gap_kind)
    VALUES (p_goal_id, p_claim_id, p_role, p_status, p_decl_name,
            CASE WHEN p_role = 'gap' THEN COALESCE(p_gap_kind, 'unclassified')
                 ELSE p_gap_kind END)
    ON CONFLICT (goal_id, claim_id, role, decl_name)
        DO UPDATE SET status = EXCLUDED.status,
                      gap_kind = COALESCE(EXCLUDED.gap_kind, cert.goal_occurrence.gap_kind)
    RETURNING * INTO v_row;
    RETURN v_row;
END
$$;

-- ---------------------------------------------------------------------------
-- 2. The cache: has this goal been settled anywhere?
-- ---------------------------------------------------------------------------

-- Their global goal cache, as a query. Answers with the SETTLING claim, not
-- just a boolean, so a consumer can check that claim's standing before
-- reusing the result -- which is the part a filesystem cache cannot do. A
-- goal proved by a claim that has since been revoked is not a cache hit, and
-- effective_status is what says so.
CREATE OR REPLACE FUNCTION cert.goal_resolved(
    p_goal_sha256 TEXT, p_digest_kind TEXT DEFAULT 'syntactic'
)
RETURNS TABLE (
    settled       BOOLEAN,
    resolution    TEXT,
    claim_id      BIGINT,
    claim_status  TEXT,
    decl_name     TEXT,
    occurrences   INTEGER
)
LANGUAGE sql STABLE AS $$
    WITH g AS (
        SELECT id FROM cert.goal
         WHERE goal_sha256 = p_goal_sha256 AND digest_kind = p_digest_kind
    ),
    occ AS (
        SELECT o.*, s.effective_status
          FROM cert.goal_occurrence o
          JOIN g ON g.id = o.goal_id
          LEFT JOIN cert.standing s ON s.claim_id = o.claim_id
    )
    SELECT
        EXISTS (SELECT 1 FROM occ
                 WHERE status IN ('proved', 'disproved')
                   AND COALESCE(effective_status, 'unchecked') = 'valid'),
        (SELECT status FROM occ
          WHERE status IN ('proved', 'disproved')
            AND COALESCE(effective_status, 'unchecked') = 'valid'
          ORDER BY recorded_at LIMIT 1),
        (SELECT claim_id FROM occ
          WHERE status IN ('proved', 'disproved')
            AND COALESCE(effective_status, 'unchecked') = 'valid'
          ORDER BY recorded_at LIMIT 1),
        (SELECT effective_status FROM occ
          WHERE status IN ('proved', 'disproved')
            AND COALESCE(effective_status, 'unchecked') = 'valid'
          ORDER BY recorded_at LIMIT 1),
        (SELECT decl_name FROM occ
          WHERE status IN ('proved', 'disproved')
            AND COALESCE(effective_status, 'unchecked') = 'valid'
          ORDER BY recorded_at LIMIT 1),
        (SELECT COUNT(*)::INTEGER FROM occ);
$$;

-- Goals that more than one claim depends on. This is structure the
-- claim-level DAG cannot see: two claims with no derivation edge between them
-- can still stand or fall together because they share an unproved obligation.
CREATE OR REPLACE VIEW cert.shared_goals AS
SELECT g.id            AS goal_id,
       g.goal_sha256,
       left(g.target_text, 120) AS target_excerpt,
       COUNT(DISTINCT o.claim_id)::INTEGER AS n_claims,
       array_agg(DISTINCT o.claim_id ORDER BY o.claim_id) AS claim_ids,
       bool_or(o.status = 'proved')  AS proved_somewhere,
       bool_or(o.role = 'gap' AND o.status = 'open') AS open_somewhere
  FROM cert.goal g
  JOIN cert.goal_occurrence o ON o.goal_id = g.id
 GROUP BY g.id, g.goal_sha256, g.target_text
HAVING COUNT(DISTINCT o.claim_id) > 1;

-- ---------------------------------------------------------------------------
-- 3. Circularity: the gap that restates the target
-- ---------------------------------------------------------------------------

-- ok = false  an open gap hashes to the claim's own target: the proof
--             assumes what it set out to prove
-- ok = true   no gap coincides with the target
-- ok = NULL   the claim has no recorded target goal, so there is nothing to
--             compare against -- unverified, never valid
CREATE OR REPLACE FUNCTION cert.goal_noncircular(p_claim_id BIGINT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_targets  BIGINT[];
    v_hits     JSONB;
    v_n        INTEGER;
    v_gaps     INTEGER;
BEGIN
    SELECT COALESCE(array_agg(goal_id), '{}') INTO v_targets
      FROM cert.goal_occurrence
     WHERE claim_id = p_claim_id AND role = 'target';

    IF cardinality(v_targets) = 0 THEN
        ok := NULL;
        evidence := jsonb_build_object(
            'reason', 'no target goal recorded for this claim -- nothing to '
                      'compare gaps against',
            'claim_id', p_claim_id);
        RETURN NEXT; RETURN;
    END IF;

    SELECT COUNT(*) INTO v_gaps
      FROM cert.goal_occurrence
     WHERE claim_id = p_claim_id AND role = 'gap' AND status = 'open';

    SELECT COALESCE(jsonb_agg(jsonb_build_object(
               'gap_occurrence_id', o.id, 'goal_id', o.goal_id,
               'goal_sha256', g.goal_sha256, 'decl', o.decl_name,
               'gap_kind', o.gap_kind,
               'target_text', left(g.target_text, 400))), '[]'::jsonb),
           COUNT(*)
      INTO v_hits, v_n
      FROM cert.goal_occurrence o
      JOIN cert.goal g ON g.id = o.goal_id
     WHERE o.claim_id = p_claim_id
       AND o.role = 'gap' AND o.status = 'open'
       AND o.goal_id = ANY(v_targets);

    IF v_n > 0 THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', 'CIRCULAR: an open gap has the same goal digest as the '
                      'claim''s own target -- the proof offloads the target '
                      'onto itself',
            'claim_id', p_claim_id, 'circular_gaps', v_hits,
            'open_gaps_total', v_gaps);
        RETURN NEXT; RETURN;
    END IF;

    ok := true;
    evidence := jsonb_build_object(
        'claim_id', p_claim_id, 'targets', cardinality(v_targets),
        'open_gaps', v_gaps, 'circular', false,
        'note', 'syntactic digests: equality is sound, absence of a hit is not '
                'a proof of non-circularity');
    RETURN NEXT;
END
$$;

CREATE OR REPLACE FUNCTION cert.goal_noncircular_claim(p_claim_id BIGINT)
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE v_stmt TEXT; v_probe TEXT; v_id BIGINT;
BEGIN
    v_stmt := format('no open gap of claim #%s restates its own target', p_claim_id);
    v_probe := format('SELECT ok, evidence FROM cert.goal_noncircular(%s)', p_claim_id);
    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
    VALUES ('goal', jsonb_build_object('claim_id', p_claim_id),
            v_stmt, 'computational', 'comp_sql', v_probe)
    ON CONFLICT (statement) DO UPDATE SET probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

COMMENT ON TABLE cert.goal IS
    'Content-addressed proof obligations: digest over the normalised context '
    'and target only. Makes the derivation DAG subgoal-granular, gives the '
    'ledger a durable global goal cache (cert.goal_resolved, standing-aware), '
    'and turns "the gap restates the target" from a prompt into a string '
    'comparison (cert.goal_noncircular).';
