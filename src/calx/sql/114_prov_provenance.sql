-- Unified model, step 114: the `prov` schema -- solution provenance.
--
-- Implements gap T5 of docs/reports/Trunkit_Erdos_AI_Capability_Fit.md: attest
-- the CREDIT STRUCTURE, not just the proof. The Erdős tracker's own architecture
-- -- sections 1(a) original / 1(b) independent rediscovery / 1(c) prior art /
-- 1(d) human+AI, each cell graded full / partial / incorrect, with "Literature
-- result", "found on" and "Similar proofs?" columns -- is already a
-- multi-contributor, three-valued attestation ledger maintained by hand in a
-- wiki. This step gives it a schema.
--
-- WHAT THIS LAYER ADDS, AND WHAT IT REFUSES TO ADD. It adds three tables and a
-- view. It does NOT add a propagation engine: cert.derivation (85) already
-- composes proofs and cert.tainted_closure / cert.derivation_valid_deep (102)
-- already walk that DAG transitively over revocation and expiry. A second
-- traversal here would be a second thing to keep correct, and the two would
-- drift the first time one was fixed. So a provenance edge MIRRORS into
-- cert.derivation and propagation is inherited rather than reimplemented.
-- The only new content in this file is the modelling: who contributed what,
-- and which of those relations actually carry support.
--
-- ============================================================================
-- THE ONE REAL DESIGN DECISION: `corrected` IS NOT A SUPPORT EDGE.
-- ============================================================================
-- The obvious implementation makes every provenance edge a derivation edge.
-- That is wrong, and wrong in the direction that silently destroys evidence.
--
-- cert.derivation means "conclusion holds BECAUSE the premises hold". For
-- `used`, `formalized` and `prior_literature` that is exactly right: a Lean
-- formalization of an AI proof is worthless if the proof it formalizes is
-- withdrawn, so revoking the source must taint the formalization. Support
-- flows from the earlier artifact to the later one.
--
-- `corrected` inverts it. When a human step corrects a flawed AI output, the
-- correction does not depend on the flawed artifact being valid -- it exists
-- precisely BECAUSE that artifact is not. Mirroring it as a derivation edge
-- would make revoking the error taint its own fix, so the moment the ledger
-- recorded that something was wrong it would also stop believing the thing
-- that made it right. The repair would vanish along with the defect.
--
-- So `corrected` is recorded as provenance and creates NO derivation edge. It
-- is credit and audit history, not support. This is the same three-valued
-- honesty the rest of cert keeps: losing standing upstream is not a refutation,
-- and being downstream of a mistake is not the same as inheriting it.
--
-- ============================================================================
-- GRADES ARE EDITORIAL, VERDICTS ARE EARNED. The tracker grades cells
-- 🟢 full / 🟡 partial / 🔴 incorrect. cert is valid / refuted / unverified --
-- there is no `partial`, and this step does not invent one. A grade is what a
-- human maintainer asserts; a verdict is what a probe re-derived. They are kept
-- in separate columns and prov.standing shows both, because collapsing them
-- would let an editorial 🟢 read as a checked `valid` -- laundering opinion into
-- proof, which is the single failure this whole system exists to prevent. An
-- artifact with no claim_id is `unverified` forever, however green its grade.
--
-- TRUST TIER IS NOT TRUSTWORTHINESS. contributor.trust_tier records what KIND
-- of thing produced an artifact (peer-reviewed literature, a named AI system, a
-- human step, a mechanical checker), because that is what the tracker records
-- and what a reader wants to filter on. It is metadata, never an input to any
-- verdict: no probe in this file reads it. A tier-4 Lean checker output and a
-- tier-1 blog post are verified by exactly the same machinery.
--
-- NO FOREIGN KEY INTO calx, for the reason given at length in 110: calx is
-- GENERATED and `trunkit reset` drops it CASCADE, while provenance is
-- DEPOSITED and must survive regeneration.
--
-- Idempotent; additive only.

CREATE SCHEMA IF NOT EXISTS prov;

COMMENT ON SCHEMA prov IS
    'Solution provenance (T5): contributors, artifacts and the edges between '
    'them, for AI-assisted mathematics. Support edges mirror into '
    'cert.derivation so revocation propagates through 102 rather than through '
    'a second traversal maintained here.';

