-- Unified model, step 63: the omega x Omega bigrading (horizontal capstone).
--
-- Unifies the omega-tower {W_i} and Omega-tower {B_j} into ONE bigraded
-- decomposition:
--
--    M_{i,j}(S) := [ t in S : omega(t)=i  AND  Omega(t)=j ]
--
--   * commuting idempotents : W_i . B_j == B_j . W_i == M_{i,j}
--   * marginals             : (+)_j M_{i,j} = W_i ,  (+)_i M_{i,j} = B_j
--   * triangular support    : M_{i,j} = empty unless 1<=i<=j  (omega<=Omega)
--                             [units sit only at the (0,0) corner]
--   * full identity         : (+)_{(i,j)} M_{i,j} = Id_seq  (natural)
--   * Mobius / inclusion-exclusion :
--       - chain Mobius (Omega):  B_j = zeta_{<=j} (-) zeta_{<=j-1}
--       - excess regrouping   :  E_d := (+)_i M_{i,i+d}  is a THIRD full
--                                 Id decomposition; E_0 = squarefree
--                                 principal idempotent (omega=Omega).
--
-- The triangular support is exactly the precondition that the marginal /
-- zeta operator on the poset {(i,j):i<=j} is unitriangular, hence
-- Mobius-invertible. Proved input-independently by proofs/bigrading.py.
-- Idempotent.

CREATE TABLE IF NOT EXISTS kan.bigrading (
    structure        TEXT PRIMARY KEY,       -- 'omega_x_Omega'
    commuting        BOOLEAN NOT NULL,       -- W_i.B_j = B_j.W_i = M_{i,j}
    marginals_ok     BOOLEAN NOT NULL,       -- (+)_j M = W_i ; (+)_i M = B_j
    triangular       BOOLEAN NOT NULL,       -- support subset {i<=j}
    resolves_id      BOOLEAN NOT NULL,       -- (+) M_{i,j} = Id_seq
    chain_mobius_ok  BOOLEAN NOT NULL,       -- B_j = zeta<=j (-) zeta<=j-1
    excess_full_id   BOOLEAN NOT NULL,       -- (+)_d E_d = Id_seq
    e0_squarefree    BOOLEAN NOT NULL,       -- E_0 = [t: omega=Omega]
    is_bigraded      BOOLEAN NOT NULL,
    verified_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Occupied joint strata per base sequence (the support, with sizes).
CREATE TABLE IF NOT EXISTS kan.bigrading_support (
    seq        TEXT NOT NULL,
    i_omega    INTEGER NOT NULL,
    j_bigomega INTEGER NOT NULL,
    n_terms    INTEGER NOT NULL,
    PRIMARY KEY (seq, i_omega, j_bigomega)
);

CREATE OR REPLACE VIEW kan.bigrading_summary AS
SELECT structure, is_bigraded,
       (commuting AND marginals_ok AND triangular AND resolves_id
        AND chain_mobius_ok AND excess_full_id AND e0_squarefree) AS all_laws
  FROM kan.bigrading;

-- The support lives on the incidence poset i<=j (triangularity witness).
-- support_triangular quantifies over occupied cells; with no support rows the
-- engine never ran, so it reads NULL (unknown), not a vacuous TRUE
-- (99_cert_vacuity discipline, cf. 36c0d04 / a725a7b).
CREATE OR REPLACE VIEW kan.bigrading_triangular AS
SELECT CASE WHEN (SELECT count(*) FROM kan.bigrading_support) = 0
            THEN NULL
            ELSE (SELECT count(*) FROM kan.bigrading_support
                   WHERE i_omega > j_bigomega
                     AND NOT (i_omega = 0 AND j_bigomega = 0)) = 0
       END                                                AS support_triangular,
       (SELECT count(*) FROM kan.bigrading_support)       AS occupied_strata,
       (SELECT count(DISTINCT seq) FROM kan.bigrading_support) AS sequences;

CREATE OR REPLACE VIEW kan.bigrading_laws AS
SELECT (SELECT bool_and(is_bigraded) FROM kan.bigrading)            AS bigraded,
       (SELECT bool_and(commuting AND marginals_ok AND triangular
                        AND resolves_id AND chain_mobius_ok
                        AND excess_full_id AND e0_squarefree)
          FROM kan.bigrading)                                       AS all_laws,
       (SELECT support_triangular FROM kan.bigrading_triangular)    AS triangular,
       (SELECT occupied_strata FROM kan.bigrading_triangular)       AS strata;
