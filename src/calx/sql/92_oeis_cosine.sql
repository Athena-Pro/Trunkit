-- Unified model, step 92: cosine candidate generation for OEIS/sequence matching.
--
-- Complements the EXACT prefix matcher (06_oeis_match) with a scale/shape-
-- invariant pre-filter. Cosine over a log+mean-centred prefix groups sequences
-- by growth profile and — crucially — recovers SCALED/affine variants the exact
-- matcher structurally misses (e.g. 2·Fibonacci). It is a CANDIDATE generator,
-- never the verdict: every candidate is confirmed by exact leading-term
-- agreement before a match is asserted (three-valued, same as everywhere).
--
-- Pure SQL, no extension, no new dependency. The cosine math is identical to the
-- vision layer's (cert.image_cosine); promoted here as a shared calx.vec_cosine.
--
-- HONEST SCOPE: cosine is scale-invariant by construction, so it CANNOT
-- distinguish within a growth class (2^n vs 3^n score ~1.0). Use it to surface
-- candidates, then let exact terms decide. Idempotent.

INSERT INTO cert.method (name, claim_kind, checker_kind, description)
VALUES ('comp_sql', 'computational', 'sql', 'in-DB probe returning (ok, evidence)')
ON CONFLICT (name) DO NOTHING;

-- Generic cosine over equal-length FLOAT8[] (NULL on length mismatch).
CREATE OR REPLACE FUNCTION calx.vec_cosine(a FLOAT8[], b FLOAT8[])
RETURNS FLOAT8 LANGUAGE sql IMMUTABLE AS $$
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

-- Shape descriptor for a term list: v_i = ln(1+|t_i|), then mean-centre, over
-- the first p_k terms. Mean-centring makes cosine == Pearson r of log-growth.
CREATE OR REPLACE FUNCTION calx.vectorize_terms(p_terms NUMERIC[], p_k INT DEFAULT NULL)
RETURNS FLOAT8[] LANGUAGE sql IMMUTABLE AS $$
    WITH t AS (
        SELECT ordinality, ln(1 + abs(val))::float8 AS lv
        FROM unnest(p_terms) WITH ORDINALITY AS u(val, ordinality)
        WHERE ordinality <= COALESCE(p_k, array_length(p_terms, 1))
    ), m AS (SELECT avg(lv) AS a FROM t)
    SELECT array_agg(lv - (SELECT a FROM m) ORDER BY ordinality) FROM t;
$$;

-- Length of the leading run where two term lists agree (the exact-match bridge).
CREATE OR REPLACE FUNCTION calx.terms_prefix_agree(a NUMERIC[], b NUMERIC[])
RETURNS INT LANGUAGE sql IMMUTABLE AS $$
    WITH pa AS (SELECT ordinality AS i, val FROM unnest(a) WITH ORDINALITY AS t(val, ordinality)),
         pb AS (SELECT ordinality AS i, val FROM unnest(b) WITH ORDINALITY AS t(val, ordinality)),
         j  AS (SELECT pa.i AS i, (pa.val = pb.val) AS eq FROM pa JOIN pb USING (i))
    SELECT COALESCE((SELECT min(i) - 1 FROM j WHERE NOT eq),
                    (SELECT count(*) FROM j))::int;
$$;

-- Materialised descriptor per sequence. Stores BOTH the raw prefix terms (for
-- exact confirmation) and the cosine vector. Decoupled from sequence_membership
-- (no FK) so orbits / ad-hoc queries can be vectorised the same way.
CREATE TABLE IF NOT EXISTS calx.seq_vector (
    seq_id      TEXT NOT NULL,
    vector_kind TEXT NOT NULL DEFAULT 'logc',
    k           INT  NOT NULL,
    terms       NUMERIC[] NOT NULL,
    vec         FLOAT8[]  NOT NULL,
    built_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (seq_id, vector_kind)
);

