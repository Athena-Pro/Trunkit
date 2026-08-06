-- Unified model, step 109: the attempt ledger -- what was tried, what it cost,
-- and how it died.
--
-- THE GAP THIS CLOSES. Every table above this one records SUCCESS. A claim
-- exists because something was proved; a certificate exists because something
-- was checked. Nothing in the schema can say "we spent four hours and $180 on
-- Erdos #152 with Gemini 3.1 Pro and it ran out of episodes." At the solve
-- rates this field actually operates at -- 9/353 Erdos problems and 44/492
-- OEIS conjectures in arXiv:2605.22763 -- that means the ledger records 2.5%
-- of the work and discards the rest.
--
-- The discarded 97.5% is not waste, it is the expensive part, and it answers
-- the question asked most often in practice: HAS ANYONE ALREADY TRIED THIS?
-- Both systems surveyed reach for this independently -- AlphaProof Nexus
-- keeps a population database of incomplete sketches, Danus keeps a global
-- memory of dead ends and counterexamples -- and in both it is per-run and
-- dies with the process.
--
-- WHY IT BELONGS IN THE LEDGER AND NOT IN A LOG FILE. Three reasons, in
-- increasing order of importance. It is queryable before you start work. It
-- carries cost, so the Pareto analysis that paper does by hand becomes a
-- view. And an attempt that FAILED still learned things: subgoals proved on
-- the way, subgoals disproved, counterexamples found. Attaching those to the
-- goal registry (107) means a failed run permanently improves the cache --
-- the single highest-value thing a failure can do, and impossible if the
-- failure was never written down.
--
-- WHAT AN ATTEMPT IS NOT. It is not a claim and it is never evidence. No
-- probe reads this table to decide whether anything is true; `outcome` is
-- reported by whoever ran the thing and is trusted exactly as far as they
-- are. It records effort, not truth. The moment an attempt produces something
-- checkable it gets a claim_id, and the claim is judged on its own by the
-- machinery above. Keeping that line sharp is what stops an attempt ledger
-- from decaying into a second, softer notion of verification.
--
-- Idempotent; additive only.

-- ---------------------------------------------------------------------------
-- 1. Attempts
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS cert.attempt (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Stable identifier for the PROBLEM, not the claim: 'erdos:125',
    -- 'oeis:A177384', 'stacks:04KM'. Deliberately a string with a convention
    -- rather than a foreign key, because the whole point is to record work on
    -- things the ledger does not have claims for yet.
    subject_key  TEXT NOT NULL,
    -- Free-form grouping for one campaign, so a sweep over 353 problems can be
    -- summarised as a unit.
    run_label    TEXT NOT NULL DEFAULT '',
    agent        TEXT NOT NULL DEFAULT '',
    model        TEXT NOT NULL DEFAULT '',
    outcome      TEXT NOT NULL CHECK (outcome IN
                     ('proved',      -- produced a checkable proof
                      'disproved',   -- produced a refutation
                      'partial',     -- real progress, gaps remain
                      'exhausted',   -- ran out of budget with nothing
                      'error',       -- infrastructure failed, says nothing about the problem
                      'abandoned')), -- stopped by a human
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at     TIMESTAMPTZ,
    wall_clock_s NUMERIC CHECK (wall_clock_s IS NULL OR wall_clock_s >= 0),
    cost_usd     NUMERIC CHECK (cost_usd IS NULL OR cost_usd >= 0),
    episodes     INTEGER CHECK (episodes IS NULL OR episodes >= 0),
    -- Set when the attempt produced something the ledger can judge.
    claim_id     BIGINT REFERENCES cert.claim(id),
    -- Why it died, what was learned, which references it leaned on. Read by
    -- people, never by probes.
    notes        JSONB NOT NULL DEFAULT '{}'::jsonb,
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    recorded_by  TEXT NOT NULL DEFAULT cert.signer_identity()
);

CREATE INDEX IF NOT EXISTS attempt_subject_idx ON cert.attempt (subject_key, started_at DESC);
CREATE INDEX IF NOT EXISTS attempt_run_idx     ON cert.attempt (run_label);
CREATE INDEX IF NOT EXISTS attempt_outcome_idx ON cert.attempt (outcome);

