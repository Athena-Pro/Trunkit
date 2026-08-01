-- Unified model, step 102: transitive closure over the proof-composition DAG.
--
-- Step 100 made certificates revocable/expirable, but cert.derivation_valid
-- (85) and the derivation block in cert.verify check DIRECT premises only.
-- So with C derived-from B derived-from A, revoking A's certificate degrades
-- B — and C keeps verifying clean. This step closes that gap on stock
-- PostgreSQL 16 (WITH RECURSIVE), and normalizes the edge list as the
-- prerequisite for declaring a SQL/PGQ property graph once PG 19 lands.
--
--   * cert.derivation_premise — one row per (conclusion, premise) edge,
--     fanned out from cert.derivation.premise_ids by trigger + backfill.
--     This is the normalized projection for indexing and future
--     CREATE PROPERTY GRAPH; the CHECKERS below read the premise_ids arrays
--     directly, so a drifted projection can never fake soundness.
--   * cert.tainted_closure — every claim transitively downstream of a claim
--     whose effective standing is not 'valid', with the seed, its status,
--     depth, and the path as evidence.
--   * cert.derivation_valid_deep(claim_id) — (ok, evidence) over ALL
--     transitive premises, not just depth 1. cert.verify (step 100 base and
--     the local kernel overlay) call it in their derivation block, so the
--     propagation is live in every verification path; cert.derivation_valid
--     (85) remains as the per-derivation depth-1 checker.
--
-- Three-valued honesty: appearing in tainted_closure is NOT a refutation.
-- The taint_status column carries the seed's effective_status ('revoked',
-- 'expired', 'unchecked', ...) so a consumer can tell loss-of-trust apart
-- from mere absence of evidence. Cycles cannot occur in a well-formed proof
-- DAG; the deep check detects one, terminates, and FAILS with
-- cycle_detected=true — a circular derivation is unsound support, not proof.
--
-- Idempotent; additive only. No existing object is redefined here — the
-- cert.verify rewire lives in the files that own it (100 / local 94).

-- ---------------------------------------------------------------------------
-- 0. helper: effective standing of one claim (pushes the claim_id predicate
--    into cert.standing instead of materialising the whole view)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION cert.effective_status(p_claim_id BIGINT)
RETURNS TEXT LANGUAGE sql STABLE AS $$
    SELECT effective_status FROM cert.standing WHERE claim_id = p_claim_id
$$;

-- ---------------------------------------------------------------------------
-- 1. normalized edge table: one row per (conclusion, premise) edge
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS cert.derivation_premise (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    derivation_id BIGINT NOT NULL REFERENCES cert.derivation(id),
    conclusion_id BIGINT NOT NULL REFERENCES cert.claim(id),
    -- No FK on premise_id: premise_ids arrays are not FK-constrained upstream,
    -- and a dangling premise must land here as an edge (so closure queries can
    -- flag it) rather than abort the derivation insert.
    premise_id    BIGINT NOT NULL,
    UNIQUE (derivation_id, premise_id)
);

CREATE INDEX IF NOT EXISTS cert_derivation_premise_conclusion_idx
    ON cert.derivation_premise (conclusion_id);
CREATE INDEX IF NOT EXISTS cert_derivation_premise_premise_idx
    ON cert.derivation_premise (premise_id);

COMMENT ON TABLE cert.derivation_premise IS
    'Normalized edge list of the proof-composition DAG: one row per '
    '(conclusion, premise) pair, fanned out from cert.derivation.premise_ids '
    'by trigger. Projection for indexing and for a future SQL/PGQ '
    'CREATE PROPERTY GRAPH (PostgreSQL 19+); the closure checkers read the '
    'premise_ids arrays directly, which remain the source of truth.';

CREATE OR REPLACE FUNCTION cert.derivation_fanout()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO cert.derivation_premise (derivation_id, conclusion_id, premise_id)
    SELECT NEW.id, NEW.conclusion_id, p.premise_id
      FROM unnest(NEW.premise_ids) AS p(premise_id)
    ON CONFLICT (derivation_id, premise_id) DO NOTHING;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS cert_derivation_fanout ON cert.derivation;
CREATE TRIGGER cert_derivation_fanout AFTER INSERT ON cert.derivation
    FOR EACH ROW EXECUTE FUNCTION cert.derivation_fanout();

-- Backfill existing derivations; idempotent via the unique edge constraint.
INSERT INTO cert.derivation_premise (derivation_id, conclusion_id, premise_id)
SELECT d.id, d.conclusion_id, p.premise_id
  FROM cert.derivation d
  CROSS JOIN LATERAL unnest(d.premise_ids) AS p(premise_id)