-- ---------------------------------------------------------------------------
-- 1. contributors
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS prov.contributor (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL
        CHECK (kind IN ('human', 'ai_system', 'literature', 'tool')),
    -- Stable external identity where one exists: ORCID, arXiv id, DOI, a model
    -- version string, a toolchain revision. Free-form because these namespaces
    -- do not unify, and a wrong-but-parseable identifier is worse than a note.
    identifier  TEXT,
    trust_tier  TEXT NOT NULL DEFAULT 'unrated'
        CHECK (trust_tier IN ('unrated', 'self_reported', 'community',
                              'peer_reviewed', 'machine_checked')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE prov.contributor IS
    'Who produced an artifact. trust_tier is descriptive metadata for readers '
    'and filters -- no probe in this layer reads it, and it can never move a '
    'verdict.';

-- ---------------------------------------------------------------------------
-- 2. artifacts -- the DAG nodes
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS prov.artifact (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Free text, e.g. 'erdos_397'. Not a foreign key: Trunkit does not host a
    -- problem registry, and inventing one here would make this layer the
    -- authority on which problems exist.
    problem      TEXT NOT NULL,
    kind         TEXT NOT NULL
        CHECK (kind IN ('prior_literature', 'ai_output', 'human_step',
                        'lean_artifact')),
    contributor_id BIGINT NOT NULL REFERENCES prov.contributor(id),
    title        TEXT NOT NULL,
    -- DOI / arXiv id / repo URL + commit / witness sha. What a reader follows
    -- to check the artifact by hand.
    locator      TEXT,
    -- The cert claim carrying this artifact's correctness, when there is one.
    -- NULL is the common and honest case: a 1971 paper has no in-DB probe.
    -- A NULL claim_id means this node can never be better than `unverified`,
    -- and -- see prov.is_support_relation -- cannot transmit support either.
    --
    -- NO FOREIGN KEY, on purpose, for a sharper reason than 110's. `trunkit
    -- reset` runs DROP SCHEMA cert CASCADE, which would drop this constraint
    -- while leaving the column and its now-dangling ids; re-applying this file
    -- would not restore it, because CREATE TABLE IF NOT EXISTS is a no-op on an
    -- existing table. A fresh install would be constrained and a reset install
    -- would not, silently, forever. A constraint that survives only until the
    -- next routine reset is worse than none, because it is believed. 102 makes
    -- the same call for derivation_premise.premise_id and says so there.
    claim_id     BIGINT,
    -- The maintainer's editorial grade. Deliberately NOT a cert status.
    grade        TEXT NOT NULL DEFAULT 'ungraded'
        CHECK (grade IN ('ungraded', 'full', 'partial', 'incorrect')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (problem, title)
);

CREATE INDEX IF NOT EXISTS prov_artifact_problem_idx ON prov.artifact (problem);
CREATE INDEX IF NOT EXISTS prov_artifact_claim_idx   ON prov.artifact (claim_id);

COMMENT ON TABLE prov.artifact IS
    'One contribution to one problem: prior literature, an AI output, a human '
    'step, or a Lean artifact. grade is editorial (the tracker''s '
    'full/partial/incorrect); the verdict comes from claim_id via cert.standing '
    'and the two are never merged.';

-- ---------------------------------------------------------------------------
-- 3. edges
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS prov.edge (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    src_id      BIGINT NOT NULL REFERENCES prov.artifact(id),
    dst_id      BIGINT NOT NULL REFERENCES prov.artifact(id),
    -- used             — dst built on src
    -- formalized       — dst is a machine-checked rendering of src
    -- prior_literature — src anticipates dst (1(c) prior art)
    -- corrected        — dst repairs a defect in src. NOT support; see header.
    relation    TEXT NOT NULL
        CHECK (relation IN ('used', 'formalized', 'prior_literature',
                            'corrected')),
    note        TEXT,
    -- The mirrored cert.derivation row, when this relation carries support and
    -- both endpoints bind claims. NULL for `corrected` always, and for support
    -- edges whose endpoints are not both claim-bound. Unconstrained for the
    -- same reset-CASCADE reason as artifact.claim_id.
    derivation_id BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (src_id, dst_id, relation),
    CHECK (src_id <> dst_id)
);

CREATE INDEX IF NOT EXISTS prov_edge_src_idx ON prov.edge (src_id);
CREATE INDEX IF NOT EXISTS prov_edge_dst_idx ON prov.edge (dst_id);

COMMENT ON TABLE prov.edge IS
    'Provenance relation between two artifacts. used / formalized / '
    'prior_literature carry support and mirror into cert.derivation; '
    'corrected does not -- a fix must not be tainted by the defect it repairs.';

-- Which relations carry support. A function rather than a CHECK so the rule is
-- stated once and read by both the mirror and the tests.
CREATE OR REPLACE FUNCTION prov.is_support_relation(p_relation TEXT)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE AS $$
    SELECT p_relation IN ('used', 'formalized', 'prior_literature')
$$;

COMMENT ON FUNCTION prov.is_support_relation(TEXT) IS
    'True when a relation means "dst holds because src holds", and therefore '
    'mirrors into cert.derivation. False for corrected: the correction does '
    'not depend on the flawed artifact being valid -- it exists because it is '
    'not -- so mirroring it would let revoking a defect taint its own repair.';

-- ---------------------------------------------------------------------------
-- 4. registration
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION prov.register_contributor(
    p_name TEXT, p_kind TEXT, p_trust_tier TEXT DEFAULT 'unrated',
    p_identifier TEXT DEFAULT NULL)
RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE v_id BIGINT;
BEGIN
    INSERT INTO prov.contributor (name, kind, trust_tier, identifier)
    VALUES (p_name, p_kind, p_trust_tier, p_identifier)
    ON CONFLICT (name) DO UPDATE SET kind = EXCLUDED.kind
    RETURNING id INTO v_id;
    RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION prov.register_artifact(
    p_problem TEXT, p_kind TEXT, p_contributor TEXT, p_title TEXT,
    p_locator TEXT DEFAULT NULL, p_claim_id BIGINT DEFAULT NULL,
    p_grade TEXT DEFAULT 'ungraded')
RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE v_contrib BIGINT; v_id BIGINT;
BEGIN
    SELECT id INTO v_contrib FROM prov.contributor WHERE name = p_contributor;
    IF v_contrib IS NULL THEN
        RAISE EXCEPTION 'prov.register_artifact: unknown contributor %', p_contributor;
    END IF;

    INSERT INTO prov.artifact (problem, kind, contributor_id, title, locator,
                               claim_id, grade)
    VALUES (p_problem, p_kind, v_contrib, p_title, p_locator, p_claim_id, p_grade)
    ON CONFLICT (problem, title) DO UPDATE
        SET locator  = COALESCE(EXCLUDED.locator, prov.artifact.locator),
            claim_id = COALESCE(EXCLUDED.claim_id, prov.artifact.claim_id),
            grade    = EXCLUDED.grade
    RETURNING id INTO v_id;
    RETURN v_id;
END $$;

-- ---------------------------------------------------------------------------
-- 5. linking -- where the mirror into cert.derivation happens
-- ---------------------------------------------------------------------------

-- Records the provenance edge and, when the relation carries support AND both
-- endpoints bind a claim, the matching cert.derivation row. Idempotent: the
-- unique (src,dst,relation) key means re-running does not stack edges, and an
-- existing derivation is reused rather than duplicated.
--
-- A support edge between artifacts that are not both claim-bound is still
-- recorded -- it is true, and it is the credit structure the tracker wants --
-- but it transmits nothing. That is not a degenerate case: most prior
-- literature has no in-DB probe, so the honest outcome is a visible edge with
-- derivation_id NULL rather than a fabricated claim to hang it on.
CREATE OR REPLACE FUNCTION prov.link(
    p_src_id BIGINT, p_dst_id BIGINT, p_relation TEXT, p_note TEXT DEFAULT NULL)
RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE
    v_src_claim BIGINT; v_dst_claim BIGINT;
    v_deriv BIGINT; v_edge BIGINT; v_rule TEXT;
BEGIN
    SELECT claim_id INTO v_src_claim FROM prov.artifact WHERE id = p_src_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'prov.link: no artifact %', p_src_id;
    END IF;
    SELECT claim_id INTO v_dst_claim FROM prov.artifact WHERE id = p_dst_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'prov.link: no artifact %', p_dst_id;
    END IF;

    IF prov.is_support_relation(p_relation)
       AND v_src_claim IS NOT NULL AND v_dst_claim IS NOT NULL THEN
        v_rule := 'prov_' || p_relation;
        SELECT id INTO v_deriv FROM cert.derivation
         WHERE conclusion_id = v_dst_claim
           AND premise_ids @> ARRAY[v_src_claim]
           AND rule = v_rule;
        IF v_deriv IS NULL THEN
            INSERT INTO cert.derivation (conclusion_id, premise_ids, rule)
            VALUES (v_dst_claim, ARRAY[v_src_claim], v_rule)
            RETURNING id INTO v_deriv;
        END IF;
    END IF;

    INSERT INTO prov.edge (src_id, dst_id, relation, note, derivation_id)
    VALUES (p_src_id, p_dst_id, p_relation, p_note, v_deriv)
    ON CONFLICT (src_id, dst_id, relation) DO UPDATE
        SET note          = COALESCE(EXCLUDED.note, prov.edge.note),
            derivation_id = COALESCE(EXCLUDED.derivation_id, prov.edge.derivation_id)
    RETURNING id INTO v_edge;

    RETURN v_edge;
END $$;

COMMENT ON FUNCTION prov.link(BIGINT, BIGINT, TEXT, TEXT) IS
    'Record a provenance edge, mirroring it into cert.derivation when the '
    'relation carries support and both artifacts bind claims. Revocation '
    'propagation is then 102''s job, not this layer''s.';

-- ---------------------------------------------------------------------------
-- 6. standing -- per artifact, three-valued, taint-aware
-- ---------------------------------------------------------------------------

-- effective_status is deliberately the CLAIM's standing, not a fourth vocabulary
-- invented here:
--   unverified — no claim bound, or the claim itself stands unverified
--   <status>   — whatever cert.standing says (valid / refuted / revoked / ...)
-- tainted_by / taint_status expose 102's transitive verdict alongside it, so a
-- reader can tell "this artifact was refuted" from "this artifact is fine but
-- something it rests on lost standing". Those are different facts and the
-- tracker conflates them; here they are separate columns.
CREATE OR REPLACE VIEW prov.standing AS
SELECT a.id                AS artifact_id,
       a.problem,
       a.kind,
       a.title,
       c.name              AS contributor,
       c.kind              AS contributor_kind,
       c.trust_tier,
       a.grade,
       a.claim_id,
       COALESCE(s.effective_status, 'unverified') AS effective_status,
       t.tainted_by,
       t.taint_status,
       t.depth             AS taint_depth
  FROM prov.artifact a
  JOIN prov.contributor c ON c.id = a.contributor_id
  LEFT JOIN cert.standing s ON s.claim_id = a.claim_id
  LEFT JOIN LATERAL (
        SELECT tc.tainted_by, tc.taint_status, tc.depth
          FROM cert.tainted_closure tc
         WHERE tc.claim_id = a.claim_id
         ORDER BY tc.depth
         LIMIT 1
  ) t ON TRUE;

COMMENT ON VIEW prov.standing IS
    'Per-artifact standing. effective_status is the bound claim''s standing '
    '(unverified when no claim is bound); tainted_by/taint_status carry 102''s '
    'transitive result so loss of upstream standing stays distinguishable from '
    'refutation of the artifact itself. grade sits beside them and is never '
    'folded in.';

-- ---------------------------------------------------------------------------
-- 7. credit -- who contributed to a problem, and does it still stand
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION prov.credit(p_problem TEXT)
RETURNS TABLE (
    contributor      TEXT,
    contributor_kind TEXT,
    trust_tier       TEXT,
    artifacts        BIGINT,
    standing         JSONB
) LANGUAGE sql STABLE AS $$
    SELECT s.contributor,
           s.contributor_kind,
           s.trust_tier,
           count(*)::BIGINT,
           jsonb_object_agg(s.effective_status, s.n)
      FROM (
            SELECT contributor, contributor_kind, trust_tier, effective_status,
                   count(*)::INT AS n
              FROM prov.standing
             WHERE problem = p_problem
             GROUP BY 1, 2, 3, 4
      ) s
     GROUP BY s.contributor, s.contributor_kind, s.trust_tier
     ORDER BY s.contributor
$$;

COMMENT ON FUNCTION prov.credit(TEXT) IS
    'Credit structure for one problem: each contributor with the standing '
    'breakdown of what they contributed. The wiki table, derived rather than '
    'hand-maintained.';
