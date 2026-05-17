-- Unified model, step 77: Monstrous Moonshine -- F_1 IS the trivial rep.
--
-- Through every step the field-with-one-element unit was the load-bearing
-- object under a mask: the CLOSER (self-syzygy, 71), the ZETA/summatory
-- operator (self-shadow, 73), the RADIX-1 cell (f1_radix, 75), W_0 / row-0
-- (lithon, 67). Moonshine says what that unit IS: the TRIVIAL
-- REPRESENTATION of the Monster M.
--
--   McKay:  196884 = 196883 + 1
--           21493760 = 21296876 + 196883 + 1
--           864299970 = 2*1 + 2*196883 + 21296876 + 842609326
-- Every graded dimension of the moonshine module V-natural decomposes into
-- Monster irreps WITH the trivial rep at multiplicity >= 1. That universal
-- "+1" is exactly our F_1 point -- F_1 adjoined to the Monster's
-- representation ring, the same shape as F_1 glued to Spec(Z).
--
-- Ogg: the primes dividing |M| are EXACTLY the 15 supersingular
-- (genus-zero) primes {2,3,5,7,11,13,17,19,23,29,31,41,47,59,71} -- the
-- same kind of finite distinguished prime horizon as lithon's 15-prime
-- adelic window (overlap 13; ss-only {59,71}, lithon-only {37,43}).
--
-- Attested:
--   M1 McKay F_1     head graded dims of V-natural decompose EXACTLY into
--                    Monster irreps, every decomposition carrying the
--                    trivial rep at multiplicity >= 1 (the universal "+1");
--   M2 ss horizon    prime_set(|M|) reconstructs |M| and equals the 15
--                    supersingular primes -- a genus-zero prime horizon of
--                    the same length/role as lithon's;
--   M3 j syzygy      the j-coefficients (exact E4^3 / Delta) run through
--                    the greedy self-syzygy have eventual leading digit 1:
--                    j is CRACKABLE, the Fibonacci class, readout 1
--                    (consecutive ratio e^{2pi/sqrt n} -> 1);
--   M4 radix collapse binary F_1-depth = O(sqrt n) << magnitude
--                    e^{4pi sqrt n}: moonshine is radix-collapsible.
--
-- Proved input-independently by proofs/moonshine.py. Idempotent.

CREATE TABLE IF NOT EXISTS kan.moonshine (
    structure        TEXT PRIMARY KEY,       -- 'V_natural'
    monster_order    NUMERIC NOT NULL,       -- |M|
    irrep_dims       TEXT NOT NULL,          -- small Monster irrep dims
    mckay_f1_ok      BOOLEAN NOT NULL,       -- M1: exact + trivial rep >=1
    ss_primes        TEXT NOT NULL,          -- 15 supersingular primes
    ss_horizon_ok    BOOLEAN NOT NULL,       -- M2: prime_set(|M|)=ss set
    lithon_overlap    INTEGER NOT NULL,       -- |ss ∩ lithon horizon|
    j_eventual_lead  INTEGER,                -- M3: stable self-syzygy lead
    j_crackable      BOOLEAN NOT NULL,       -- M3: bounded lead (Fib class)
    j_depth_collapse BOOLEAN NOT NULL,       -- M4: O(sqrt n) << magnitude
    head_sha         TEXT NOT NULL,          -- pinned moonshine fingerprint
    verified_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kan.moonshine_term (
    n                INTEGER PRIMARY KEY,    -- graded level
    graded_dim       NUMERIC NOT NULL,       -- dim V_n = j-coeff a(n)
    mult_vector      TEXT NOT NULL,          -- irrep multiplicities
    trivial_mult     INTEGER NOT NULL,       -- the "+1" = F_1 multiplicity
    decomposes_ok    BOOLEAN NOT NULL,       -- SUM mult*dim == a(n)
    syzygy_lead      NUMERIC,                -- q_{n-1}=floor(a_n/a_{n-1})
    bitlen           INTEGER,                -- magnitude proxy
    binary_depth     INTEGER                 -- 16*ceil(bitlen/16)
);

CREATE OR REPLACE VIEW kan.moonshine_summary AS
SELECT structure, irrep_dims,
       (mckay_f1_ok AND ss_horizon_ok AND j_crackable
        AND j_depth_collapse)               AS faithful,
       j_eventual_lead, lithon_overlap, head_sha
  FROM kan.moonshine;

-- The supersingular = lithon prime-horizon witness.
CREATE OR REPLACE VIEW kan.moonshine_supersingular AS
SELECT structure,
       ss_primes,
       ss_horizon_ok,
       lithon_overlap,
       (15 - lithon_overlap) AS horizon_symmetric_diff_each
  FROM kan.moonshine;

CREATE OR REPLACE VIEW kan.moonshine_laws AS
SELECT (SELECT bool_and(mckay_f1_ok)      FROM kan.moonshine) AS m1_mckay_f1,
       (SELECT bool_and(ss_horizon_ok)    FROM kan.moonshine) AS m2_ss_horizon,
       (SELECT bool_and(j_crackable)      FROM kan.moonshine) AS m3_j_crackable,
       (SELECT bool_and(j_depth_collapse) FROM kan.moonshine) AS m4_collapse,
       (SELECT bool_and(decomposes_ok)    FROM kan.moonshine_term)
                                                              AS all_decompose,
       (SELECT bool_and(trivial_mult >= 1) FROM kan.moonshine_term)
                                                              AS f1_everywhere;
