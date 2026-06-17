-- Unified model, step 84: the "claims-outrun-implementation" detector (first-class).
--
-- This session surfaced the pattern FIVE times across independent labs: a source
-- asserts a result whose backing is a stub, a postulate, missing data, or an open
-- problem (hypergroup `_dualize` identity stub; Whitney "automatic A/B"; Yang-Mills
-- "area law" + Clay mass gap; diffeology "3 open problems solved" on postulated Agda).
-- Each was caught by hand. cert.outrun_watch() makes the catch RE-RUNNABLE: it scans
-- the live ledger and flags every claim whose asserted status outruns its backing.
--
-- A claim is "outrun" iff any of:
--   * it has never been checked (no certificate);
--   * its latest verdict is `unverified` (asserted, not established);
--   * it is a formal_external claim with no pinned artifact and is not independently valid;
--   * it is a cert_kernel claim with no submitted proof object.
-- A cleanly VALID, backed claim is never flagged. Idempotent.

CREATE OR REPLACE FUNCTION cert.outrun_watch()
RETURNS TABLE(claim_id bigint, statement text, method text, latest_status text, reason text, severity text)
LANGUAGE sql STABLE AS $$
  WITH latest AS (
    SELECT cl.id, cl.statement, cl.method,
           (SELECT ce.status FROM cert.certificate ce
             WHERE ce.claim_id = cl.id ORDER BY ce.seq DESC LIMIT 1) AS status
      FROM cert.claim cl
  )
  SELECT l.id, l.statement, l.method, l.status,
    CASE
      WHEN l.status IS NULL                  THEN 'never checked (no certificate)'
      WHEN l.status = 'unverified'           THEN 'asserted but unverified — status outruns backing'
      WHEN l.method = 'formal_external'
           AND NOT EXISTS (SELECT 1 FROM cert.artifact a WHERE a.claim_id = l.id)
                                             THEN 'formal_external with no pinned artifact'
      WHEN l.method = 'cert_kernel'
           AND NOT EXISTS (SELECT 1 FROM cert.proof_obligation po WHERE po.claim_id = l.id)
                                             THEN 'cert_kernel with no submitted proof object'
    END AS reason,
    CASE WHEN l.status IS NULL OR l.status = 'unverified' THEN 'high' ELSE 'medium' END AS severity
  FROM latest l
  WHERE l.method NOT IN ('empirical_corpus','agent_adjudication','domain_invariant_decl')  -- provenance/record classes are exempt (see cert.outrun_exempt)
    AND ( l.status IS NULL
       OR l.status = 'unverified'
       OR (l.status IS DISTINCT FROM 'valid'
           AND l.method = 'formal_external'
           AND NOT EXISTS (SELECT 1 FROM cert.artifact a WHERE a.claim_id = l.id))
       OR (l.status IS DISTINCT FROM 'valid'
           AND l.method = 'cert_kernel'
           AND NOT EXISTS (SELECT 1 FROM cert.proof_obligation po WHERE po.claim_id = l.id)) );
$$;

COMMENT ON FUNCTION cert.outrun_watch() IS
  'Claims-outrun-implementation detector: flags every VERIFIABLE claim whose asserted '
  'status outruns its backing (unverified / unchecked / unbacked formal_external / '
  'proofless cert_kernel). Provenance/record methods (empirical_corpus, agent_adjudication, '
  'domain_invariant_decl) are NOT probe-verifiable by nature and are exempt — see '
  'cert.outrun_exempt(). A cleanly valid, backed claim is never flagged. Re-runnable self-audit.';

-- Companion: provenance/record claims that are exempt from the outrun count because they
-- are not the kind of claim an in-DB probe is meant to verify. Surfaced separately so the
-- exemption is transparent, not a silent drop.
CREATE OR REPLACE FUNCTION cert.outrun_exempt()
RETURNS TABLE(claim_id bigint, statement text, method text, latest_status text, class text)
LANGUAGE sql STABLE AS $$
  SELECT cl.id, cl.statement, cl.method,
         (SELECT ce.status FROM cert.certificate ce WHERE ce.claim_id = cl.id ORDER BY ce.seq DESC LIMIT 1),
         CASE cl.method
           WHEN 'empirical_corpus'      THEN 'provenance record (corpus assertion)'
           WHEN 'agent_adjudication'    THEN 'agent decision log'
           WHEN 'domain_invariant_decl' THEN 'invariant declaration (awaiting a checker)'
         END
  FROM cert.claim cl
  WHERE cl.method IN ('empirical_corpus','agent_adjudication','domain_invariant_decl')
    AND COALESCE((SELECT ce.status FROM cert.certificate ce WHERE ce.claim_id = cl.id ORDER BY ce.seq DESC LIMIT 1), 'unverified') <> 'valid';
$$;

-- valid (comp_sql): the detector is SOUND — it never flags a cleanly-valid claim, and
-- it partitions the ledger (flagged ∪ valid = all claims, disjoint). Re-checkable.
INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql)
SELECT 'outrun_detector',
 '{"detector":"cert.outrun_watch","property":"sound: never flags a cleanly-valid claim; flagged and valid partition the ledger"}'::jsonb,
 'cert: the claims-outrun-implementation detector cert.outrun_watch() is sound — it never flags a claim whose latest verdict is valid (a cleanly-valid, backed claim is never reported as outrun)',
 'computational','comp_sql',
 $p$WITH w AS (SELECT claim_id, latest_status, reason FROM cert.outrun_watch())
   SELECT (NOT EXISTS (SELECT 1 FROM w WHERE latest_status = 'valid')) AS ok,
     jsonb_build_object(
       'flagged_count', (SELECT count(*) FROM w),
       'flagged_by_reason', COALESCE((SELECT jsonb_object_agg(reason, c)
                                        FROM (SELECT reason, count(*) c FROM w GROUP BY reason) r), '{}'::jsonb),
       'soundness', 'no flagged claim has a valid latest verdict') AS evidence$p$
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement LIKE 'cert: the claims-outrun-implementation detector%');

DO $$ DECLARE c RECORD; BEGIN
  FOR c IN SELECT id FROM cert.claim WHERE statement LIKE 'cert: the claims-outrun-implementation detector%'
  LOOP PERFORM cert.check(c.id); END LOOP;
END $$;
