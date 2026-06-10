-- TEL behavioral claims (capability-tree T1).
--
-- Turns manually-discovered behavioral TODOs into re-runnable ledger claims, so
-- "what's the next method/TODO" becomes a query instead of a manual dig. Mirrors
-- the tel_project live-build pattern: tools/tel_behavior_check.py runs the TEL
-- interpreter and writes a verdict into cert.tel_behavior; each claim's probe_sql
-- reads it three-valued (valid / refuted / unverified).
--
-- Idempotent: safe to re-run.

CREATE TABLE IF NOT EXISTS cert.tel_behavior (
    claim_id   integer PRIMARY KEY,
    program    text,
    expect     text,
    status     text,
    detail     text,
    checked_at timestamptz
);

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
VALUES
 ('tel_behavior',
  '{"repo":"C:/AI-Local/tel-clean","program":"tests/behavior/rec_fact.tel","expect":"Return value: 120"}'::jsonb,
  'TEL behavior: recursive factorial fact(5) evaluates to 120 under the interpreter',
  'computational', 'comp_sql', ''),
 ('tel_behavior',
  '{"repo":"C:/AI-Local/tel-clean","program":"tests/behavior/while_count.tel","expect":"Return value: 3"}'::jsonb,
  'TEL behavior: a while-loop with a body-mutated counter terminates and returns 3',
  'computational', 'comp_sql', ''),
 ('tel_behavior',
  '{"repo":"C:/AI-Local/tel-clean","program":"tests/behavior/array_index.tel","expect":"Return value: 20"}'::jsonb,
  'TEL behavior: indexed array read xs[1] returns 20 under the interpreter',
  'computational', 'comp_sql', '')
ON CONFLICT (statement) DO NOTHING;

-- Bake each claim's own id + expected substring into its probe_sql.
UPDATE cert.claim c SET probe_sql = format(
  $f$SELECT (CASE WHEN tb.status='valid'  THEN true
                  WHEN tb.status='failed' THEN false
                  ELSE NULL END) AS ok,
            jsonb_build_object('program', tb.program, 'expect', %L,
              'status', COALESCE(tb.status,'not_run'),
              'checked_at', tb.checked_at, 'detail', left(tb.detail,200)) AS evidence
       FROM (SELECT %s::int AS cid) q
       LEFT JOIN cert.tel_behavior tb ON tb.claim_id = q.cid$f$,
  c.subject_ref->>'expect', c.id)
WHERE c.subject_kind = 'tel_behavior';
