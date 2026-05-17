-- Unified model, step 57: the strata tower, surfaced from kan.
--
-- tools/build_strata_tower.py registers an abstract category 'tower' and a
-- graded family of rung endofunctors strata_W1..W3 (omega=k) and
-- strata_B1..B4 (Omega=k). prime_members == strata_W1 (the bottom rung).
-- These views read the tower back as object maps plus the SQL-checkable
-- tower laws. Idempotent (CREATE OR REPLACE only).

CREATE OR REPLACE VIEW kan.strata_tower AS
SELECT m.functor,
       substring(m.functor FROM 'strata_(.*)')          AS rung,
       left(split_part(m.functor,'_',2),1)              AS grading, -- W|B
       substring(m.functor FROM 'strata_[WB](\d+)')::int AS k,
       m.src_object                                     AS seq,
       m.tgt_object                                     AS image,
       (SELECT count(*) FROM kan.sequence_terms t
         WHERE t.seq_id=m.src_object)                   AS src_terms,
       (SELECT count(*) FROM kan.sequence_terms t
         WHERE t.seq_id=m.tgt_object)                   AS img_terms,
       (m.src_object LIKE 'T\_%' ESCAPE '\')            AS is_image
  FROM kan.functor_object_map m
 WHERE m.functor LIKE 'strata\_%' ESCAPE '\';

-- Tower-law audit (SQL-checkable parts). all booleans must be TRUE.
--   idempotent  : every rung-image object maps to itself
--   orthogonal  : within the omega-tower, distinct rungs of the SAME base
--                 share no term (W_j(S) and W_k(S) are disjoint for j!=k)
--   bottom_rung : strata_W1 and prime_members agree on every shared base
CREATE OR REPLACE VIEW kan.strata_tower_laws AS
WITH idem AS (
  SELECT count(*) AS bad FROM kan.functor_object_map
   WHERE functor LIKE 'strata\_%' ESCAPE '\'
     AND src_object LIKE 'T\_%' ESCAPE '\'
     AND src_object <> tgt_object
),
ortho AS (
  -- any term shared between two different omega-rung images of one base
  SELECT count(*) AS bad
    FROM kan.strata_tower a
    JOIN kan.strata_tower b
      ON a.grading='W' AND b.grading='W' AND a.seq=b.seq AND a.k<b.k
       AND NOT a.is_image
    JOIN kan.sequence_terms ta ON ta.seq_id=a.image
    JOIN kan.sequence_terms tb ON tb.seq_id=b.image AND tb.term=ta.term
),
bottom AS (
  SELECT count(*) AS bad
    FROM kan.functor_object_map w
    JOIN kan.functor_object_map p
      ON p.functor='prime_members' AND p.src_object=w.src_object
   WHERE w.functor='strata_W1' AND w.src_object NOT LIKE 'T\_%' ESCAPE '\'
     AND (SELECT array_agg(term ORDER BY idx) FROM kan.sequence_terms
           WHERE seq_id=w.tgt_object)
      IS DISTINCT FROM
         (SELECT array_agg(term ORDER BY idx) FROM kan.sequence_terms
           WHERE seq_id=p.tgt_object)
)
SELECT (SELECT bad FROM idem)   = 0 AS idempotent,
       (SELECT bad FROM ortho)  = 0 AS orthogonal,
       (SELECT bad FROM bottom) = 0 AS bottom_rung,
       (SELECT count(DISTINCT functor) FROM kan.strata_tower) AS rung_functors;
