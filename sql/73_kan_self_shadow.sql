-- Unified model, step 73: the self-shadow multiplicity rho_self.
--
-- The self-syzygy (step 71) took ONE greedy representation of a_n over its
-- own predecessors. The self-shadow counts ALL of them -- the denumerant
-- fiber of the relative expansion:
--
--   rho_self(n) = #{ (c_0,...,c_{n-1}) in Z>=0^n : SUM_k c_k a_k = a_n }
--
-- a_0 = 1 is the field-with-one-element operator again, but here it is no
-- longer a CLOSER (step 71) -- it is the SUMMATORY / zeta operator. Because
-- the unit part is UNBOUNDED, every representation splits uniquely as
-- (units take the slack) + (a representation of the slack by the non-unit
-- parts), giving the exact factorization
--
--   rho_self(n) = SUM_{m=0}^{a_n} rho_hat(m)           (F_1 = the zeta op)
--
-- where rho_hat omits a_0. F_1 turns the point-count rho_hat into its own
-- summatory function -- dual to step 70's static shadow where F_1 was the
-- binomial convolution kernel.
--
-- Targets a_n explode, so the count is windowed to the head (a_n <= cap):
-- the chestnut, counted regardless of the tail's size.
--
-- Attested:
--   L1 well-defined    rho_self(n) >= 1 for all n (all-units rep), and
--                      >= 2 for n >= 2 (the duplicate-1 / convolution syzygy
--                      always yields a second representation);
--   L2 F_1 = zeta      rho_self == SUM_{m=0}^{a_n} rho_hat(m), the factored
--                      cumulative agreeing with an independent direct DP;
--   L3 head fingerprint the windowed rho_self vector is hash-pinned;
--   L4 separation      the self-shadow signature pairwise-distinguishes the
--                      recursive corpus (orthogonal, relative invariant).
--
-- Proved input-independently by proofs/self_shadow.py. Idempotent.

CREATE TABLE IF NOT EXISTS kan.self_shadow (
    seq              TEXT PRIMARY KEY,
    n_terms          INTEGER NOT NULL,
    window_cap       NUMERIC NOT NULL,
    windowed_terms   INTEGER NOT NULL,        -- # of n with a_n <= cap
    a0_is_one        BOOLEAN NOT NULL,
    all_ge1          BOOLEAN NOT NULL,        -- L1: rho_self >= 1 everywhere
    all_ge2_from_n2  BOOLEAN NOT NULL,        -- L1: rho_self >= 2 for n >= 2
    f1_summatory_ok  BOOLEAN NOT NULL,        -- L2: F_1 = zeta factorization
    head_sha         TEXT NOT NULL,           -- L3: pinned head fingerprint
    verified_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kan.self_shadow_term (
    seq              TEXT NOT NULL,
    n                INTEGER NOT NULL,        -- index of the counted term
    target           NUMERIC NOT NULL,        -- a_n
    in_window        BOOLEAN NOT NULL,        -- a_n <= cap (counted)
    rho_self         NUMERIC,                 -- full denumerant (with a_0)
    rho_hat_sum      NUMERIC,                 -- SUM_{m<=a_n} rho_hat(m)
    factored_ok      BOOLEAN,                 -- rho_self == rho_hat_sum
    PRIMARY KEY (seq, n)
);

CREATE OR REPLACE VIEW kan.self_shadow_summary AS
SELECT seq, n_terms, window_cap, windowed_terms,
       (all_ge1 AND all_ge2_from_n2) AS multiplicity_law,
       f1_summatory_ok               AS f1_is_zeta,
       head_sha
  FROM kan.self_shadow
 ORDER BY seq;

-- The F_1 = zeta witness: per windowed term, factored cumulative vs direct.
CREATE OR REPLACE VIEW kan.self_shadow_separation AS
SELECT seq,
       windowed_terms,
       substring(head_sha for 16) AS signature,
       (all_ge1 AND all_ge2_from_n2 AND f1_summatory_ok) AS faithful
  FROM kan.self_shadow
 ORDER BY signature;

CREATE OR REPLACE VIEW kan.self_shadow_laws AS
SELECT (SELECT bool_and(all_ge1)         FROM kan.self_shadow) AS l1_ge1,
       (SELECT bool_and(all_ge2_from_n2) FROM kan.self_shadow) AS l1_ge2,
       (SELECT bool_and(f1_summatory_ok) FROM kan.self_shadow) AS l2_f1_zeta,
       (SELECT count(DISTINCT substring(head_sha for 16))
          FROM kan.self_shadow)                                AS distinct_sigs,
       (SELECT count(*) FROM kan.self_shadow)                  AS sequences;