-- Append-only. An attempt is a historical event; revising it is how a log
-- becomes a story. Corrections are new rows.
DROP TRIGGER IF EXISTS cert_attempt_append_only ON cert.attempt;
CREATE TRIGGER cert_attempt_append_only BEFORE UPDATE OR DELETE ON cert.attempt
    FOR EACH ROW EXECUTE FUNCTION cert.reject_mutation();

-- What a failed run learned. This is the bridge that makes failure pay: a
-- subgoal settled inside an attempt that never reached its target is still a
-- cache entry for every future attempt on any problem.
CREATE TABLE IF NOT EXISTS cert.attempt_goal (
    attempt_id  BIGINT NOT NULL REFERENCES cert.attempt(id) ON DELETE CASCADE,
    goal_id     BIGINT NOT NULL REFERENCES cert.goal(id) ON DELETE CASCADE,
    status      TEXT NOT NULL CHECK (status IN ('proved', 'disproved', 'open')),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (attempt_id, goal_id)
);

CREATE OR REPLACE FUNCTION cert.record_attempt(
    p_subject_key  TEXT,
    p_outcome      TEXT,
    p_agent        TEXT DEFAULT '',
    p_model        TEXT DEFAULT '',
    p_run_label    TEXT DEFAULT '',
    p_cost_usd     NUMERIC DEFAULT NULL,
    p_wall_clock_s NUMERIC DEFAULT NULL,
    p_episodes     INTEGER DEFAULT NULL,
    p_claim_id     BIGINT DEFAULT NULL,
    p_notes        JSONB DEFAULT '{}'::jsonb,
    p_started_at   TIMESTAMPTZ DEFAULT NULL
) RETURNS cert.attempt
LANGUAGE plpgsql AS $$
DECLARE v_row cert.attempt%ROWTYPE;
BEGIN
    IF p_outcome IN ('proved', 'disproved') AND p_claim_id IS NULL THEN
        -- Not fatal, but worth saying out loud: an attempt that claims to have
        -- proved something without producing a claim has produced nothing the
        -- ledger can check.
        RAISE NOTICE 'attempt on % reports % with no claim_id -- nothing checkable was recorded',
            p_subject_key, p_outcome;
    END IF;
    INSERT INTO cert.attempt
        (subject_key, run_label, agent, model, outcome, started_at, ended_at,
         wall_clock_s, cost_usd, episodes, claim_id, notes)
    VALUES (p_subject_key, p_run_label, p_agent, p_model, p_outcome,
            COALESCE(p_started_at, now()), now(),
            p_wall_clock_s, p_cost_usd, p_episodes, p_claim_id, p_notes)
    RETURNING * INTO v_row;
    RETURN v_row;
END
$$;

CREATE OR REPLACE FUNCTION cert.attempt_learned(
    p_attempt_id BIGINT, p_goal_id BIGINT, p_status TEXT
) RETURNS cert.attempt_goal
LANGUAGE plpgsql AS $$
DECLARE v_row cert.attempt_goal%ROWTYPE;
BEGIN
    INSERT INTO cert.attempt_goal (attempt_id, goal_id, status)
    VALUES (p_attempt_id, p_goal_id, p_status)
    ON CONFLICT (attempt_id, goal_id) DO UPDATE SET status = EXCLUDED.status
    RETURNING * INTO v_row;
    RETURN v_row;
END
$$;

-- ---------------------------------------------------------------------------
-- 2. The query you run before starting work
-- ---------------------------------------------------------------------------

