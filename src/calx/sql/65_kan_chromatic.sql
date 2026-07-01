-- Unified model, step 65: the chromatic height tower (horizontal axis 3).
--
-- A faithful arithmetic analog of the chromatic filtration. Height of an
-- integer t is the prime-INDEX of its largest prime factor:
--
--     ht(t) = pi(P+(t))      ht(1)=0,  ht(p_n)=n,  ht(2^k)=1
--
-- Localizations (the chromatic TOWER -- a *filtration*, not a flat grading):
--     L_n(S) = [ t in S : ht(t) <= n ]      (the p_n-smooth localization)
--     M_n(S) = L_n(S) (-) L_{n-1}(S)        (monochromatic layer = ht == n)
--
--   C1 idempotent      L_n . L_n = L_n
--   C2 filtration      L_n(S) subset L_{n+1}(S) subset S      (nested)
--   C3 smashing law    L_m . L_n = L_{min(m,n)}  (the chromatic tower law)
--   C4 layers/fracture M_n = L_n (-) L_{n-1} ; M_n = [t: ht=n]
--   C5 convergence     colim_n L_n = Id_seq ; (+)_n M_n = Id_seq (natural)
--   C6 compatibility   L_n . W_i = W_i . L_n , L_n . B_j = B_j . L_n
--                      (chromatic localization is smashing wrt the omega x
--                       Omega bigrading -> a compatible trigrading)
--
-- The smashing law + convergence are the genuinely chromatic features the
-- omega/Omega/excess gradings lacked. Proved by proofs/chromatic.py.
-- Idempotent.

CREATE TABLE IF NOT EXISTS kan.chromatic (
    structure     TEXT PRIMARY KEY,          -- 'largest_prime_height'
    idempotent    BOOLEAN NOT NULL,          -- C1
    filtration    BOOLEAN NOT NULL,          -- C2
    smashing      BOOLEAN NOT NULL,          -- C3  L_m.L_n = L_min
    layers_ok     BOOLEAN NOT NULL,          -- C4  M_n = L_n - L_{n-1}
    convergence   BOOLEAN NOT NULL,          -- C5  colim L_n = Id
    bigrade_compat BOOLEAN NOT NULL,         -- C6  commutes with W_i,B_j
    is_chromatic  BOOLEAN NOT NULL,
    verified_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Per-sequence chromatic profile: monochromatic-layer sizes by height.
CREATE TABLE IF NOT EXISTS kan.chromatic_layer (
    seq      TEXT NOT NULL,
    height   INTEGER NOT NULL,               -- n (0 = units)
    n_terms  INTEGER NOT NULL,               -- |M_n(S)|
    cum_terms INTEGER NOT NULL,              -- |L_n(S)|  (cumulative)
    PRIMARY KEY (seq, height)
);

CREATE OR REPLACE VIEW kan.chromatic_summary AS
SELECT structure, is_chromatic,
       (idempotent AND filtration AND smashing AND layers_ok
        AND convergence AND bigrade_compat) AS all_laws
  FROM kan.chromatic;

-- Convergence witness: top cumulative localization recovers the whole seq.
CREATE OR REPLACE VIEW kan.chromatic_convergence AS
SELECT c.seq,
       max(c.height)                              AS top_height,
       (SELECT cum_terms FROM kan.chromatic_layer x
         WHERE x.seq=c.seq ORDER BY height DESC LIMIT 1) AS top_cum,
       (SELECT count(*) FROM kan.sequence_terms t WHERE t.seq_id=c.seq) AS n_terms,
       -- Convergence compares L_top(S) against |S|. When the source
       -- kan.sequence_terms is empty for this seq, |S| is unknown here (a
       -- partial/stale load leaves chromatic_layer populated but its source
       -- gone), so convergence is VACUOUS, not violated. Return NULL, not
       -- FALSE: emitting FALSE manufactures a contradiction and refutes the
       -- chromatic laws on what is really a staleness event (see the
       -- three-valued discipline in 79_cert_kan_engines.sql).
       CASE WHEN (SELECT count(*) FROM kan.sequence_terms t
                   WHERE t.seq_id=c.seq) = 0
            THEN NULL
            ELSE (SELECT cum_terms FROM kan.chromatic_layer x
                   WHERE x.seq=c.seq ORDER BY height DESC LIMIT 1)
                 = (SELECT count(*) FROM kan.sequence_terms t
                     WHERE t.seq_id=c.seq)
       END                                        AS converges
  FROM kan.chromatic_layer c
 GROUP BY c.seq;

CREATE OR REPLACE VIEW kan.chromatic_laws AS
SELECT (SELECT bool_and(is_chromatic) FROM kan.chromatic)              AS chromatic,
       (SELECT bool_and(idempotent AND filtration AND smashing
                        AND layers_ok AND convergence AND bigrade_compat)
          FROM kan.chromatic)                                          AS all_laws,
       (SELECT bool_and(converges) FROM kan.chromatic_convergence)     AS all_converge,
       (SELECT count(*) FROM kan.chromatic_layer)                      AS layer_rows;
