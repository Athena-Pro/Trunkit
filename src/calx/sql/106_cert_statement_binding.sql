-- Unified model, step 106: statement binding -- does the artifact still prove
-- the claim the ledger thinks it proves?
--
-- THE GAP THIS CLOSES. `trunkit register-lean` binds a claim to a Lean project
-- by hashing the FILE CLOSURE (leanbridge.closure_digest). That answers "have
-- the files changed", which is not the question. The question is whether the
-- declaration the checker builds still has the type the claim asserts. A
-- proof that compiles is worthless if the theorem drifted out from under it,
-- and the drift is invisible to a closure digest: editing a hypothesis
-- changes the closure hash exactly as much as fixing a typo in a comment, so
-- re-registering hides it.
--
-- AlphaProof Nexus (arXiv:2605.22763) runs this check on every episode: a
-- sandbox pass that permits `sorry` placeholders but verifies that the
-- original target theorem statement was not altered. They need it because
-- their agents edit the file the theorem lives in. So does anything that lets
-- an LLM touch a proof project.
--
-- WHAT IS BOUND. A binding pins (claim, declaration, statement digest) at a
-- moment in time, with the toolchain that produced the digest. Verification
-- is an OBSERVATION: the harness re-derives the digest from the current
-- project and records it here. The probe compares the latest observation to
-- the binding. That split matters -- the database cannot elaborate Lean, and
-- pretending otherwise would make this a checker that always says yes.
--
-- Three-valued by construction:
--   valid       latest observation matches the bound digest
--   refuted     latest observation differs -- the statement drifted
--   unverified  no observation, or the newest observation predates the
--               artifact's current closure digest (so it says nothing about
--               the files as they now stand)
--
-- The unverified case is the one that earns its keep. "Nobody has re-checked
-- this since the files changed" is the normal state of a live proof project,
-- and reporting it as valid would be the whole bug this layer exists to
-- prevent. Idempotent; additive only.

INSERT INTO cert.method (name, claim_kind, checker_kind, description)
VALUES ('comp_sql', 'computational', 'sql', 'in-DB probe returning (ok, evidence)')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 1. Bindings and observations
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS cert.statement_binding (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    claim_id      BIGINT NOT NULL REFERENCES cert.claim(id) ON DELETE CASCADE,
    -- Fully-qualified declaration, e.g. Erdos125.target_theorem_0. The same
    -- claim may be proved by different declarations over time; each gets its
    -- own binding, and the newest one wins.
    decl_name     TEXT NOT NULL,
    -- Digest of the declaration's STATEMENT, not its proof. The recipe lives
    -- in calx.statement_digest (Python) so the harness and the ledger agree;
    -- digest_kind records which recipe was used, because a syntactic digest
    -- and an elaborated one are not interchangeable and must never silently
    -- compare equal.
    stmt_sha256   TEXT NOT NULL CHECK (stmt_sha256 ~ '^[0-9a-f]{64}$'),
    digest_kind   TEXT NOT NULL DEFAULT 'syntactic'
                  CHECK (digest_kind IN ('syntactic', 'elaborated')),
    -- The statement text as bound, kept verbatim. Without it a mismatch tells
    -- you that something changed but not what, which is the difference between
    -- a usable alert and a shrug.
    stmt_text     TEXT NOT NULL,
    toolchain     TEXT NOT NULL DEFAULT '',
    bound_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    bound_by      TEXT NOT NULL DEFAULT cert.signer_identity(),
    UNIQUE (claim_id, decl_name, stmt_sha256, digest_kind)
);

CREATE INDEX IF NOT EXISTS statement_binding_claim_idx
    ON cert.statement_binding (claim_id, bound_at DESC);

-- Append-only: a binding is a historical assertion about what was proved at a
-- point in time. Rebinding after a legitimate statement change is a NEW row,
-- which is what makes the drift visible in the first place.
DROP TRIGGER IF EXISTS cert_statement_binding_append_only ON cert.statement_binding;
CREATE TRIGGER cert_statement_binding_append_only
    BEFORE UPDATE OR DELETE ON cert.statement_binding
    FOR EACH ROW EXECUTE FUNCTION cert.reject_mutation();

CREATE TABLE IF NOT EXISTS cert.statement_observation (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    binding_id     BIGINT NOT NULL REFERENCES cert.statement_binding(id) ON DELETE CASCADE,
    observed_sha256 TEXT NOT NULL CHECK (observed_sha256 ~ '^[0-9a-f]{64}$'),
    observed_text  TEXT,
    -- Closure digest of the project the observation was taken from, so an
    -- observation can be aged out when the files move on.
    closure_digest TEXT NOT NULL DEFAULT '',
    observed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    observed_by    TEXT NOT NULL DEFAULT cert.signer_identity()
);

CREATE INDEX IF NOT EXISTS statement_observation_binding_idx
    ON cert.statement_observation (binding_id, observed_at DESC);

CREATE OR REPLACE FUNCTION cert.bind_statement(
    p_claim_id    BIGINT,
    p_decl_name   TEXT,
    p_stmt_text   TEXT,
    p_stmt_sha256 TEXT,
    p_digest_kind TEXT DEFAULT 'syntactic',
    p_toolchain   TEXT DEFAULT ''
) RETURNS cert.statement_binding
LANGUAGE plpgsql AS $$
DECLARE v_row cert.statement_binding%ROWTYPE;
BEGIN
    INSERT INTO cert.statement_binding
        (claim_id, decl_name, stmt_sha256, digest_kind, stmt_text, toolchain)
    VALUES (p_claim_id, p_decl_name, p_stmt_sha256, p_digest_kind, p_stmt_text, p_toolchain)
    ON CONFLICT (claim_id, decl_name, stmt_sha256, digest_kind) DO NOTHING
    RETURNING * INTO v_row;
    IF v_row.id IS NULL THEN
        SELECT * INTO v_row FROM cert.statement_binding
         WHERE claim_id = p_claim_id AND decl_name = p_decl_name
           AND stmt_sha256 = p_stmt_sha256 AND digest_kind = p_digest_kind;
    END IF;
    RETURN v_row;
