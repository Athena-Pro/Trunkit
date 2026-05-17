-- Unified model, step 43: gap-pattern homology of sequences, at depth.
--
-- The original calx OEIS matcher (sql/06, tools/oeis_match.py) did SHALLOW
-- prefix agreement: "does the leading n-term prefix of sequence A coincide
-- with B" (blowup-term membership). This layer goes to DEPTH:
--
--   * difference tower  : analyse delta^0 A, delta^1 A, delta^2 A
--                          (a sequence, its gaps, the gaps of its gaps)
--   * gap-pattern H1     : the verified Erdos gap-pattern complex per order
--   * hyperedges         : count of 3-gap commuting closures (multi-gap
--                          co-occurrence beyond the pairwise 2-cell squares)
--
-- Two sequences are "similar at depth k" when their H1 signature vectors
-- agree at order k even if their term prefixes never coincide -- the
-- recurrent-similarity-at-depth the prefix scan cannot see.
--
-- Term lists live HERE (kan), not in calx.sequence_membership, whose
-- n -> calx.integers(n) FK cannot hold large OEIS terms. calx.sequences
-- remains the catalog; kan is the analysis substrate. Idempotent.

-- Analyzed term lists (NUMERIC: Bell/Catalan/partition terms get huge).
CREATE TABLE IF NOT EXISTS kan.sequence_terms (
    seq_id TEXT    NOT NULL REFERENCES calx.sequences(seq_id) ON DELETE CASCADE,
    idx    INTEGER NOT NULL,
    term   NUMERIC NOT NULL,
    PRIMARY KEY (seq_id, idx)
);

-- One row per (sequence, difference order): the gap-pattern invariants.
CREATE TABLE IF NOT EXISTS kan.sequence_homology (
    seq_id      TEXT    NOT NULL REFERENCES calx.sequences(seq_id) ON DELETE CASCADE,
    diff_order  INTEGER NOT NULL,        -- 0 = A, 1 = gaps, 2 = gaps of gaps
    n_vertices  INTEGER NOT NULL,
    n_edges     INTEGER NOT NULL,
    n_squares   INTEGER NOT NULL,        -- pairwise 2-cells (original Erdos)
    n_hyper3    INTEGER NOT NULL,        -- 3-gap commuting closures (hyperedges)
    h1          INTEGER NOT NULL,        -- H1 rank of the gap-pattern complex
    d1d2_zero   BOOLEAN NOT NULL,        -- chain-complex property check
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (seq_id, diff_order)
);

-- The H1 signature vector of a sequence, ordered by difference order.
CREATE OR REPLACE VIEW kan.sequence_signature AS
SELECT h.seq_id,
       s.name,
       s.family,
       array_agg(h.h1       ORDER BY h.diff_order) AS h1_signature,
       array_agg(h.n_hyper3 ORDER BY h.diff_order) AS hyper3_signature,
       max(h.diff_order)                           AS max_order
  FROM kan.sequence_homology h
  JOIN calx.sequences s ON s.seq_id = h.seq_id
 GROUP BY h.seq_id, s.name, s.family;

-- Recurrent similarity at depth: distinct sequences whose FULL H1 signature
-- vectors are identical. This is the deep analogue of the prefix match --
-- structural agreement across the whole difference tower, prefix-independent.
CREATE OR REPLACE VIEW kan.homology_similarity AS
SELECT a.seq_id          AS seq_a,
       b.seq_id          AS seq_b,
       a.h1_signature    AS h1_signature,
       a.family          AS family_a,
       b.family          AS family_b,
       (a.family = b.family) AS same_family
  FROM kan.sequence_signature a
  JOIN kan.sequence_signature b
    ON a.h1_signature = b.h1_signature
   AND a.seq_id < b.seq_id;
