-- Unified model, step 45: factorial homology of sequences.
--
-- Where 43 took gap-pattern H1 of the DIFFERENCE tower, this takes gap-pattern
-- H1 of the FACTORIZATION-feature streams of each sequence -- realising the
-- four structural signals as homology axes:
--
--   parity        : (t mod 2) stream            -- even/odd parity structure
--   omega         : omega(t) stream             -- # UNIQUE prime factors
--   bigomega      : Omega(t) stream             -- # TOTAL prime factors
--   shared_prime  : graph, t_i ~ t_j iff they share a prime factor
--                   (the "interleaved primes" structure; H1 = prime cycles)
--
-- omega/Omega use calx.factorizations for t in 2..100 (authoritative bedrock);
-- larger t use a bounded factorizer and, if the cofactor exceeds budget, the
-- term is marked unfactored and dropped from the omega/Omega streams. Parity
-- is always exact. Idempotent.

CREATE TABLE IF NOT EXISTS kan.sequence_factorial_homology (
    seq_id       TEXT    NOT NULL REFERENCES calx.sequences(seq_id) ON DELETE CASCADE,
    axis         TEXT    NOT NULL,   -- parity | omega | bigomega | shared_prime
    n_vertices   INTEGER NOT NULL,
    n_edges      INTEGER NOT NULL,
    n_squares    INTEGER NOT NULL,   -- 0 for the shared_prime graph axis
    h1           INTEGER NOT NULL,
    n_terms      INTEGER NOT NULL,   -- terms considered for this axis
    n_unfactored INTEGER NOT NULL,   -- terms beyond the factoring budget
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (seq_id, axis)
);

-- Factorial H1 signature, one labeled vector per sequence (fixed axis order).
CREATE OR REPLACE VIEW kan.factorial_signature AS
SELECT h.seq_id,
       s.name,
       s.family,
       max(h.h1) FILTER (WHERE h.axis='parity')       AS h1_parity,
       max(h.h1) FILTER (WHERE h.axis='omega')         AS h1_omega,
       max(h.h1) FILTER (WHERE h.axis='bigomega')      AS h1_bigomega,
       max(h.h1) FILTER (WHERE h.axis='shared_prime')  AS h1_shared_prime,
       max(h.n_unfactored) FILTER (WHERE h.axis='omega') AS unfactored
  FROM kan.sequence_factorial_homology h
  JOIN calx.sequences s ON s.seq_id = h.seq_id
 GROUP BY h.seq_id, s.name, s.family;

-- Cross-axis recurrent similarity: sequences whose FULL factorial signature
-- (parity, omega, bigomega, shared_prime) coincides -- structural agreement
-- in factorization space, prefix-independent.
CREATE OR REPLACE VIEW kan.factorial_similarity AS
SELECT a.seq_id AS seq_a,
       b.seq_id AS seq_b,
       a.family AS family_a,
       b.family AS family_b,
       (a.family = b.family) AS same_family,
       a.h1_parity, a.h1_omega, a.h1_bigomega, a.h1_shared_prime
  FROM kan.factorial_signature a
  JOIN kan.factorial_signature b
    ON a.h1_parity        = b.h1_parity
   AND a.h1_omega         = b.h1_omega
   AND a.h1_bigomega      = b.h1_bigomega
   AND a.h1_shared_prime  = b.h1_shared_prime
   AND a.seq_id < b.seq_id;
