-- TEL calx live-render claim (capability-tree T1).
--
-- Distinguishes what claim 266 (tel_graphics) attests — a committed .tel
-- snapshot — from what this claim attests: the full live bridge
--   calx DB  →  tel_calx_render.py  →  calx_render.tel  →  telc  →  PNG
--
-- tools/tel_calx_live_check.py runs the render script, verifies the PNG,
-- and writes a verdict into cert.tel_calx_live.  This claim reads it.
--
-- Idempotent.

CREATE TABLE IF NOT EXISTS cert.tel_calx_live (
    claim_id   integer PRIMARY KEY,
    n          integer,
    tel_path   text,
    png_path   text,
    png_bytes  integer,
    status     text,
    detail     text,
    checked_at timestamptz
);

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
VALUES (
    'tel_calx_live',
    '{"repo":   "C:/AI-Local/tel-clean",
      "script": "tools/tel_calx_render.py",
      "produces":"bootstrap/output/calx_divisors.png",
      "n":       96}'::jsonb,
    'TEL calx live: tel_calx_render.py regenerates calx_render.tel from the live DB and telc produces a valid PNG',
    'computational', 'comp_sql', ''
)
ON CONFLICT (statement) DO NOTHING;

-- Wire the probe_sql to read from cert.tel_calx_live
UPDATE cert.claim SET probe_sql = format($f$
    SELECT
        (CASE
            WHEN tcl.status = 'valid'      THEN true
            WHEN tcl.status = 'failed'     THEN false
            ELSE NULL
         END) AS ok,
        jsonb_build_object(
            'n',          tcl.n,
            'tel_path',   tcl.tel_path,
            'png_path',   tcl.png_path,
            'png_bytes',  tcl.png_bytes,
            'status',     COALESCE(tcl.status, 'not_run'),
            'checked_at', tcl.checked_at,
            'detail',     left(tcl.detail, 200)
        ) AS evidence
    FROM (SELECT %s::int AS cid) q
    LEFT JOIN cert.tel_calx_live tcl ON tcl.claim_id = q.cid
$f$, id)
WHERE subject_kind = 'tel_calx_live';