ON CONFLICT (derivation_id, premise_id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 2. tainted closure: everything downstream of a non-valid claim
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW cert.tainted_closure AS
WITH RECURSIVE bad_seed AS (
    SELECT s.claim_id, s.effective_status
      FROM cert.standing s
     WHERE s.effective_status IS DISTINCT FROM 'valid'
),
walk (claim_id, tainted_by, taint_status, depth, path) AS (
    SELECT d.conclusion_id, b.claim_id, b.effective_status,
           1, ARRAY[b.claim_id, d.conclusion_id]
      FROM cert.derivation d
      JOIN bad_seed b ON b.claim_id = ANY (d.premise_ids)
    UNION ALL
    SELECT d.conclusion_id, w.tainted_by, w.taint_status,
           w.depth + 1, w.path || d.conclusion_id
      FROM walk w
      JOIN cert.derivation d ON w.claim_id = ANY (d.premise_ids)
     WHERE NOT d.conclusion_id = ANY (w.path)
)
SELECT claim_id, tainted_by, taint_status, depth, path FROM walk;

COMMENT ON VIEW cert.tainted_closure IS
    'Claims transitively downstream (via cert.derivation) of a claim whose '
    'effective standing is not ''valid''. One row per (seed, path); a claim '
    'may appear many times. taint_status is the seed''s effective_status — '
    'taint is loss of standing upstream, never a refutation of the claim.';

-- ---------------------------------------------------------------------------
-- 3. deep derivation check: all transitive premises must stand valid
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION cert.derivation_valid_deep(p_claim_id BIGINT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_total BIGINT;
    v_bad_n BIGINT;
    v_bad   JSONB;
    v_cycle BOOLEAN;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM cert.derivation WHERE conclusion_id = p_claim_id) THEN
        ok := TRUE;
        evidence := jsonb_build_object(
            'transitive_premises', 0, 'vacuous', true,
            'note', 'no derivation concludes this claim; nothing to check');
        RETURN NEXT; RETURN;
    END IF;

    -- Standard cycle-flag idiom: a node re-reached on its own path is
    -- RECORDED with is_cycle=true but never expanded, so the walk both
    -- terminates and can report the circularity.
    WITH RECURSIVE up (claim_id, depth, path, is_cycle) AS (
        SELECT p.premise_id, 1, ARRAY[p_claim_id, p.premise_id],
               p.premise_id = p_claim_id
          FROM cert.derivation d
          CROSS JOIN LATERAL unnest(d.premise_ids) AS p(premise_id)
         WHERE d.conclusion_id = p_claim_id
        UNION ALL
        SELECT p.premise_id, u.depth + 1, u.path || p.premise_id,
               p.premise_id = ANY (u.path)
          FROM up u
          JOIN cert.derivation d ON d.conclusion_id = u.claim_id
          CROSS JOIN LATERAL unnest(d.premise_ids) AS p(premise_id)
         WHERE NOT u.is_cycle
    ),
    premises AS (
        SELECT claim_id, MIN(depth) AS depth
          FROM up GROUP BY claim_id
    ),
    graded AS (
        SELECT pr.claim_id, pr.depth,
               COALESCE(cert.effective_status(pr.claim_id), 'missing')
                   AS effective_status
          FROM premises pr
    ),
    bad AS (
        SELECT claim_id, depth, effective_status
          FROM graded
         WHERE effective_status IS DISTINCT FROM 'valid'
         ORDER BY depth, claim_id
         LIMIT 50   -- bound the evidence payload
    )
    SELECT (SELECT count(*) FROM graded),
           (SELECT count(*) FROM graded
             WHERE effective_status IS DISTINCT FROM 'valid'),
           (SELECT COALESCE(jsonb_agg(jsonb_build_object(
                       'claim_id', claim_id,
                       'status',   effective_status,
                       'depth',    depth)), '[]'::jsonb)
              FROM bad),
           (SELECT bool_or(is_cycle) FROM up)
      INTO v_total, v_bad_n, v_bad, v_cycle;

    ok := (v_bad_n = 0 AND NOT COALESCE(v_cycle, FALSE));
    evidence := jsonb_build_object(
        'transitive_premises', v_total,
        'invalid_count',       v_bad_n,
        'invalid_premises',    v_bad,
        'cycle_detected',      COALESCE(v_cycle, FALSE));
    RETURN NEXT;
END $$;

COMMENT ON FUNCTION cert.derivation_valid_deep(BIGINT) IS
    'Transitive analogue of cert.derivation_valid: walks EVERY premise '
    'reachable from the claim through cert.derivation and requires each to '
    'stand effectively valid (revoked/expired/unchecked/missing all fail). '
    'A cycle in the premise walk fails with cycle_detected=true — circular '
    'support is not proof. ok=true with vacuous:true when no derivation '
    'concludes the claim.';
