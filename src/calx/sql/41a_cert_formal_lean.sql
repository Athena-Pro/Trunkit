-- Unified model, step 41a: Lean adapter for the formal tier (T1 Lean bridge).
--
-- ADDITIVE over 41_cert_formal.sql. A Lean proof is a Lake *project* plus a
-- named *declaration*, not a single file, and its trust gate is the declaration's
-- axiom set (not just `lake build` exit code). This step extends cert.artifact
-- with the project/declaration/toolchain metadata the Lean checker needs, and
-- adds a thin registration helper.
--
-- Hashing stays where it already lives: Python (tools/cert_formal.py uses
-- hashlib; there is no SQL-side hashing in this codebase). For kind='lean' the
-- existing scalar `sha256` holds a *closure digest* computed by the harness/CLI
-- over the canonical file-digest map, so the drift gate (current == trusted)
-- works unchanged with no SQL hashing and no pgcrypto dependency. The
-- per-file `file_digests` map is stored alongside for transparency/audit.
--
-- Canonical closure-digest recipe (single source of truth, implemented in
-- src/calx/cli.py and re-used by the harness):
--   lines := sorted("<relpath>:<hex_sha256>" for each file in the build closure)
--   closure_digest := sha256( "\n".join(lines) )
--
-- No existing column is altered; every prior artifact row stays valid.
-- Idempotent.

ALTER TABLE cert.artifact ADD COLUMN IF NOT EXISTS project_root TEXT;   -- repo-relative Lake project dir
ALTER TABLE cert.artifact ADD COLUMN IF NOT EXISTS target_decl  TEXT;   -- attested declaration, e.g. Erdos728.main
ALTER TABLE cert.artifact ADD COLUMN IF NOT EXISTS file_digests JSONB;  -- {relpath: hex_sha256} over the build closure
ALTER TABLE cert.artifact ADD COLUMN IF NOT EXISTS toolchain    JSONB;  -- {lean, mathlib_rev, lake}

-- Register (or re-register) a Lean artifact. Wraps cert.register_artifact with
-- kind='lean', path := project_root, and the harness-computed closure digest as
-- sha256. checker_cmd defaults to the repo driver but is overridable (e.g. a
-- sandboxed wrapper for untrusted / AI-supplied proofs).
CREATE OR REPLACE FUNCTION cert.register_lean_artifact(
    p_claim_id       BIGINT,
    p_project_root   TEXT,
    p_target_decl    TEXT,
    p_file_digests   JSONB,
    p_toolchain      JSONB,
    p_closure_digest TEXT,
    p_checker_cmd    TEXT DEFAULT NULL
) RETURNS cert.artifact
LANGUAGE plpgsql AS $$
DECLARE
    v_cmd TEXT := COALESCE(
                    p_checker_cmd,
                    'scripts/lean_check.sh "' || p_project_root || '" "' || p_target_decl || '"'
                  );
    v_row cert.artifact%ROWTYPE;
BEGIN
    -- reuse the base upsert for the shared columns + drift semantics
    PERFORM cert.register_artifact(p_claim_id, 'lean', p_project_root, p_closure_digest, v_cmd);
    -- then set the Lean-specific columns on the same row
    UPDATE cert.artifact
       SET project_root = p_project_root,
           target_decl  = p_target_decl,
           file_digests = p_file_digests,
           toolchain    = p_toolchain
     WHERE claim_id = p_claim_id
    RETURNING * INTO v_row;
    RETURN v_row;
END
$$;

-- Lean attestation standing: latest status joined to the toolchain pin and the
-- attested declaration. The declaration's pretty-printed type is recorded by the
-- harness in the certificate evidence (evidence->>'type').
CREATE OR REPLACE VIEW cert.lean_standing AS
SELECT s.claim_id,
       s.statement,
       s.status,
       s.seq,
       a.project_root,
       a.target_decl,
       a.toolchain,
       s.valid_under->>'artifact_sha256'          AS attested_digest,
       a.sha256                                    AS registered_digest,
       s.checked_at
  FROM cert.standing s
  JOIN cert.artifact a ON a.claim_id = s.claim_id
 WHERE a.kind = 'lean';
