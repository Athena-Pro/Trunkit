-- Unified model, step 49: higher homology of the shared-prime structure.
--
-- The factorial layer (45) took the shared-prime GRAPH's cycle rank
-- (Betti-1 = E - V + C). This goes up a dimension: the simplicial FLAG
-- (clique) complex of that graph --
--
--   0-simplices : factorable terms
--   1-simplices : shared-prime edges
--   2-simplices : 3-cliques (triangles)
--   3-simplices : 4-cliques (tetrahedra)
--
-- Standard simplicial boundaries d1,d2,d3 (d.d = 0 automatic), giving
--   b0 = #components
--   b1_flag = (E - rank d1) - rank d2     (cycle rank with triangles filled)
--   b2 = (T - rank d2) - rank d3          -- THE new higher-homology content
--
-- cycle_rank (= E - V + C) is recorded as a consistency anchor: it must
-- equal the prior factorial shared_prime H1. Dense shared-prime graphs make
-- the 3-/4-clique enumeration explode; those are marked over_budget and only
-- the cheap graph invariants (b0, cycle_rank) are stored. Idempotent.

CREATE TABLE IF NOT EXISTS kan.shared_prime_betti (
    seq_id       TEXT    NOT NULL REFERENCES calx.sequences(seq_id) ON DELETE CASCADE,
    n_vertices   INTEGER NOT NULL,
    n_edges      INTEGER NOT NULL,
    n_triangles  INTEGER NOT NULL,   -- -1 if not enumerated (over budget)
    n_tetra      INTEGER NOT NULL,   -- -1 if not enumerated (over budget)
    b0           INTEGER NOT NULL,   -- connected components
    cycle_rank   INTEGER NOT NULL,   -- graph Betti-1 = E - V + C (anchor)
    b1_flag      INTEGER NOT NULL,   -- flag-complex H1; -1 if over budget
    b2           INTEGER NOT NULL,   -- flag-complex H2; -1 if over budget
    over_budget  BOOLEAN NOT NULL,
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (seq_id)
);

-- Higher-homology summary joined to the factorial anchor; the anchor_ok
-- column flags any drift between cycle_rank and the factorial shared_prime H1.
CREATE OR REPLACE VIEW kan.higher_homology_summary AS
SELECT b.seq_id,
       s.name,
       s.family,
       b.b0,
       b.cycle_rank,
       b.b1_flag,
       b.b2,
       b.over_budget,
       f.h1                                   AS factorial_sp_h1,
       (b.cycle_rank = f.h1)                  AS anchor_ok,
       b.n_vertices, b.n_edges, b.n_triangles, b.n_tetra
  FROM kan.shared_prime_betti b
  JOIN calx.sequences s ON s.seq_id = b.seq_id
  LEFT JOIN kan.sequence_factorial_homology f
         ON f.seq_id = b.seq_id AND f.axis = 'shared_prime';

-- Sequences with non-trivial second homology (b2 > 0): genuine 2-cycles
-- in the prime-sharing structure that no 1-dimensional view detects.
CREATE OR REPLACE VIEW kan.b2_nontrivial AS
SELECT seq_id, name, family, b2, n_triangles, n_tetra
  FROM kan.higher_homology_summary
 WHERE NOT over_budget AND b2 > 0
 ORDER BY b2 DESC, seq_id;
