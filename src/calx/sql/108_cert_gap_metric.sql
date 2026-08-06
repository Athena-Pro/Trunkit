-- Unified model, step 108: load-bearing gaps and support ratio.
--
-- THE GAP THIS CLOSES. Once goals are content-hashed (107), the ledger knows
-- where the holes are. It still does not know which holes MATTER. Two open
-- gaps are not equal: one sits under a leaf nobody cites, the other sits under
-- forty downstream claims. The rater rubric in arXiv:2605.22763 makes exactly
-- this distinction -- "good gaps (routine/technical)" versus "bad gaps
-- (miracles/strategic)", with "it is unacceptable to sorry the core insight"
-- -- and delegates it to an LLM judge scoring prose.
--
-- It does not have to be a judgement. Once the derivation DAG is in place
-- (85, 102), how much rests on a gap is a graph query. This layer computes it.
--
-- TWO MEASURES, ANSWERING DIFFERENT QUESTIONS.
--
--   blast radius -- how many claims transitively DEPEND on the claim carrying
--   the gap. This is the cost of the hole turning out to be unfillable. It is
--   the downward closure of cert.derivation_premise, i.e. the same walk
--   cert.tainted_closure (102) does, but seeded by an open gap rather than by
--   a revoked certificate: a gap is a taint that has not happened yet.
--
--   support ratio -- of everything recorded, how much actually holds up the
--   target. Danus (arXiv:2607.06447) reports fact graphs of 3,157 verified
--   facts of which 664 support the theorem, 784 of which 77 do, 100 of which
--   37 do. Between 63% and 90% of what those runs verified is scaffolding the
--   final proof never cites. Any ledger that reports "N verified claims" as a
--   headline is quoting the wrong N; this function reports both.
--
-- WHY THIS IS NOT A SCORE. No weighting, no single number, no threshold. A
-- gap with a large blast radius under a claim nobody has cited in a year is
-- not obviously worse than a small one under active work, and the ledger has
-- no basis for that comparison. It reports the graph facts and lets the
-- reader rank. The one place it does render a verdict is the probe below,
-- and only for a condition that is unambiguous: a claim standing valid while
-- carrying an open gap that other claims depend on.
--
-- Idempotent; additive only. Read-only over 85/102/107.

INSERT INTO cert.method (name, claim_kind, checker_kind, description)
VALUES ('comp_sql', 'computational', 'sql', 'in-DB probe returning (ok, evidence)')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 1. Closure walks
-- ---------------------------------------------------------------------------

-- Everything the claim rests on, transitively. Cycle-safe via the visited
-- path, same discipline as cert.derivation_valid_deep (102): a cycle is
-- reported, never followed.
CREATE OR REPLACE FUNCTION cert.support_closure(p_claim_id BIGINT)
RETURNS TABLE (claim_id BIGINT, depth INTEGER, path BIGINT[])
LANGUAGE sql STABLE AS $$
    WITH RECURSIVE up AS (
        SELECT dp.premise_id AS claim_id, 1 AS depth,
               ARRAY[p_claim_id, dp.premise_id] AS path
          FROM cert.derivation_premise dp
         WHERE dp.conclusion_id = p_claim_id
        UNION ALL
        SELECT dp.premise_id, u.depth + 1, u.path || dp.premise_id
          FROM up u
          JOIN cert.derivation_premise dp ON dp.conclusion_id = u.claim_id
         WHERE NOT dp.premise_id = ANY(u.path)
           AND u.depth < 256
    )
    SELECT DISTINCT ON (claim_id) claim_id, depth, path
      FROM up ORDER BY claim_id, depth;
$$;

-- Everything that rests on the claim, transitively -- the blast radius set.
CREATE OR REPLACE FUNCTION cert.dependent_closure(p_claim_id BIGINT)
RETURNS TABLE (claim_id BIGINT, depth INTEGER, path BIGINT[])
LANGUAGE sql STABLE AS $$
    WITH RECURSIVE down AS (
        SELECT dp.conclusion_id AS claim_id, 1 AS depth,
               ARRAY[p_claim_id, dp.conclusion_id] AS path
          FROM cert.derivation_premise dp
         WHERE dp.premise_id = p_claim_id
        UNION ALL
        SELECT dp.conclusion_id, d.depth + 1, d.path || dp.conclusion_id
          FROM down d
          JOIN cert.derivation_premise dp ON dp.premise_id = d.claim_id
         WHERE NOT dp.conclusion_id = ANY(d.path)
           AND d.depth < 256
    )
    SELECT DISTINCT ON (claim_id) claim_id, depth, path
      FROM down ORDER BY claim_id, depth;
