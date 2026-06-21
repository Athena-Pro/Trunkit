-- Unified model, step 91: image anchoring + perceptual similarity (vision layer).
--
-- Two capabilities, both dependency-light and re-verifiable:
--
--   (a) ANCHORING  — register a scientific image / figure / explicit construction
--       by its sha256 (exact integrity) plus a small deterministic descriptor
--       vector. The registry row is the carried, hash-pinned artifact.
--
--   (b) SIMILARITY — cosine similarity between two descriptor vectors, computed
--       in PURE SQL (no pgvector, no extension). A "match" attestation is just a
--       standard comp_sql cert.claim, so it re-verifies via cert.check and travels
--       in export bundles with ZERO changes to the cert/bundle machinery.
--
-- The descriptor is intentionally NOT a deep-model embedding (that would be a
-- multi-GB dependency and non-deterministic across versions). It is a downscaled,
-- mean-centred grayscale vector (default 16x16 = 256 dims, vector_kind
-- 'gray16c'), produced out-of-package by tools/image_features.py. Cosine over it
-- ~ Pearson correlation of layout/intensity: good for "is this the same figure,
-- possibly re-rendered/rescaled", honest about not being semantic understanding.
--
-- Idempotent.

-- Ensure the computational method tier exists (seeded in 40-series normally).
INSERT INTO cert.method (name, claim_kind, checker_kind, description)
VALUES ('comp_sql', 'computational', 'sql', 'in-DB probe returning (ok, evidence)')
ON CONFLICT (name) DO NOTHING;

-- Registry of anchored images. Unique per (sha256, descriptor scheme) so the
-- same image may carry more than one descriptor kind over time.
CREATE TABLE IF NOT EXISTS cert.image_artifact (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sha256        TEXT NOT NULL,              -- exact-integrity anchor
    vector_kind   TEXT NOT NULL,             -- descriptor scheme, e.g. 'gray16c'
    dims          INTEGER NOT NULL,          -- length of vector (sanity/guard)
    vector        FLOAT8[] NOT NULL,         -- the descriptor
    width         INTEGER,
    height        INTEGER,
    label         TEXT,                      -- human tag (e.g. 'erdos1153 fig2')
    meta          JSONB NOT NULL DEFAULT '{}'::jsonb,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (sha256, vector_kind)
);

-- Pure-SQL cosine similarity over equal-length FLOAT8[] vectors.
-- Returns NULL on length mismatch; 0 when either vector has zero magnitude.
CREATE OR REPLACE FUNCTION cert.image_cosine(a FLOAT8[], b FLOAT8[])
RETURNS FLOAT8
LANGUAGE sql IMMUTABLE AS $$
    WITH pa AS (SELECT ordinality AS i, val FROM unnest(a) WITH ORDINALITY AS t(val, ordinality)),
         pb AS (SELECT ordinality AS i, val FROM unnest(b) WITH ORDINALITY AS t(val, ordinality)),
         j  AS (SELECT pa.val AS av, pb.val AS bv FROM pa JOIN pb USING (i))
    SELECT CASE
             WHEN array_length(a,1) IS DISTINCT FROM array_length(b,1) THEN NULL
             WHEN sqrt(sum(av*av)) = 0 OR sqrt(sum(bv*bv)) = 0 THEN 0
             ELSE sum(av*bv) / (sqrt(sum(av*av)) * sqrt(sum(bv*bv)))
           END
    FROM j;
$$;

-- Register (or re-register) an anchored image. Upsert on (sha256, vector_kind):
-- a re-register with a different vector is a legitimate descriptor refresh.
CREATE OR REPLACE FUNCTION cert.register_image(
    p_sha256      TEXT,
    p_vector_kind TEXT,
    p_vector      FLOAT8[],
    p_width       INTEGER DEFAULT NULL,
    p_height      INTEGER DEFAULT NULL,
    p_label       TEXT    DEFAULT NULL,
    p_meta        JSONB   DEFAULT '{}'::jsonb
) RETURNS cert.image_artifact
LANGUAGE plpgsql AS $$
DECLARE v_row cert.image_artifact%ROWTYPE;
BEGIN
    INSERT INTO cert.image_artifact
        (sha256, vector_kind, dims, vector, width, height, label, meta)
    VALUES
        (p_sha256, p_vector_kind, COALESCE(array_length(p_vector,1),0),
         p_vector, p_width, p_height, p_label, p_meta)
    ON CONFLICT (sha256, vector_kind) DO UPDATE
        SET dims = EXCLUDED.dims, vector = EXCLUDED.vector,
            width = EXCLUDED.width, height = EXCLUDED.height,
            label = COALESCE(EXCLUDED.label, cert.image_artifact.label),
            meta = EXCLUDED.meta, registered_at = now()
    RETURNING * INTO v_row;
    RETURN v_row;
END
$$;

-- Build a re-checkable match attestation between two registered images as a
-- standard comp_sql claim. The probe is self-contained (ids + threshold inline),
-- so cert.check / bundle re-verification work unchanged. Three-valued by design:
--   cosine >= threshold -> valid ; cosine < threshold -> refuted ;
--   (a NULL cosine from a descriptor-kind/length mismatch) -> unverified.
CREATE OR REPLACE FUNCTION cert.image_match_claim(
    p_candidate_id BIGINT,
    p_reference_id BIGINT,
    p_threshold    FLOAT8 DEFAULT 0.95,
    p_label        TEXT   DEFAULT NULL
) RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE
    v_stmt  TEXT;
    v_probe TEXT;
    v_id    BIGINT;
BEGIN
    v_stmt := format(
        'image #%s matches reference #%s (cosine >= %s)%s',
        p_candidate_id, p_reference_id, p_threshold,
        CASE WHEN p_label IS NOT NULL THEN ' — '||p_label ELSE '' END
    );
    v_probe := format($q$
        SELECT (cert.image_cosine(c.vector, r.vector) >= %1$s) AS ok,
               jsonb_build_object(
                   'cosine', cert.image_cosine(c.vector, r.vector),
                   'threshold', %1$s::float8,
                   'candidate_id', %2$s, 'reference_id', %3$s,
                   'candidate_sha256', c.sha256, 'reference_sha256', r.sha256,
                   'vector_kind', c.vector_kind
               ) AS evidence
          FROM cert.image_artifact c, cert.image_artifact r
         WHERE c.id = %2$s AND r.id = %3$s
           AND c.vector_kind = r.vector_kind
    $q$, p_threshold, p_candidate_id, p_reference_id);

    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
    VALUES ('image_match',
            jsonb_build_object('candidate_id', p_candidate_id,
                               'reference_id', p_reference_id,
                               'threshold', p_threshold),
            v_stmt, 'computational', 'comp_sql', v_probe)
    ON CONFLICT (statement) DO UPDATE SET probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

-- Convenience: anchored images with their latest match-claim standing (if any).
CREATE OR REPLACE VIEW cert.image_standing AS
SELECT ia.id        AS image_id,
       ia.sha256,
       ia.vector_kind,
       ia.dims,
       ia.label,
       ia.width, ia.height,
       ia.registered_at
  FROM cert.image_artifact ia
 ORDER BY ia.id;
