-- TEL constants drift claim (capability-tree T1).
--
-- Closes the last baked-not-verified TEL surface: an aggregate claim that the
-- seeded curry.constants type/signature definitions still match current
-- tel-clean source. tools/tel_constants_check.py extracts each symbol's
-- structural core from hir_*.rs, compares to the stored value, and writes
-- per-constant verdicts into cert.tel_constants; this claim reads them.
--
-- Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
VALUES (
  'tel_constants',
  '{"repo":"C:/AI-Local/tel-clean","manifest":"tools/tel_constants_manifest.json","n":23}'::jsonb,
  'TEL constants: all seeded type/signature constants match current hir_*.rs source (no drift)',
  'computational', 'comp_sql', '')
ON CONFLICT (statement) DO NOTHING;

UPDATE cert.claim SET probe_sql = $f$
  SELECT (CASE WHEN bool_or(status='drift')   THEN false
               WHEN bool_or(status='missing') THEN NULL
               WHEN count(*) = 0              THEN NULL
               ELSE true END) AS ok,
         jsonb_build_object(
           'total',   count(*),
           'match',   count(*) FILTER (WHERE status='match'),
           'drift',   count(*) FILTER (WHERE status='drift'),
           'missing', count(*) FILTER (WHERE status='missing'),
           'drifted', COALESCE(jsonb_agg(const_id) FILTER (WHERE status='drift'), '[]'::jsonb)
         ) AS evidence
    FROM cert.tel_constants
$f$
WHERE subject_kind = 'tel_constants';
