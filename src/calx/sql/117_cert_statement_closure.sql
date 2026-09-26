-- Unified model, step 117: statement CLOSURE binding -- does the statement
-- still MEAN what the claim thinks it means?
--
-- THE GAP THIS CLOSES. 106 binds a claim to the TEXT of a declaration's
-- statement. Text is not meaning. Take
--
--     def Strong (c : Cfg) : Prop := c.n = c.n + 0
--     theorem target (c : Cfg) : Strong c := rfl
--
-- and weaken the definition to `def Strong (c : Cfg) : Prop := True`. The
-- theorem's signature is byte-identical, so its syntactic digest is too; Lean
-- even pretty-prints the same type (`∀ (c : Drift.Cfg), Drift.Strong c`). 106
-- reports `valid`, and the ledger now certifies that something trivially true
-- is the theorem it bound. tests/fixtures/lean_closure/ holds the real
-- AxiomAudit output for exactly this pair.
--
-- Comparator (github.com/leanprover/comparator) -- the check the Navier-Stokes
-- and Riemann-zeta Lean certificates of 2026 were accepted under -- closes this
-- by requiring every declaration a statement uses to be identical between the
-- challenge and the solution environments. This layer is the single-
-- environment form of the same check, made durable: AxiomAudit walks the
-- statement's closure (every definition, inductive and instance its meaning
-- depends on; theorems skipped by proof irrelevance) and emits a structural
-- hash of each constant's type and body. A binding pins that manifest; a later
-- observation that differs is DEFINITION DRIFT, and the evidence names the
-- constants that moved.
--
-- CONTENT-ADDRESSED MANIFESTS. A real statement's closure is thousands of
-- constants (Formal Conjectures' navier_stokes_breakdown_R3: 3,106; ~195 KB).
-- Manifests are stored once per distinct digest, so re-observing an unchanged
-- closure costs one short row. The digest is RECOMPUTED here from the manifest
-- and a mismatch is refused: the ledger cannot hold a digest paired with a
-- manifest it did not come from, whoever inserted it.
--
-- HONEST LIMITS, stated once.
--   * Comparable only within one toolchain. Different Lean or Mathlib
--     revisions change library hashes wholesale, so a cross-toolchain
--     comparison reports drift that is real but uninformative. Bind and
--     observe in the same environment -- which is also why comparator builds
--     challenge and solution side by side. Bindings and observations each
--     record their toolchain, and cert.statement_bound reports a
--     cross-toolchain comparison as unverified ("not comparable"), never as
--     drift.
--   * Lean's Expr.hash carries 32 significant bits. An individual edit escapes
--     with probability ~2^-32: a drift detector, including for drift an agent
--     introduced, not a defence against a deliberately crafted collision. That
--     adversary is comparator's (the comparator_check.sh checker kind), which
--     compares declarations, not hashes.
--
-- Supersedes 106's body of cert.statement_bound (the file order guarantees
-- this definition wins). Idempotent; additive only.

-- ---------------------------------------------------------------------------
-- 1. A third digest kind
-- ---------------------------------------------------------------------------

ALTER TABLE cert.statement_binding
    DROP CONSTRAINT IF EXISTS statement_binding_digest_kind_check;
ALTER TABLE cert.statement_binding
    ADD CONSTRAINT statement_binding_digest_kind_check
    CHECK (digest_kind IN ('syntactic', 'elaborated', 'closure'));

