-- Unified model, step 71: the greedy self-syzygy expansion.
--
-- For a recursively-defined sequence, expand each term in its OWN earlier
-- terms (Ostrowski-style mixed radix), descending:
--
--   r = a_n;  for k = n-1 .. 0:  q_k = floor(r / a_k);  r -= q_k * a_k
--
-- a_0 = 1 is the field-with-one-element CLOSER: it drives the final
-- remainder to 0 (dual to row-0 closing the lithon greedy). The construction
-- is RELATIVE, so it needs no adelic window -- explosive sequences are
-- handled in full (every term, exactly).
--
-- Attested:
--   G1 termination     a_0=1  =>  greedy ends with remainder 0;
--   G2 reconstruction  SUM_k q_k * a_k = a_n  (faithful digit string);
--   G3/G4 the CRACK    the leading digit q_{n-1} = floor(a_n/a_{n-1}) is
--                      eventually CONSTANT iff the sequence has finite
--                      geometric growth (Catalan->3, Fibonacci->1,
--                      Motzkin->2 = floor of the growth ratio), and
--                      DIVERGES iff super-exponential (Bell, factorial).
--                      => a bounded self-fingerprint cracks the chestnut,
--                         and tells you exactly which sequences it cracks.
--
-- Proved input-independently by proofs/self_syzygy.py. Idempotent.

CREATE TABLE IF NOT EXISTS kan.self_syzygy (
    seq            TEXT PRIMARY KEY,
    n_terms        INTEGER NOT NULL,
    a0_is_one      BOOLEAN NOT NULL,
    terminates     BOOLEAN NOT NULL,        -- G1: all remainders hit 0
    reconstructs   BOOLEAN NOT NULL,        -- G2: SUM q_k a_k = a_n for all n
    leading_digits TEXT NOT NULL,           -- the q_{n-1} string (head)
    eventual_lead  INTEGER,                 -- stable leading digit, NULL=unbounded
    bounded_lead   BOOLEAN NOT NULL,        -- leading digit eventually constant
    growth_class   TEXT NOT NULL,           -- 'geometric' | 'super-exponential'
    verified_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kan.self_syzygy_term (
    seq            TEXT NOT NULL,
    n              INTEGER NOT NULL,        -- index of the expanded term
    lead_digit     NUMERIC NOT NULL,        -- q_{n-1} = floor(a_n/a_{n-1})
    nonzero_digits INTEGER NOT NULL,        -- # nonzero q_k (expansion weight)
    final_remainder NUMERIC NOT NULL,       -- 0 iff F_1 closer worked
    reconstructs_ok BOOLEAN NOT NULL,
    PRIMARY KEY (seq, n)
);

CREATE OR REPLACE VIEW kan.self_syzygy_summary AS
SELECT seq, n_terms, growth_class, eventual_lead, bounded_lead,
       (terminates AND reconstructs) AS faithful,
       leading_digits
  FROM kan.self_syzygy
 ORDER BY growth_class, seq;

-- The crack dichotomy: which sequences have a bounded self-fingerprint.
CREATE OR REPLACE VIEW kan.self_syzygy_crack AS
SELECT seq,
       bounded_lead                       AS crackable,
       eventual_lead                      AS growth_readout,
       growth_class
  FROM kan.self_syzygy
 ORDER BY bounded_lead DESC, seq;

CREATE OR REPLACE VIEW kan.self_syzygy_laws AS
SELECT (SELECT bool_and(terminates) FROM kan.self_syzygy)             AS all_terminate,
       (SELECT bool_and(reconstructs) FROM kan.self_syzygy)           AS all_reconstruct,
       (SELECT count(*) FROM kan.self_syzygy WHERE bounded_lead)      AS crackable_seqs,
       (SELECT count(*) FROM kan.self_syzygy WHERE NOT bounded_lead)  AS superexp_seqs,
       (SELECT count(*) FROM kan.self_syzygy)                         AS sequences;
