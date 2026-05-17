-- Unified model, step 75: the F_1 radix axis (unary zeta <-> binary place-value).
--
-- lithon row-0 is 16 unit cells. Every shadow so far read them ONE way --
-- unary: each cell worth 1, value = popcount, multiplicity C(16,s). That is
-- the zeta/binomial kernel of steps 70 (static shadow) and 73 (summatory).
--
-- Read the SAME 16 cells as a BINARY place-value register: cell c worth
-- 2^c, and the UNSET cells are significant zeros ("0 columns accounted
-- for"). Same register, radix 1 -> radix 2:
--
--   unary  (b=1): cell=1,   value=popcount in [0,16],  mult = C(16,s)
--   binary (b=2): cell=2^c, value in [0,65535],         mult = 1 (bijection)
--
-- Consequence: the F_1 slack of an explosive term needs c_0 = a_n unit
-- copies under unary (depth LINEAR in magnitude -- why step 73 had to
-- window the explosive corpus out), but only ceil(bitlen/16) carry blocks
-- under binary (depth O(log a_n)). The depth of explosive terms collapses.
--
-- The two readings are the dual extremes of one axis: b=1 is multiplicity-
-- maximal / depth-unbounded; b=2 is multiplicity-trivial / depth-minimal.
-- They RECONCILE on the value (both decode to a_n); the radix only trades
-- depth against multiplicity -- the same "two factorizations of one object"
-- shape as the bigrading and the identity capstone.
--
-- Attested:
--   R1 binary bijection  2^c strictly super-increasing => per-cell count
--                        in {0,1}; multiplicity 1, vs unary's C(16,s);
--   R2 depth collapse    over ALL 60 terms (incl. the astronomical tail)
--                        binary depth = 16*ceil(bitlen/16) = O(log a_n),
--                        strictly below the unbounded unary depth a_n;
--   R3 reconciliation    decoding the binary 16-bit blocks sums to exactly
--                        a_n (radix moves cost, never the integer);
--   R4 carry / horizon   depth = 16 * blocks, blocks = ceil(bitlen/16):
--                        the lithon 16-col horizon restated as a radix carry.
--
-- Proved input-independently by proofs/f1_radix.py. Idempotent.

CREATE TABLE IF NOT EXISTS kan.f1_radix (
    seq              TEXT PRIMARY KEY,
    n_terms          INTEGER NOT NULL,
    binary_bijection BOOLEAN NOT NULL,        -- R1: mult 1 (vs C(16,s))
    depth_collapses  BOOLEAN NOT NULL,        -- R2: O(log) < unbounded unary
    reconciles       BOOLEAN NOT NULL,        -- R3: blocks decode to a_n
    max_unary_depth  NUMERIC NOT NULL,        -- max a_n (the unary cost)
    max_binary_depth INTEGER NOT NULL,        -- max 16*blocks (the binary cost)
    max_blocks       INTEGER NOT NULL,        -- max ceil(bitlen/16)
    head_sha         TEXT NOT NULL,           -- pinned radix fingerprint
    verified_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kan.f1_radix_term (
    seq              TEXT NOT NULL,
    n                INTEGER NOT NULL,
    term             NUMERIC NOT NULL,        -- a_n
    bitlen           INTEGER NOT NULL,        -- floor(log2 a_n)+1
    unary_depth      NUMERIC NOT NULL,        -- a_n (the unary unit count)
    blocks           INTEGER NOT NULL,        -- ceil(bitlen/16) carry blocks
    binary_depth     INTEGER NOT NULL,        -- 16 * blocks
    reconstructs_ok  BOOLEAN NOT NULL,        -- blocks decode back to a_n
    PRIMARY KEY (seq, n)
);

CREATE OR REPLACE VIEW kan.f1_radix_summary AS
SELECT seq, n_terms,
       (binary_bijection AND depth_collapses AND reconciles) AS faithful,
       max_unary_depth, max_binary_depth, max_blocks, head_sha
  FROM kan.f1_radix
 ORDER BY seq;

-- The collapse witness: unary (magnitude) vs binary (log) cost per sequence.
CREATE OR REPLACE VIEW kan.f1_radix_collapse AS
SELECT seq,
       max_unary_depth,
       max_binary_depth,
       max_blocks,
       (max_unary_depth > max_binary_depth) AS collapsed
  FROM kan.f1_radix
 ORDER BY max_unary_depth DESC;

CREATE OR REPLACE VIEW kan.f1_radix_laws AS
SELECT (SELECT bool_and(binary_bijection) FROM kan.f1_radix) AS r1_bijection,
       (SELECT bool_and(depth_collapses)  FROM kan.f1_radix) AS r2_collapse,
       (SELECT bool_and(reconciles)       FROM kan.f1_radix) AS r3_reconcile,
       (SELECT bool_and(max_unary_depth > max_binary_depth)
          FROM kan.f1_radix)                                 AS r4_carry,
       (SELECT count(*) FROM kan.f1_radix)                    AS sequences;