-- The environment an observation was taken in. On the OBSERVATION, not on the
-- manifest: manifests are content-addressed, and a small closure can hash the
-- same under two toolchains, so a toolchain stored with the manifest would be
-- whichever observer arrived first. The binding already carries its own
-- (106's statement_binding.toolchain); comparability is those two compared.
ALTER TABLE cert.statement_observation
    ADD COLUMN IF NOT EXISTS toolchain TEXT NOT NULL DEFAULT '';

-- ---------------------------------------------------------------------------
-- 2. Content-addressed closure manifests
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS cert.closure_manifest (
    sha256       TEXT PRIMARY KEY CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    -- {"<constant>": ["<type_hash>", "<value_hash>"], ...}
    manifest     JSONB NOT NULL CHECK (jsonb_typeof(manifest) = 'object'),
    n_constants  INTEGER NOT NULL,
    -- Informational only: the toolchain of the FIRST observer. Comparability
    -- is decided from the binding and observation rows, never from this.
    toolchain    TEXT NOT NULL DEFAULT '',
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS cert_closure_manifest_append_only ON cert.closure_manifest;
CREATE TRIGGER cert_closure_manifest_append_only
    BEFORE UPDATE OR DELETE ON cert.closure_manifest
    FOR EACH ROW EXECUTE FUNCTION cert.reject_mutation();

-- The recipe, in SQL. Must stay byte-identical to
-- calx.leanbridge.closure_manifest_digest:
--   sha256( join("\n", sorted-by-name "<name>\t<type_hash>\t<value_hash>") )
-- COLLATE "C" is code-point order, which is what Python's sorted() uses.
CREATE OR REPLACE FUNCTION cert.closure_manifest_digest(p_manifest JSONB)
RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
    SELECT encode(sha256(convert_to(COALESCE(
               string_agg(e.key || E'\t' || (e.value->>0) || E'\t' || (e.value->>1),
                          E'\n' ORDER BY e.key COLLATE "C"),
               ''), 'UTF8')), 'hex')
      FROM jsonb_each(p_manifest) AS e
$$;

CREATE OR REPLACE FUNCTION cert.record_closure_manifest(
    p_sha256 TEXT, p_manifest JSONB, p_toolchain TEXT DEFAULT ''
) RETURNS TEXT LANGUAGE plpgsql AS $$
DECLARE v_actual TEXT;
BEGIN
    v_actual := cert.closure_manifest_digest(p_manifest);
    IF v_actual IS DISTINCT FROM p_sha256 THEN
        RAISE EXCEPTION 'closure manifest digest mismatch: claimed %, manifest hashes to %',
            p_sha256, v_actual;
    END IF;
    INSERT INTO cert.closure_manifest (sha256, manifest, n_constants, toolchain)
    VALUES (p_sha256, p_manifest,
            (SELECT count(*) FROM jsonb_object_keys(p_manifest)), p_toolchain)
    ON CONFLICT (sha256) DO NOTHING;
    RETURN p_sha256;
END $$;

-- Which constants moved between two manifests. NULL when either manifest is
-- not on record -- a digest mismatch is still drift, it just cannot say where.
CREATE OR REPLACE FUNCTION cert.closure_diff(p_bound TEXT, p_observed TEXT)
RETURNS JSONB LANGUAGE sql STABLE AS $$
    WITH b AS (SELECT manifest FROM cert.closure_manifest WHERE sha256 = p_bound),
         o AS (SELECT manifest FROM cert.closure_manifest WHERE sha256 = p_observed)
    SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM b) OR NOT EXISTS (SELECT 1 FROM o)
                THEN NULL
           ELSE jsonb_build_object(
             'changed', COALESCE((SELECT jsonb_agg(k ORDER BY k COLLATE "C")
                 FROM b, o, jsonb_object_keys(b.manifest) AS k
                WHERE o.manifest ? k AND o.manifest->k <> b.manifest->k), '[]'),
             'added', COALESCE((SELECT jsonb_agg(k ORDER BY k COLLATE "C")
                 FROM b, o, jsonb_object_keys(o.manifest) AS k
                WHERE NOT b.manifest ? k), '[]'),
             'removed', COALESCE((SELECT jsonb_agg(k ORDER BY k COLLATE "C")
                 FROM b, o, jsonb_object_keys(b.manifest) AS k
                WHERE NOT o.manifest ? k), '[]'))
           END
$$;

-- ---------------------------------------------------------------------------
-- 3. Bind and observe
-- ---------------------------------------------------------------------------

-- stmt_text for a closure binding is the auditor's pretty-printed type: what a
-- reader needs to see what was bound. It is not what is compared.
CREATE OR REPLACE FUNCTION cert.bind_statement_closure(
    p_claim_id BIGINT, p_decl_name TEXT, p_type_text TEXT,
    p_sha256 TEXT, p_manifest JSONB, p_toolchain TEXT DEFAULT ''
) RETURNS cert.statement_binding LANGUAGE plpgsql AS $$
BEGIN
    PERFORM cert.record_closure_manifest(p_sha256, p_manifest, p_toolchain);
    RETURN cert.bind_statement(p_claim_id, p_decl_name, p_type_text, p_sha256,
                               'closure', p_toolchain);
END $$;

-- Records the manifest and an observation against the claim's newest CLOSURE
-- binding. Returns NULL (and records only the manifest) when the claim has no
-- closure binding: an unbound observation has nothing to be compared to, and
-- inventing a binding here would be trust-on-first-use by the back door.
CREATE OR REPLACE FUNCTION cert.observe_statement_closure(
    p_claim_id BIGINT, p_sha256 TEXT, p_manifest JSONB,
    p_type_text TEXT DEFAULT NULL, p_closure_digest TEXT DEFAULT '',
    p_toolchain TEXT DEFAULT ''
) RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE v_binding BIGINT;
BEGIN
    PERFORM cert.record_closure_manifest(p_sha256, p_manifest, p_toolchain);
    SELECT id INTO v_binding FROM cert.statement_binding
     WHERE claim_id = p_claim_id AND digest_kind = 'closure'
     ORDER BY bound_at DESC, id DESC LIMIT 1;
    IF v_binding IS NULL THEN
        RETURN NULL;
    END IF;
    INSERT INTO cert.statement_observation
        (binding_id, observed_sha256, observed_text, closure_digest, toolchain)
    VALUES (v_binding, p_sha256, p_type_text, p_closure_digest, p_toolchain)
    RETURNING id INTO v_binding;
    RETURN v_binding;
END $$;

-- ---------------------------------------------------------------------------
-- 4. The drift probe, closure-aware
-- ---------------------------------------------------------------------------

-- Same three verdicts as 106. The one change: on a closure binding, drift
-- evidence carries cert.closure_diff, so the refutation says WHICH definition
-- moved rather than only that a hash did.
CREATE OR REPLACE FUNCTION cert.statement_bound(p_claim_id BIGINT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    b   cert.statement_binding%ROWTYPE;
    o   cert.statement_observation%ROWTYPE;
    v_n_bindings INTEGER;
    v_diff JSONB;
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
        IF b.digest_kind = 'closure' THEN
            -- A closure taken under another toolchain differs wholesale and
            -- says nothing about meaning (header, first limit). That is
            -- "not comparable", which is unverified -- reporting it as drift
            -- would refute every correct proof that moved to a newer Mathlib.
            IF o.toolchain IS DISTINCT FROM b.toolchain THEN
                ok := NULL;
                evidence := jsonb_build_object(
                    'reason', 'NOT COMPARABLE: the closure was observed under a '
                              'different toolchain than it was bound in; rebind '
                              'in the proof''s environment, or check with comparator',
                    'binding_id', b.id, 'decl', b.decl_name,
                    'bound_toolchain', b.toolchain,
                    'observed_toolchain', o.toolchain);
                RETURN NEXT; RETURN;
            END IF;
            v_diff := cert.closure_diff(b.stmt_sha256, o.observed_sha256);
            evidence := jsonb_build_object(
                'reason', 'DEFINITION DRIFT: a constant the statement depends on '
                          'changed -- the declaration may read the same and no '
                          'longer mean what this claim was bound to',
                'closure_diff', v_diff);
        ELSE
            evidence := jsonb_build_object(
                'reason', 'STATEMENT DRIFT: the declaration no longer has the '
                          'statement this claim was bound to');
        END IF;
        evidence := evidence || jsonb_build_object(
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

-- Lean-backed claims whose meaning is not pinned: a syntactic binding (or
-- none) is exactly the case the header's example slips through.
CREATE OR REPLACE VIEW cert.lean_claims_without_closure_binding AS
SELECT c.id AS claim_id, c.statement, a.target_decl, a.project_root
  FROM cert.claim c
  JOIN cert.artifact a ON a.claim_id = c.id AND a.kind = 'lean'
 WHERE NOT EXISTS (SELECT 1 FROM cert.statement_binding sb
                    WHERE sb.claim_id = c.id AND sb.digest_kind = 'closure');

COMMENT ON TABLE cert.closure_manifest IS
    'Content-addressed statement closures from AxiomAudit: every constant a '
    'statement''s meaning depends on, with structural type/body hashes. The '
    'digest is recomputed on insert and a mismatch is refused.';
COMMENT ON FUNCTION cert.closure_diff(TEXT, TEXT) IS
    'Constants changed/added/removed between two closure manifests. Only '
    'meaningful for manifests taken under the same toolchain.';
