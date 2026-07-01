-- Unified model, step 69: the static adelic shadow.
--
-- The boildown fiber Phi^{-1}(N) of val:lithon->seq -- the representational
-- multiplicity invisible to every multiplicative axis (it is what `val`
-- forgets). Each prime row {p^1..p^16} is SUPER-INCREASING, so its subset
-- sums are unique (per-prime-row count in {0,1}); ALL multiplicity is the
-- F_1 binomial kernel convolved with the prime-power subset-sum count:
--
--     rho(N) = SUM_{s=0..16} C(16,s) * A(N - s)
--
-- where A(m) = #subsets of the 240 prime-power atoms summing to m.
-- The F_1 row (16 unit cells) is the screen that catches the shadow.
--
-- We compute rho + the F_1-marginal per corpus term (within a bounded
-- shadow window), build a coarse per-sequence shadow signature, and TEST
-- whether the shadow separates the residual combined-invariant collision
-- kernel that the multiplicative tower could NOT separate
-- ({pow2,pow3,pow4}, primorial~evens). Proved by proofs/shadow.py.
-- Idempotent.

CREATE TABLE IF NOT EXISTS kan.shadow_term (
    seq        TEXT NOT NULL,
    n          NUMERIC NOT NULL,             -- corpus terms can be astronomical
    in_window  BOOLEAN NOT NULL,             -- n <= shadow CAP
    rho        NUMERIC,                      -- |Phi^{-1}(n)| (can be huge)
    a_count    NUMERIC,                      -- A(n): prime-power subset-sum #
    f1_mean    NUMERIC,                      -- mean # row-0 cells over the fiber
    f1_max     INTEGER,                      -- max row-0 usage with rep
    f1_support INTEGER,                      -- # of s in 0..16 with C(16,s)A(n-s)>0
    PRIMARY KEY (seq, n)
);

-- Self-heal DBs created before n was widened to NUMERIC (idempotent).
ALTER TABLE kan.shadow_term ALTER COLUMN n TYPE NUMERIC;

CREATE TABLE IF NOT EXISTS kan.shadow_signature (
    seq          TEXT PRIMARY KEY,
    window_terms INTEGER NOT NULL,
    sig_sha      TEXT NOT NULL                -- sha of the coarse shadow vector
);

-- Does the shadow separate a combined-invariant collision?
CREATE TABLE IF NOT EXISTS kan.shadow_separation (
    seq_a          TEXT NOT NULL,
    seq_b          TEXT NOT NULL,
    combined_equal BOOLEAN NOT NULL,          -- collided under combined invariant
    shadow_distinct BOOLEAN NOT NULL,         -- shadow signatures differ
    resolves       BOOLEAN NOT NULL,          -- combined_equal AND shadow_distinct
    PRIMARY KEY (seq_a, seq_b)
);

CREATE OR REPLACE VIEW kan.shadow_summary AS
SELECT s.seq, s.window_terms, s.sig_sha,
       (SELECT count(*) FROM kan.shadow_term t
         WHERE t.seq=s.seq AND t.in_window)              AS in_window_terms,
       (SELECT max(f1_max) FROM kan.shadow_term t
         WHERE t.seq=s.seq AND t.in_window)              AS peak_f1
  FROM kan.shadow_signature s;

CREATE OR REPLACE VIEW kan.shadow_laws AS
SELECT (SELECT count(*) FROM kan.shadow_separation)                   AS tested_pairs,
       (SELECT count(*) FROM kan.shadow_separation
          WHERE combined_equal)                                       AS collided_pairs,
       -- "the shadow resolves every residual combined-invariant collision."
       -- Three cases, kept distinct (see 79_cert_kan_engines.sql discipline):
       --   * shadow never computed (no in-window terms) -> NULL (unknown).
       --   * computed, some collisions -> bool_and(resolves): FALSE if the
       --     shadow fails to separate any of them (a genuine refutation).
       --   * computed, ZERO collisions -> vacuously TRUE (the multiplicative
       --     tower already separated the corpus; nothing left to resolve).
       -- Only the last case changes: bool_and over the empty set is NULL, but
       -- with the shadow actually computed that is a vacuous truth, not an
       -- unknown -- consistent with how the other engines score empty strata.
       CASE WHEN (SELECT count(*) FROM kan.shadow_term WHERE in_window) = 0
            THEN NULL
            ELSE COALESCE(
                   (SELECT bool_and(resolves) FROM kan.shadow_separation
                     WHERE combined_equal),
                   TRUE)
       END                                                            AS shadow_resolves_kernel,
       (SELECT count(DISTINCT seq) FROM kan.shadow_term
          WHERE in_window)                                            AS seqs_with_window;
