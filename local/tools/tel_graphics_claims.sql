-- TEL graphic-representation capability claim (capability-tree).
--
-- TEL ships a Canvas/Color drawing API (Canvas::new, clear, draw_line/circle/
-- rectangle, set_pixel, save_png). This claim verifies it works end-to-end: a
-- .tel program builds a canvas, draws, and saves a PNG; tel_behavior_check.py
-- runs it and confirms a real PNG artifact was (re)produced. Verdict lands in
-- cert.tel_behavior (shared with behavioral claims); this claim reads it.
--
-- Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
VALUES (
  'tel_graphics',
  '{"repo":"C:/AI-Local/tel-clean","program":"tests/behavior/canvas_smoke.tel",
    "expect":"Return value: 0","produces":"bootstrap/output/tel_graphic_smoke.png"}'::jsonb,
  'TEL graphics: a .tel program draws to a Canvas and saves a valid PNG end-to-end',
  'computational', 'comp_sql', '')
ON CONFLICT (statement) DO NOTHING;

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
WHERE c.subject_kind = 'tel_graphics';
