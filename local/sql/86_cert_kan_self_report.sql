-- Unified model, step 86: an honest claim over the kan self-report.
--
-- The kan-work sweep found claim #273 ("latest kan_self_report has coverage = 1.0")
-- grey because no kan_self_report constant existed — its generator (local/tools/
-- kan_in_kan.py) had never been run on this DB. Running it now produces a real
-- self-report, but coverage is 13/15 = 0.867, NOT 1.0: two checks are genuine gaps
-- (L30 corpus: 1 of 5 documents loaded; L30 corpus-fts: 0 chunks indexed). So #273's
-- strict "= 1.0" is honestly NOT met and stays unverified.
--
-- This records the verifiable, growth-robust property instead: the self-report exists
-- and is INTERNALLY CONSISTENT (its coverage equals checks_covered / checks_total), and
-- it enumerates its own gaps. It does NOT assert coverage = 1.0 (which would require
-- loading 4 more corpus papers + FTS indexing). Re-run kan_in_kan.py to refresh the
-- report; this claim re-checks the latest version. Idempotent.

INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql)
SELECT 'kan_self_report',
 '{"generator":"local/tools/kan_in_kan.py","supersedes":"the intent of #273 (coverage=1.0, currently 0.867)",
   "property":"self-report exists and coverage = checks_covered/checks_total; gaps enumerated",
   "known_gaps":["L30 corpus (1<5 docs)","L30 corpus-fts (0<1 chunks)"]}'::jsonb,
 'cert: the kan self-report exists and is internally consistent — its coverage equals checks_covered/checks_total, and it enumerates its own gaps (does not assert coverage = 1.0; the corpus is under-provisioned)',
 'computational','comp_sql',
 $p$WITH r AS (
    SELECT convert_from(value,'UTF8')::jsonb AS rpt
    FROM curry.constants WHERE id='kan_self_report' AND retired_at IS NULL
    ORDER BY version DESC LIMIT 1)
  SELECT (r.rpt IS NOT NULL
          AND (r.rpt->>'checks_total')::int > 0
          AND abs((r.rpt->>'coverage')::float
                  - (r.rpt->>'checks_covered')::float / (r.rpt->>'checks_total')::float) < 1e-9) AS ok,
    jsonb_build_object(
      'coverage', r.rpt->>'coverage',
      'checks', (r.rpt->>'checks_covered')||'/'||(r.rpt->>'checks_total'),
      'gaps', (SELECT jsonb_agg(e.key)
                 FROM jsonb_each(r.rpt->'evidence') AS e
                WHERE (e.value->>'got')::int < (e.value->>'need')::int),
      'note','self-report is consistent; coverage<1.0 reflects real gaps, not a generator failure') AS evidence
  FROM r$p$
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement LIKE 'cert: the kan self-report exists and is internally consistent%');

DO $$ DECLARE c RECORD; BEGIN
  FOR c IN SELECT id FROM cert.claim WHERE statement LIKE 'cert: the kan self-report exists and is internally consistent%'
  LOOP PERFORM cert.check(c.id); END LOOP;
END $$;