$$;

-- ---------------------------------------------------------------------------
-- 2. Gap impact
-- ---------------------------------------------------------------------------

-- One row per open gap on the claim, with what rests on it.
--   blast_radius   distinct claims transitively depending on the carrier
--   max_depth      how far the dependency reaches
--   shared_with    other claims recording the SAME goal (107) -- a gap can be
--                  load-bearing for claims that never cite the carrier at all,
--                  which the derivation DAG alone cannot show
--   load_bearing   true when anything at all rests on it, by either route
CREATE OR REPLACE FUNCTION cert.gap_impact(p_claim_id BIGINT)
RETURNS TABLE (
    gap_occurrence_id BIGINT,
    goal_id           BIGINT,
    goal_sha256       TEXT,
    gap_kind          TEXT,
    decl_name         TEXT,
    target_excerpt    TEXT,
    blast_radius      INTEGER,
    max_depth         INTEGER,
    shared_with       INTEGER,
    load_bearing      BOOLEAN
)
LANGUAGE sql STABLE AS $$
    WITH dep AS (
        SELECT COUNT(*)::INTEGER AS n, COALESCE(MAX(depth), 0)::INTEGER AS d
          FROM cert.dependent_closure(p_claim_id)
    )
    SELECT o.id, o.goal_id, g.goal_sha256, o.gap_kind, o.decl_name,
           left(g.target_text, 120),
           dep.n, dep.d,
           (SELECT COUNT(DISTINCT x.claim_id)::INTEGER
              FROM cert.goal_occurrence x
             WHERE x.goal_id = o.goal_id AND x.claim_id <> p_claim_id),
           (dep.n > 0
            OR EXISTS (SELECT 1 FROM cert.goal_occurrence x
                        WHERE x.goal_id = o.goal_id AND x.claim_id <> p_claim_id))
      FROM cert.goal_occurrence o
      JOIN cert.goal g ON g.id = o.goal_id
      CROSS JOIN dep
     WHERE o.claim_id = p_claim_id AND o.role = 'gap' AND o.status = 'open'
     ORDER BY dep.n DESC, o.id;
$$;

-- What fraction of the recorded work holds the target up.
--   supporting  size of the transitive premise closure
--   universe    claims in the same domain (the pool the run drew from)
-- Reported as a pair, never as a headline count on its own.
CREATE OR REPLACE FUNCTION cert.support_ratio(p_claim_id BIGINT)
RETURNS TABLE (
    supporting  INTEGER,
    max_depth   INTEGER,
    universe    INTEGER,
    ratio       NUMERIC,
    domain      TEXT
)
LANGUAGE sql STABLE AS $$
    WITH d AS (SELECT c.domain FROM cert.claim c WHERE c.id = p_claim_id),
    sup AS (
        SELECT COUNT(*)::INTEGER AS n, COALESCE(MAX(depth), 0)::INTEGER AS depth
          FROM cert.support_closure(p_claim_id)
    ),
    uni AS (
        SELECT COUNT(*)::INTEGER AS n FROM cert.claim c, d WHERE c.domain = d.domain
    )
    SELECT sup.n, sup.depth, uni.n,
           CASE WHEN uni.n > 0
                THEN ROUND(sup.n::NUMERIC / uni.n, 4) ELSE NULL END,
           d.domain
      FROM sup, uni, d;
$$;

-- ---------------------------------------------------------------------------
-- 3. The probe worth failing on
-- ---------------------------------------------------------------------------