-- "Has anyone tried this, and how did it go?" Everything needed to decide
-- whether to spend the afternoon, in one row.
CREATE OR REPLACE FUNCTION cert.attempt_history(p_subject_key TEXT)
RETURNS TABLE (
    subject_key   TEXT,
    attempts      INTEGER,
    best_outcome  TEXT,
    last_attempt  TIMESTAMPTZ,
    total_cost_usd NUMERIC,
    total_wall_s  NUMERIC,
    agents        TEXT[],
    models        TEXT[],
    outcomes      JSONB,
    settled_claim BIGINT,
    goals_learned INTEGER
)
LANGUAGE sql STABLE AS $$
    WITH a AS (SELECT * FROM cert.attempt WHERE subject_key = p_subject_key)
    SELECT p_subject_key,
           (SELECT COUNT(*)::INTEGER FROM a),
           -- Ordered by how much the outcome tells you, best first.
           (SELECT outcome FROM a ORDER BY CASE outcome
                WHEN 'proved' THEN 1 WHEN 'disproved' THEN 2 WHEN 'partial' THEN 3
                WHEN 'exhausted' THEN 4 WHEN 'abandoned' THEN 5 ELSE 6 END,
                started_at LIMIT 1),
           (SELECT MAX(started_at) FROM a),
           (SELECT SUM(cost_usd) FROM a),
           (SELECT SUM(wall_clock_s) FROM a),
           (SELECT COALESCE(array_agg(DISTINCT agent) FILTER (WHERE agent <> ''), '{}') FROM a),
           (SELECT COALESCE(array_agg(DISTINCT model) FILTER (WHERE model <> ''), '{}') FROM a),
           (SELECT COALESCE(jsonb_object_agg(outcome, n), '{}'::jsonb)
              FROM (SELECT outcome, COUNT(*) AS n FROM a GROUP BY outcome) t),
           (SELECT claim_id FROM a WHERE claim_id IS NOT NULL
             ORDER BY CASE outcome WHEN 'proved' THEN 1 WHEN 'disproved' THEN 2
                                   ELSE 3 END, started_at LIMIT 1),
           (SELECT COUNT(DISTINCT ag.goal_id)::INTEGER
              FROM cert.attempt_goal ag JOIN a ON a.id = ag.attempt_id);
$$;

-- Cheap gate for a harness: skip, or go.
CREATE OR REPLACE FUNCTION cert.attempted_before(p_subject_key TEXT)
RETURNS BOOLEAN
LANGUAGE sql STABLE AS $$
    SELECT EXISTS (SELECT 1 FROM cert.attempt WHERE subject_key = p_subject_key);
$$;

-- Spend against result, per problem. The cost side of the Pareto picture
-- arXiv:2605.22763 draws by hand -- with the difference that unsolved
-- problems appear here too, which is where the money actually went.
CREATE OR REPLACE VIEW cert.attempt_spend AS
SELECT subject_key,
       COUNT(*)::INTEGER                                   AS attempts,
       SUM(cost_usd)                                       AS cost_usd,
       SUM(wall_clock_s)                                   AS wall_clock_s,
       bool_or(outcome IN ('proved', 'disproved'))         AS settled,
       COUNT(*) FILTER (WHERE outcome = 'exhausted')::INTEGER AS exhausted,
       COUNT(*) FILTER (WHERE outcome = 'error')::INTEGER     AS errored,
       MAX(started_at)                                     AS last_attempt
  FROM cert.attempt
 GROUP BY subject_key
 ORDER BY settled, cost_usd DESC NULLS LAST;

-- Per-campaign rollup, including the solve rate that makes the denominator
-- honest.
CREATE OR REPLACE VIEW cert.run_summary AS
SELECT run_label,
       COUNT(*)::INTEGER                                     AS attempts,
       COUNT(DISTINCT subject_key)::INTEGER                  AS subjects,
       COUNT(*) FILTER (WHERE outcome IN ('proved','disproved'))::INTEGER AS settled,
       ROUND(COUNT(*) FILTER (WHERE outcome IN ('proved','disproved'))::NUMERIC
             / NULLIF(COUNT(DISTINCT subject_key), 0), 4)    AS settle_rate,
       SUM(cost_usd)                                         AS cost_usd,
       ROUND(SUM(cost_usd) / NULLIF(
             COUNT(*) FILTER (WHERE outcome IN ('proved','disproved')), 0), 2)
                                                             AS cost_per_settled,
       MIN(started_at)                                       AS started,
       MAX(COALESCE(ended_at, started_at))                   AS ended
  FROM cert.attempt
 WHERE run_label <> ''
 GROUP BY run_label;

COMMENT ON TABLE cert.attempt IS
    'Effort ledger: what was tried, what it cost, how it died. Records effort, '
    'never truth -- no probe reads it as evidence, and an attempt claiming a '
    'proof without a claim_id has produced nothing checkable. Its payoff is '
    'cert.attempt_history (do not re-run this) and cert.attempt_goal (a failed '
    'run still fills the goal cache).';
