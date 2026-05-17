-- Unified model, step 67: lithon integration (F_1 glued to Spec(Z)).
--
-- lithon () is an adelic prime-power lattice:
-- a 16x16 boolean grid, Phi(state)=Sum BASES[r]^(c+1),
-- BASES=[1,2,3,5,...,47]. Row 0 (base 1) is the unary bridge -- the
-- field-with-one-element F_1 absolute point gluing the disconnected
-- p-adic prime rows (the finite places of Spec(Z)).
--
-- We integrate it as a CONCRETE SPLITTING of our abstract decomposition:
--   val  : lithon -> seq      (Phi : state |-> integer)
--   pack : seq   -> lithon    (state_from_integer : greedy prime-power atoms)
--
-- Attested (faithful/bounded scope -- additive Phi only):
--   P1 retraction      Phi(pack(n)) = n  on every reachable n
--                      (val o pack = id_seq there).
--   P2 W1 single-atom  for an IN-WINDOW prime power p^k (p among the first
--                      15 primes, p^k <= MAX_VALUE -- the bottom rung of our
--                      tower inside lithon's finite adelic horizon) pack(p^k)
--                      is the SINGLE cell (row pi(p), col k-1): the grid row
--                      index equals pi(p) = ht(p^k) exactly. Out-of-window
--                      prime powers are honestly beyond the adelic horizon
--                      (the same finite-window discipline as the chromatic
--                      SIEVE/HI bucket); lithon realises the chromatic/
--                      prime_members data geometrically within its window.
--   P3 F1 gluing       the unit 1 is UNREACHABLE from the prime rows alone
--                      (smallest prime atom = 2), but reachable once row-0
--                      is added. Row-0 == F_1 == our W_0 units rung:
--                      F_1 literally adjoins the multiplicative unit 1 to
--                      Spec(Z); it is the same gluing the identity-
--                      decomposition capstone required (W_0).
--
-- Proved input-independently by proofs/lithon.py. Idempotent.

CREATE TABLE IF NOT EXISTS kan.lithon (
    structure        TEXT PRIMARY KEY,       -- 'lithon_F1_SpecZ'
    val_functor      TEXT NOT NULL,          -- 'val'  (lithon -> seq)
    pack_functor     TEXT NOT NULL,          -- 'pack' (seq -> lithon)
    retraction       BOOLEAN NOT NULL,       -- P1  Phi(pack(n))=n
    w1_single_atom   BOOLEAN NOT NULL,       -- P2  p^k -> one cell, row=pi(p)
    f1_adjoins_unit  BOOLEAN NOT NULL,       -- P3a 1 reachable iff row-0 kept
    f1_load_bearing  BOOLEAN NOT NULL,       -- P3b row-0 used by some pack(n)
    w0_correspondence BOOLEAN NOT NULL,      -- P3c row-0 == W_0 units rung
    is_integration   BOOLEAN NOT NULL,
    verified_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Per-term witnesses: how each base term packs onto the adelic grid.
CREATE TABLE IF NOT EXISTS kan.lithon_witness (
    n             BIGINT PRIMARY KEY,
    phi_ok        BOOLEAN NOT NULL,          -- Phi(pack(n)) == n
    row0_cells    INTEGER NOT NULL,          -- # F_1 (row-0) atoms used
    prime_rows    INTEGER NOT NULL,          -- # occupied prime rows
    is_prime_pow  BOOLEAN NOT NULL,          -- omega(n)=1
    in_window     BOOLEAN NOT NULL,          -- prime among first 15 & n<=MAX
    grid_ht       INTEGER,                   -- max occupied prime-row idx
    model_ht      INTEGER,                   -- ht(n) from our chromatic axis
    ht_matches    BOOLEAN NOT NULL           -- grid_ht == model_ht (in-window W1)
);

-- Self-heal DBs created before in_window existed (idempotent).
ALTER TABLE kan.lithon_witness
    ADD COLUMN IF NOT EXISTS in_window BOOLEAN NOT NULL DEFAULT FALSE;

-- View columns changed across versions; drop so CREATE can reshape them.
DROP VIEW IF EXISTS kan.lithon_laws,
                    kan.lithon_ht_correspondence,
                    kan.lithon_summary CASCADE;

CREATE OR REPLACE VIEW kan.lithon_summary AS
SELECT structure, val_functor, pack_functor, is_integration,
       (retraction AND w1_single_atom AND f1_adjoins_unit
        AND f1_load_bearing AND w0_correspondence) AS all_laws
  FROM kan.lithon;

-- Witness that ht read off the adelic grid equals our chromatic ht on the
-- prime-power (W1) stratum -- the geometric/abstract correspondence.
CREATE OR REPLACE VIEW kan.lithon_ht_correspondence AS
SELECT count(*) FILTER (WHERE is_prime_pow AND in_window)         AS w1_terms,
       count(*) FILTER (WHERE is_prime_pow AND in_window AND ht_matches) AS w1_ht_ok,
       count(*) FILTER (WHERE is_prime_pow AND NOT in_window)     AS beyond_horizon,
       bool_and(phi_ok)                                           AS all_phi_ok,
       count(*) FILTER (WHERE row0_cells > 0)                     AS f1_used,
       bool_and(CASE WHEN is_prime_pow AND in_window
                     THEN ht_matches ELSE TRUE END)               AS w1_corresponds
  FROM kan.lithon_witness;

CREATE OR REPLACE VIEW kan.lithon_laws AS
SELECT (SELECT bool_and(is_integration) FROM kan.lithon)   AS integrated,
       (SELECT bool_and(retraction AND w1_single_atom AND f1_adjoins_unit
                        AND f1_load_bearing AND w0_correspondence)
          FROM kan.lithon)                                 AS all_laws,
       (SELECT w1_corresponds FROM kan.lithon_ht_correspondence)        AS w1_ht_correspondence,
       (SELECT count(*) FROM kan.lithon_witness)                        AS witnesses;
