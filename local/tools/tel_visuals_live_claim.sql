-- Live calx visualisation claims (Ulam prime spiral + factorization mosaic).
--
-- Each claim attests the full live pipeline for one renderer:
--   calx DB  →  renderer script  →  generated .tel  →  telc  →  PNG
--
-- tools/tel_visuals_live_check.py runs each renderer, verifies the PNG, and
-- writes a verdict into cert.tel_visuals_live.  These claims read it.
--
-- Idempotent.

CREATE TABLE IF NOT EXISTS cert.tel_visuals_live (
    claim_id   integer PRIMARY KEY,
    script     text,
    png_path   text,
    png_bytes  integer,
    status     text,
    detail     text,
    checked_at timestamptz
);

-- Ulam prime spiral
INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
VALUES (
    'tel_visuals_live',
    '{"repo":    "C:/AI-Local/tel-clean",
      "script":  "tel_ulam_render.py",
      "produces":"bootstrap/output/calx_ulam.png",
      "n":       961}'::jsonb,
    'TEL visuals live: tel_ulam_render.py generates a 31² Ulam prime spiral from live calx and telc produces a valid PNG',
    'computational', 'comp_sql', ''
)
ON CONFLICT (statement) DO NOTHING;

-- Factorization mosaic
INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
VALUES (
    'tel_visuals_live',
    '{"repo":    "C:/AI-Local/tel-clean",
      "script":  "tel_mosaic_render.py",
      "produces":"bootstrap/output/calx_mosaic.png",
      "n":       100}'::jsonb,
    'TEL visuals live: tel_mosaic_render.py generates a prime×integer factorization mosaic (n=2..100) from live calx and telc produces a valid PNG',
    'computational', 'comp_sql', ''
)
ON CONFLICT (statement) DO NOTHING;

-- Wire probe_sql for all tel_visuals_live claims
UPDATE cert.claim SET probe_sql = format($f$
    SELECT
        (CASE
            WHEN tvl.status = 'valid'  THEN true
            WHEN tvl.status = 'failed' THEN false
            ELSE NULL
         END) AS ok,
        jsonb_build_object(
            'script',    tvl.script,
            'png_path',  tvl.png_path,
            'png_bytes', tvl.png_bytes,
            'status',    COALESCE(tvl.status, 'not_run'),
            'checked_at',tvl.checked_at,
            'detail',    left(tvl.detail, 200)
        ) AS evidence
    FROM (SELECT %s::int AS cid) q
    LEFT JOIN cert.tel_visuals_live tvl ON tvl.claim_id = q.cid
$f$, id)
WHERE subject_kind = 'tel_visuals_live';