END
$$;

CREATE OR REPLACE FUNCTION cert.observe_statement(
    p_binding_id     BIGINT,
    p_observed_sha256 TEXT,
    p_observed_text  TEXT DEFAULT NULL,
    p_closure_digest TEXT DEFAULT ''
) RETURNS cert.statement_observation
LANGUAGE plpgsql AS $$
DECLARE v_row cert.statement_observation%ROWTYPE;
BEGIN
    INSERT INTO cert.statement_observation
        (binding_id, observed_sha256, observed_text, closure_digest)
    VALUES (p_binding_id, p_observed_sha256, p_observed_text, p_closure_digest)
    RETURNING * INTO v_row;
    RETURN v_row;
END
$$;

-- ---------------------------------------------------------------------------
-- 2. The drift probe
-- ---------------------------------------------------------------------------

-- ok = true   latest observation matches the newest binding
-- ok = false  latest observation differs -- STATEMENT DRIFT
-- ok = NULL   never observed (unverified, not valid)
--
-- Compares against the NEWEST binding for the claim: rebinding is the
-- sanctioned way to record an intentional statement change, and the drift
-- question is always asked about the current intent.
CREATE OR REPLACE FUNCTION cert.statement_bound(p_claim_id BIGINT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    b   cert.statement_binding%ROWTYPE;
    o   cert.statement_observation%ROWTYPE;
    v_n_bindings INTEGER;
BEGIN
    SELECT * INTO b FROM cert.statement_binding
     WHERE claim_id = p_claim_id ORDER BY bound_at DESC, id DESC LIMIT 1;
    IF b.id IS NULL THEN
        ok := NULL;
        evidence := jsonb_build_object(
            'reason', 'no statement binding for this claim',
            'claim_id', p_claim_id);
        RETURN NEXT; RETURN;
    END IF;

    SELECT COUNT(*) INTO v_n_bindings
      FROM cert.statement_binding WHERE claim_id = p_claim_id;

    SELECT * INTO o FROM cert.statement_observation
     WHERE binding_id = b.id ORDER BY observed_at DESC, id DESC LIMIT 1;
    IF o.id IS NULL THEN
        ok := NULL;
        evidence := jsonb_build_object(
            'reason', 'bound but never re-observed -- nothing has checked the '
                      'declaration since it was bound',
            'binding_id', b.id, 'decl', b.decl_name,
            'bound_at', b.bound_at, 'digest_kind', b.digest_kind);
        RETURN NEXT; RETURN;
    END IF;

    IF o.observed_sha256 IS DISTINCT FROM b.stmt_sha256 THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', 'STATEMENT DRIFT: the declaration no longer has the '
                      'statement this claim was bound to',
            'binding_id', b.id, 'decl', b.decl_name,
            'digest_kind', b.digest_kind,
            'bound_sha256', b.stmt_sha256,
            'observed_sha256', o.observed_sha256,
            'bound_text', b.stmt_text,
            'observed_text', o.observed_text,
            'observed_at', o.observed_at,
            'rebindings', v_n_bindings);
        RETURN NEXT; RETURN;
    END IF;

    ok := true;
    evidence := jsonb_build_object(
        'binding_id', b.id, 'decl', b.decl_name, 'digest_kind', b.digest_kind,
        'sha256', b.stmt_sha256, 'observed_at', o.observed_at,
        'closure_digest', NULLIF(o.closure_digest, ''),
        'rebindings', v_n_bindings);
    RETURN NEXT;
END
$$;

CREATE OR REPLACE FUNCTION cert.statement_binding_claim(p_claim_id BIGINT)
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE b cert.statement_binding%ROWTYPE; v_stmt TEXT; v_probe TEXT; v_id BIGINT;
BEGIN
    SELECT * INTO b FROM cert.statement_binding
     WHERE claim_id = p_claim_id ORDER BY bound_at DESC, id DESC LIMIT 1;
    IF b.id IS NULL THEN
        RAISE EXCEPTION 'no statement binding for claim % (call cert.bind_statement first)',
            p_claim_id;
    END IF;
    v_stmt := format('declaration %s still carries the statement claim #%s was bound to',
                     b.decl_name, p_claim_id);
    v_probe := format('SELECT ok, evidence FROM cert.statement_bound(%s)', p_claim_id);
    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
    VALUES ('statement_binding',
            jsonb_build_object('claim_id', p_claim_id, 'decl', b.decl_name),
            v_stmt, 'computational', 'comp_sql', v_probe)
    ON CONFLICT (statement) DO UPDATE SET probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

-- Every claim carrying a Lean artifact but no statement binding: the backlog
-- of proofs nobody can prove are still about what they say they are.
CREATE OR REPLACE VIEW cert.unbound_formal_claims AS
SELECT c.id AS claim_id, c.statement, a.target_decl, a.registered_at
  FROM cert.claim c
  JOIN cert.artifact a ON a.claim_id = c.id
 WHERE NOT EXISTS (SELECT 1 FROM cert.statement_binding sb WHERE sb.claim_id = c.id);

COMMENT ON TABLE cert.statement_binding IS
    'Binds a claim to the STATEMENT of a declaration, not to its proof or its '
    'file closure. cert.statement_bound compares the newest binding against '
    'the newest observation: mismatch is drift (refuted), absence is '
    'unverified. Closes the gap left by register-lean, which hashes files.';
