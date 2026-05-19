-- Unified model, step 66: attest the chromatic height tower (axis 3).
--
-- ht(t) = prime-index of t's largest prime factor; L_n = p_n-smooth
-- localization, M_n = monochromatic layer. Six laws (hash-pinned
-- self-contained checker proofs/chromatic.py):
--   C1 idempotent   L_n . L_n = L_n;
--   C2 filtration   L_n(S) subset L_{next}(S) subset S (nested);
--   C3 smashing     L_m . L_n = L_{min(m,n)}  -- the chromatic TOWER law
--                   (distinguishes a height filtration from a flat grading);
--   C4 layers       M_n = L_n (-) L_{prev} = [t: ht(t)=n]  (fracture);
--   C5 convergence  colim_n L_n = Id_seq ; (+)_n M_n = Id_seq (natural);
--   C6 compatibility L_n commutes with W_i and B_j -- the chromatic tower
--                   is smashing wrt the omega x Omega bigrading, giving a
--                   compatible TRIGRADING.
-- Canonical: naturals(120) chromatic profile = 31 heights summing to 120
-- (sha 6f18e87ac5343999).
--
-- The smashing law + convergence are the genuinely chromatic features the
-- omega/Omega/excess axes lacked (those are flat orthogonal gradings; this
-- is a tower of nested idempotent localizations). Live: kan carries
-- L_chromatic / M_chromatic functors + tables kan.chromatic[_layer]
-- (views *_summary/_convergence/_laws). Driven by tools/cert_formal.py.
-- Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'chromatic',
       '{"height":"ht(t)=pi(largest prime factor)",
         "localization":"L_n=[t:ht<=n] (p_n-smooth)",
         "layer":"M_n=L_n(-)L_{prev} (largest prime = p_n)",
         "laws":["C1_idempotent","C2_filtration","C3_smashing",
                 "C4_layers_fracture","C5_convergence","C6_bigrading_compat"],
         "key":"smashing tower law + chromatic convergence; trigrading with omega x Omega",
         "canonical":"naturals(120) profile=31 heights, sum=120"}'::jsonb,
       'the chromatic height tower is a smashing filtration of idempotent localizations with monochromatic layers, convergence, and compatibility with the omega x Omega bigrading',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'the chromatic height tower is a smashing filtration of idempotent localizations with monochromatic layers, convergence, and compatibility with the omega x Omega bigrading'
);