-- Build/refresh a sequence's descriptor from sequence_membership (first p_k terms).
CREATE OR REPLACE FUNCTION calx.build_seq_vector(
    p_seq_id TEXT, p_k INT DEFAULT 16, p_kind TEXT DEFAULT 'logc'
) RETURNS calx.seq_vector LANGUAGE plpgsql AS $$
DECLARE v_terms NUMERIC[]; v_row calx.seq_vector%ROWTYPE;
BEGIN
    SELECT array_agg(n::numeric ORDER BY idx) INTO v_terms
      FROM (SELECT n, idx FROM sequence_membership
             WHERE seq_id = p_seq_id ORDER BY idx LIMIT p_k) s;
    IF v_terms IS NULL THEN
        RAISE EXCEPTION 'build_seq_vector: no membership terms for %', p_seq_id;
    END IF;
    INSERT INTO calx.seq_vector (seq_id, vector_kind, k, terms, vec)
    VALUES (p_seq_id, p_kind, array_length(v_terms,1), v_terms,
            calx.vectorize_terms(v_terms, p_k))
    ON CONFLICT (seq_id, vector_kind) DO UPDATE
        SET k = EXCLUDED.k, terms = EXCLUDED.terms, vec = EXCLUDED.vec, built_at = now()
    RETURNING * INTO v_row;
    RETURN v_row;
END $$;

-- Top-N cosine candidates for a query sequence, each annotated with the exact
-- leading-term agreement length (the confirmation signal).
CREATE OR REPLACE FUNCTION calx.oeis_cosine_candidates(
    p_query TEXT, p_top INT DEFAULT 5, p_kind TEXT DEFAULT 'logc'
) RETURNS TABLE (seq_id TEXT, cosine FLOAT8, exact_prefix INT)
LANGUAGE sql STABLE AS $$
    SELECT b.seq_id,
           calx.vec_cosine(q.vec, b.vec)        AS cosine,
           calx.terms_prefix_agree(q.terms, b.terms) AS exact_prefix
      FROM calx.seq_vector q
      JOIN calx.seq_vector b
        ON b.vector_kind = q.vector_kind AND b.seq_id <> q.seq_id
     WHERE q.seq_id = p_query AND q.vector_kind = p_kind
     ORDER BY cosine DESC NULLS LAST
     LIMIT p_top;
$$;

-- Wire cosine → exact verifier: a comp_sql claim whose probe CONFIRMS the match
-- by exact leading-term agreement (>= p_min_prefix). cosine proposes; this decides.
CREATE OR REPLACE FUNCTION calx.oeis_cosine_match_claim(
    p_query TEXT, p_candidate TEXT, p_min_prefix INT DEFAULT 8, p_kind TEXT DEFAULT 'logc'
) RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE v_stmt TEXT; v_probe TEXT; v_id BIGINT;
BEGIN
    v_stmt := format('sequence %s matches %s (exact prefix >= %s; cosine-proposed)',
                     p_query, p_candidate, p_min_prefix);
    v_probe := format($q$
        SELECT (calx.terms_prefix_agree(q.terms, c.terms) >= %3$s) AS ok,
               jsonb_build_object(
                   'query', %1$L, 'candidate', %2$L,
                   'exact_prefix', calx.terms_prefix_agree(q.terms, c.terms),
                   'min_prefix', %3$s,
                   'cosine', calx.vec_cosine(q.vec, c.vec)
               ) AS evidence
          FROM calx.seq_vector q, calx.seq_vector c
         WHERE q.seq_id = %1$L AND c.seq_id = %2$L
           AND q.vector_kind = %4$L AND c.vector_kind = %4$L
    $q$, p_query, p_candidate, p_min_prefix, p_kind);

    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
    VALUES ('oeis_cosine_match',
            jsonb_build_object('query', p_query, 'candidate', p_candidate,
                               'min_prefix', p_min_prefix),
            v_stmt, 'computational', 'comp_sql', v_probe)
    ON CONFLICT (statement) DO UPDATE SET probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_id;
    RETURN v_id;
END $$;
