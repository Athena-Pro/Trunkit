-- Unified model, step 41: the formal tier of the `cert` pillar.
--
-- Where `cert.check` (40_cert.sql) trusts an in-DB probe, the FORMAL tier
-- trusts an *external* proof artifact whose integrity is hash-pinned. The
-- distinction is epistemic:
--
--   computational claim -> trust root is calx itself (the DB computed it)
--   formal claim        -> trust root is an independent artifact + its sha256,
--                          accepted by an external checker (TEL/Lean/Agda/...)
--
-- `cert.check` cannot shell out; the formal tier is driven by the Python
-- harness tools/cert_formal.py, which writes certificates through the SAME
-- append-only cert.certificate table and curry.inferences provenance.
--
-- Idempotent.

CREATE TABLE IF NOT EXISTS cert.artifact (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    claim_id     BIGINT NOT NULL UNIQUE REFERENCES cert.claim(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,            -- python | lean | agda | tel | file
    path         TEXT NOT NULL,            -- artifact location (repo-relative ok)
    sha256       TEXT,                     -- trusted hash (NULL = trust-on-first-register)
    checker_cmd  TEXT NOT NULL,            -- command whose exit code attests
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Register (or re-register) the artifact backing a formal claim.
-- Re-registration updates the trusted hash — a *legitimate* artifact change;
-- the next harness run will re-attest against the new hash.
CREATE OR REPLACE FUNCTION cert.register_artifact(
    p_claim_id    BIGINT,
    p_kind        TEXT,
    p_path        TEXT,
    p_sha256      TEXT,
    p_checker_cmd TEXT
) RETURNS cert.artifact
LANGUAGE plpgsql AS $$
DECLARE
    v_row cert.artifact%ROWTYPE;
BEGIN
    INSERT INTO cert.artifact (claim_id, kind, path, sha256, checker_cmd)
    VALUES (p_claim_id, p_kind, p_path, p_sha256, p_checker_cmd)
    ON CONFLICT (claim_id) DO UPDATE
        SET kind = EXCLUDED.kind,
            path = EXCLUDED.path,
            sha256 = EXCLUDED.sha256,
            checker_cmd = EXCLUDED.checker_cmd,
            registered_at = now()
    RETURNING * INTO v_row;
    RETURN v_row;
END
$$;

-- Formal/external claims are owned by the harness (tools/cert_formal.py), not
-- by in-DB probes. Redefine cert.check_all() to re-run ONLY probe-driven
-- claims, so it never downgrades a harness-attested formal claim to
-- 'unverified' (its NULL probe_sql would otherwise do exactly that).
CREATE OR REPLACE FUNCTION cert.check_all()
RETURNS TABLE (claim_id BIGINT, statement TEXT, status TEXT)
LANGUAGE sql AS $$
    SELECT c.id, c.statement, (cert.check(c.id)).status
      FROM cert.claim c
     WHERE c.probe_sql IS NOT NULL
     ORDER BY c.id;
$$;

-- Latest formal attestation per claim, joined to its artifact.
CREATE OR REPLACE VIEW cert.formal_standing AS
SELECT s.claim_id,
       s.statement,
       s.status,
       s.seq,
       a.kind,
       a.path,
       a.sha256       AS registered_sha256,
       s.valid_under->>'artifact_sha256' AS attested_sha256,
       s.checked_at
  FROM cert.standing s
  JOIN cert.artifact a ON a.claim_id = s.claim_id;

-- Seed the worked-example formal claim (probe_sql NULL => harness-driven).
INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'number_fact',
       '{"n":28,"property":"perfect"}'::jsonb,
       '28 is a perfect number (independently verified by external checker)',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = '28 is a perfect number (independently verified by external checker)'
);