-- A claim that STANDS VALID while carrying an open gap that other claims rest
-- on is the one unambiguous defect in this space: the ledger is asserting
-- something is settled while advertising, in its own tables, that it is not.
--
-- ok = false  standing valid with a load-bearing open gap
-- ok = true   no load-bearing open gaps
-- ok = NULL   open gaps exist but nothing rests on them yet, or no goals
--             recorded at all -- honest silence, not approval
CREATE OR REPLACE FUNCTION cert.no_load_bearing_gaps(p_claim_id BIGINT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_gaps    INTEGER;
    v_bearing JSONB;
    v_n       INTEGER;
    v_standing TEXT;
    v_goals   INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_goals
      FROM cert.goal_occurrence WHERE claim_id = p_claim_id;
    IF v_goals = 0 THEN
        ok := NULL;
        evidence := jsonb_build_object(
            'reason', 'no goals recorded for this claim -- gap structure unknown',
            'claim_id', p_claim_id);
        RETURN NEXT; RETURN;
    END IF;

    SELECT COUNT(*) INTO v_gaps
      FROM cert.goal_occurrence
     WHERE claim_id = p_claim_id AND role = 'gap' AND status = 'open';

    SELECT COALESCE(jsonb_agg(jsonb_build_object(
               'gap_occurrence_id', gap_occurrence_id, 'goal_sha256', goal_sha256,
               'gap_kind', gap_kind, 'decl', decl_name,
               'blast_radius', blast_radius, 'shared_with', shared_with,
               'target', target_excerpt)), '[]'::jsonb),
           COUNT(*)
      INTO v_bearing, v_n
      FROM cert.gap_impact(p_claim_id) WHERE load_bearing;

    SELECT effective_status INTO v_standing
      FROM cert.standing WHERE claim_id = p_claim_id;

    IF v_n > 0 AND v_standing = 'valid' THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', 'claim stands valid while carrying open gaps that other '
                      'claims rest on',
            'claim_id', p_claim_id, 'standing', v_standing,
            'load_bearing_gaps', v_bearing, 'open_gaps_total', v_gaps);
        RETURN NEXT; RETURN;
    END IF;

    IF v_n > 0 THEN
        ok := NULL;
        evidence := jsonb_build_object(
            'reason', 'load-bearing open gaps, but the claim does not stand '
                      'valid -- consistent, and still worth reading',
            'claim_id', p_claim_id,
            'standing', COALESCE(v_standing, 'unchecked'),
            'load_bearing_gaps', v_bearing, 'open_gaps_total', v_gaps);
        RETURN NEXT; RETURN;
    END IF;

    ok := true;
    evidence := jsonb_build_object(
        'claim_id', p_claim_id, 'open_gaps', v_gaps,
        'load_bearing', 0, 'standing', COALESCE(v_standing, 'unchecked'));
    RETURN NEXT;
END
$$;

CREATE OR REPLACE FUNCTION cert.gap_metric_claim(p_claim_id BIGINT)
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE v_stmt TEXT; v_probe TEXT; v_id BIGINT;
BEGIN
    v_stmt := format('claim #%s carries no load-bearing open gaps', p_claim_id);
    v_probe := format('SELECT ok, evidence FROM cert.no_load_bearing_gaps(%s)', p_claim_id);
    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
    VALUES ('goal', jsonb_build_object('claim_id', p_claim_id),
            v_stmt, 'computational', 'comp_sql', v_probe)
    ON CONFLICT (statement) DO UPDATE SET probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

-- Every open gap in the ledger, worst first. The board a person actually
-- reads: not "how many holes" but "which hole is holding up the most".
CREATE OR REPLACE VIEW cert.gap_board AS
SELECT o.claim_id,
       left(c.statement, 100) AS claim_excerpt,
       s.effective_status,
       o.id AS gap_occurrence_id,
       o.gap_kind,
       o.decl_name,
       left(g.target_text, 100) AS gap_excerpt,
       (SELECT COUNT(*)::INTEGER FROM cert.dependent_closure(o.claim_id)) AS blast_radius,
       (SELECT COUNT(DISTINCT x.claim_id)::INTEGER FROM cert.goal_occurrence x
         WHERE x.goal_id = o.goal_id AND x.claim_id <> o.claim_id) AS shared_with
  FROM cert.goal_occurrence o
  JOIN cert.goal g  ON g.id = o.goal_id
  JOIN cert.claim c ON c.id = o.claim_id
  LEFT JOIN cert.standing s ON s.claim_id = o.claim_id
 WHERE o.role = 'gap' AND o.status = 'open'
 ORDER BY blast_radius DESC, shared_with DESC, o.claim_id;

COMMENT ON FUNCTION cert.gap_impact(BIGINT) IS
    'Per open gap: blast radius (transitive dependents of the carrier), how '
    'many other claims record the same goal, and whether anything rests on it '
    'at all. Computes what arXiv:2605.22763 has an LLM rater judge -- good '
    'gaps versus gaps that hide the core insight.';
