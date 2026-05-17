-- Unified model, step 55: the prime-members functor, surfaced from kan.
--
-- tools/prime_members_functor.py registers an abstract category 'seq' (objects
-- = all sequences) and a total idempotent endofunctor 'prime_members'
-- (S |-> PM(S) = the omega=1 / atomic terms of S; identity on its image).
-- These views read that structure back as the object map plus its laws.
-- Idempotent (CREATE OR REPLACE only).

-- The functor's object map with source/image term counts and properties.
CREATE OR REPLACE VIEW kan.prime_members_functor AS
SELECT m.src_object                                   AS seq,
       m.tgt_object                                   AS prime_members,
       (SELECT count(*) FROM kan.sequence_terms t
         WHERE t.seq_id = m.src_object)                AS src_terms,
       (SELECT count(*) FROM kan.sequence_terms t
         WHERE t.seq_id = m.tgt_object)                AS pm_terms,
       (m.src_object = m.tgt_object)                   AS is_fixed_point,
       (m.src_object LIKE 'PM\_%' ESCAPE '\')          AS is_image
  FROM kan.functor_object_map m
 WHERE m.functor = 'prime_members';

-- Fixed points of PM: sequences S with PM(S) ISOMORPHIC to S (same ordered
-- terms) -- not object-id equality, since PM(S) is the distinct object PM_S.
-- Categorically: S is a fixed point iff S ≅ PM(S). Primes and the omega=1
-- strata live here (every term already a prime power).
CREATE OR REPLACE VIEW kan.prime_members_fixed AS
SELECT m.src_object AS seq
  FROM kan.functor_object_map m
 WHERE m.functor = 'prime_members'
   AND m.src_object NOT LIKE 'PM\_%' ESCAPE '\'
   AND (SELECT array_agg(term ORDER BY idx) FROM kan.sequence_terms
         WHERE seq_id = m.src_object)
     = (SELECT array_agg(term ORDER BY idx) FROM kan.sequence_terms
         WHERE seq_id = m.tgt_object)
 ORDER BY m.src_object;

-- Functor-law audit: one row, all booleans must be TRUE.
--   total      : every 'seq' object has an image
--   idempotent : every image object maps to itself
--   well_typed : (checked by the cert; recorded here as the image set)
CREATE OR REPLACE VIEW kan.prime_members_laws AS
SELECT
  (SELECT count(*) FROM kan.object o
     WHERE o.category = 'seq'
       AND NOT EXISTS (SELECT 1 FROM kan.functor_object_map m
                        WHERE m.functor = 'prime_members'
                          AND m.src_object = o.name)) = 0          AS total,
  (SELECT count(*) FROM kan.functor_object_map m
     WHERE m.functor = 'prime_members'
       AND m.src_object LIKE 'PM\_%' ESCAPE '\'
       AND m.src_object <> m.tgt_object) = 0                       AS idempotent,
  (SELECT count(*) FROM kan.functor_object_map
     WHERE functor = 'prime_members')                              AS object_map_size,
  (SELECT count(*) FROM kan.prime_members_fixed)                   AS fixed_points;
