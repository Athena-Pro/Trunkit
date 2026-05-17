-- Unified model, step 47: the combined signature (difference (+) factorial).
--
-- The two homology lenses are unified into ONE 7-component fingerprint:
--
--   [ H1(d^0), H1(d^1), H1(d^2),            -- difference tower (43)
--     H1(parity), H1(omega),
--     H1(bigomega), H1(shared_prime) ]      -- factorial homology (45)
--
-- The combined key is strictly finer than either lens: a single-lens
-- "false equivalence" is resolved iff the OTHER lens separates the pair.
-- kan.lens_resolution makes that explicit. Pure composition of existing
-- kan signatures -- no new computation. Idempotent.

CREATE OR REPLACE VIEW kan.combined_signature AS
SELECT s.seq_id,
       s.name,
       s.family,
       ds.h1_signature                                   AS diff_signature,
       ARRAY[fs.h1_parity, fs.h1_omega,
             fs.h1_bigomega, fs.h1_shared_prime]          AS fact_signature,
       (ds.h1_signature
        || ARRAY[fs.h1_parity, fs.h1_omega,
                 fs.h1_bigomega, fs.h1_shared_prime])     AS combined_signature
  FROM calx.sequences s
  JOIN kan.sequence_signature  ds ON ds.seq_id = s.seq_id
  JOIN kan.factorial_signature fs ON fs.seq_id = s.seq_id;

-- Pairs that remain equivalent under the FULL combined key. On a corpus the
-- combined invariant fully separates, this view is empty.
CREATE OR REPLACE VIEW kan.combined_similarity AS
SELECT a.seq_id AS seq_a,
       b.seq_id AS seq_b,
       a.combined_signature,
       (a.family = b.family) AS same_family
  FROM kan.combined_signature a
  JOIN kan.combined_signature b
    ON a.combined_signature = b.combined_signature
   AND a.seq_id < b.seq_id;

-- For each pair that EITHER single lens conflates, show whether the other
-- lens resolves it under the combined key (the unification payoff).
CREATE OR REPLACE VIEW kan.lens_resolution AS
SELECT a.seq_id AS seq_a,
       b.seq_id AS seq_b,
       (a.diff_signature = b.diff_signature)         AS diff_equal,
       (a.fact_signature = b.fact_signature)         AS fact_equal,
       (a.combined_signature = b.combined_signature) AS combined_equal,
       CASE
         WHEN a.diff_signature = b.diff_signature
              AND a.combined_signature <> b.combined_signature
           THEN 'factorial lens resolves a difference-tower class'
         WHEN a.fact_signature = b.fact_signature
              AND a.combined_signature <> b.combined_signature
           THEN 'difference lens resolves a factorial class'
         WHEN a.combined_signature = b.combined_signature
           THEN 'combined-equivalent (neither lens separates)'
         ELSE 'distinct under both lenses'
       END AS resolution
  FROM kan.combined_signature a
  JOIN kan.combined_signature b ON a.seq_id < b.seq_id
 WHERE a.diff_signature = b.diff_signature
    OR a.fact_signature = b.fact_signature;
