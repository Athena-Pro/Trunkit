-- Unified model, step 96: probe sandbox — close CWE-89/94 (SECURITY.md design A).
--
-- A cert probe (cert.claim.probe_sql) is SQL the checker EXECUTEs. Run as the
-- caller, a malicious or careless probe is arbitrary code execution in the
-- backend (read files, write tables, DROP, run forever). This step confines
-- every probe to: read-only, time-limited, allowlisted schemas, no dangerous
-- builtins — without changing the verdict contract (ok BOOLEAN, evidence JSONB
-- [, witness JSONB]).
--
-- Strategy (defense in depth):
--   1. A NOLOGIN role `cert_probe` with the minimum it needs (SELECT on the
--      read surface; nothing on cert/curry; no superuser builtins).
--   2. cert.run_probe(sql, n_cols) — the ONE place a probe is EXECUTEd. It sets
--      LOCAL role + read-only + timeout + pinned search_path inside the existing
--      subtransaction, runs the probe, and RESETs. The four call sites delegate
--      here instead of bare EXECUTE.
--   3. A BEFORE INSERT tripwire on cert.claim that rejects probes containing
--      obviously dangerous tokens (pg_read_file, COPY, DDL, pg_sleep, …). This
--      is a smoke alarm, not the wall — the wall is (1)+(2).
--
-- Policy invariant (the durable fix, enforced in §4): untrusted facts are
-- submitted as cert_kernel DATA witnesses (cert.submit_proof), never as
-- probe_sql. comp_sql/struct_kan authorship is privileged.
--
-- Idempotent. Requires steps 40/86/88/94 (the probe sites) to exist.

-- ---------------------------------------------------------------------------
-- 1. least-privilege probe role
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cert_probe') THEN
        CREATE ROLE cert_probe NOLOGIN;
    END IF;
END $$;

-- Read surface only. Probes legitimately read calx data and kan structure;
-- they must NOT see/modify the ledger or provenance.
GRANT USAGE ON SCHEMA calx, kan, curry, cert TO cert_probe;

REVOKE ALL ON ALL TABLES IN SCHEMA cert, curry FROM cert_probe;
GRANT SELECT ON ALL TABLES IN SCHEMA calx, kan TO cert_probe;
-- Allow reading the kan/cert helper functions a probe may legitimately call,
-- but not the dangerous superuser builtins (those are not in these schemas).
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA calx, kan TO cert_probe;

-- Future tables in the read schemas stay readable; future cert/curry tables stay off.
ALTER DEFAULT PRIVILEGES IN SCHEMA calx, kan GRANT SELECT ON TABLES TO cert_probe;

-- Belt-and-suspenders: revoke filesystem/program builtins from PUBLIC reach for
-- this role's session (cannot REVOKE from pg_catalog generally; rely on role
-- not being superuser + read-only tx + denylist tripwire in §3).
COMMENT ON ROLE cert_probe IS
    'Least-privilege role used by cert.run_probe to execute cert.claim.probe_sql: '
    'SELECT on calx/kan only, no cert/curry access, no LOGIN, no superuser. '
    'See SECURITY.md design A.';

-- ---------------------------------------------------------------------------
-- 2. the single sandboxed probe executor
-- ---------------------------------------------------------------------------

-- Two-column probes (comp_sql / struct_kan): returns (ok, evidence).
CREATE OR REPLACE FUNCTION cert.run_probe(p_sql TEXT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql AS $$
BEGIN
    -- Confined to THIS subtransaction; RESET on exit/exception.
    SET LOCAL statement_timeout = '5s';
    SET LOCAL default_transaction_read_only = on;
    SET LOCAL search_path = calx, kan, pg_catalog;
    SET LOCAL ROLE cert_probe;
    BEGIN
        RETURN QUERY EXECUTE p_sql;
    EXCEPTION WHEN OTHERS THEN
        RESET ROLE;
        RAISE;                       -- caller's subtransaction maps this to 'error'
    END;
    RESET ROLE;
END $$;

COMMENT ON FUNCTION cert.run_probe(TEXT) IS
    'Sandboxed executor for 2-column probes (ok BOOLEAN, evidence JSONB). '
    'Runs as role cert_probe, read-only, 5s timeout, search_path calx/kan only. '
    'The ONLY blessed place to EXECUTE a comp_sql/struct_kan probe (CWE-89).';

-- Three-column probes (witness_carry): returns (ok, evidence, witness).
CREATE OR REPLACE FUNCTION cert.run_probe3(p_sql TEXT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB, witness JSONB)
LANGUAGE plpgsql AS $$
BEGIN
    SET LOCAL statement_timeout = '5s';
    SET LOCAL default_transaction_read_only = on;
    SET LOCAL search_path = calx, kan, pg_catalog;
    SET LOCAL ROLE cert_probe;
    BEGIN
        RETURN QUERY EXECUTE p_sql;
    EXCEPTION WHEN OTHERS THEN
        RESET ROLE;
        RAISE;
    END;
    RESET ROLE;
END $$;

COMMENT ON FUNCTION cert.run_probe3(TEXT) IS
    'Sandboxed executor for 3-column witness_carry probes (ok, evidence, witness). '
    'Same confinement as cert.run_probe. See SECURITY.md design A.';

-- ---------------------------------------------------------------------------
-- 3. insert-time tripwire (smoke alarm, not the wall)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION cert.reject_dangerous_probe() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    bad TEXT;
    -- case-insensitive denylist of tokens no legitimate read-only probe needs
    patterns TEXT[] := ARRAY[
        'pg_read_file', 'pg_read_binary_file', 'pg_ls_dir', 'pg_stat_file',
        'lo_import', 'lo_export', 'copy ', 'dblink', 'pg_sleep',
        'pg_terminate_backend', 'pg_cancel_backend',
        'drop ', 'truncate ', 'alter ', 'grant ', 'revoke ',
        'create ', 'insert ', 'update ', 'delete '
    ];
BEGIN
    IF NEW.probe_sql IS NULL THEN
        RETURN NEW;
    END IF;
    FOREACH bad IN ARRAY patterns LOOP
        IF position(bad IN lower(NEW.probe_sql)) > 0 THEN
            RAISE EXCEPTION 'probe rejected: contains forbidden token %', bad
                USING HINT = 'Read-only SELECT probes only. For untrusted facts '
                             'use cert_kernel data witnesses (cert.submit_proof), '
                             'not probe_sql. See SECURITY.md design A.';
        END IF;
    END LOOP;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS cert_claim_probe_guard ON cert.claim;
CREATE TRIGGER cert_claim_probe_guard BEFORE INSERT OR UPDATE ON cert.claim
    FOR EACH ROW EXECUTE FUNCTION cert.reject_dangerous_probe();

-- ---------------------------------------------------------------------------
-- 4. policy invariant: cert_kernel claims carry DATA, never code
-- ---------------------------------------------------------------------------

-- A cert_kernel claim must NOT have a probe_sql (its evidence is a data witness
-- checked by an in-DB kernel). Enforce so the untrusted tier can never smuggle
-- code through the probe path.
CREATE OR REPLACE FUNCTION cert.enforce_kernel_is_data() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.method = 'cert_kernel' AND NEW.probe_sql IS NOT NULL THEN
        RAISE EXCEPTION 'cert_kernel claims must not carry probe_sql (data, not code)'
            USING HINT = 'Submit a witness via cert.submit_proof; the kernel checks it.';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS cert_claim_kernel_data_only ON cert.claim;
CREATE TRIGGER cert_claim_kernel_data_only BEFORE INSERT OR UPDATE ON cert.claim
    FOR EACH ROW EXECUTE FUNCTION cert.enforce_kernel_is_data();

-- ---------------------------------------------------------------------------
-- 5. migration note (call-site rewiring — applied in steps 40/86/88/94)
-- ---------------------------------------------------------------------------
-- After this step is applied, the four probe-EXECUTE sites should delegate:
--     -- was: EXECUTE v_claim.probe_sql INTO v_ok, v_evidence;
--     SELECT ok, evidence INTO v_ok, v_evidence FROM cert.run_probe(v_claim.probe_sql);
--   and the witness_carry site (88) -> cert.run_probe3(...).
-- Those edits live in their own files (they change existing functions); this
-- step provides the sandbox they call. Until rewired, run_probe* is available
-- and the tripwire + role exist, but bare EXECUTE sites remain the active path
-- (documented KNOWN GAP in AUDIT.md §9A until the rewire lands).

COMMENT ON FUNCTION cert.reject_dangerous_probe() IS
    'BEFORE INSERT/UPDATE tripwire on cert.claim: rejects probes with filesystem/'
    'DDL/DML/sleep tokens. Defense-in-depth only; the boundary is cert.run_probe '
    '(role + read-only + timeout). See SECURITY.md design A.';
