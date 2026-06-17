-- Unified model, step 89: empirical Yang-Mills (SU(2) lattice) intake.
--
-- Source: tel-research/yang-mills/ (YANG_MILLS_SU2_COMPLETE.md + committed CSVs
-- wilson_loops.csv, mass_gap_data.csv). This is empirical physics data, not a
-- stratification, so it lands as direct cert.claims (not a strat site/tower).
--
-- HONESTY IS THE POINT HERE. The data does NOT support the framework's narrative,
-- and the intake records that faithfully:
--   * Y1 (valid)      : the BPS action bound S >= 8*pi^2*|Q| holds for the reported
--                       instanton (exact arithmetic).
--   * Y2 (valid NULL) : the 25-point Wilson-loop dataset shows NO area law -- the
--                       loop value is area-INDEPENDENT (slope ~ 0, R^2 ~ 0). The
--                       "Day-26 confinement / area-law" reading is NOT in the data.
--   * Y3 (valid)      : the implied mass-gap decay constant between the only TWO
--                       correlation points is exactly m = 0.5731 -- exact arithmetic,
--                       loudly caveated as not a meaningful fit (n=2).
--   * Y4 (unverified) : the Yang-Mills existence-and-mass-gap problem (Clay
--                       Millennium) is NOT resolved -- finite-lattice numerics are
--                       not a continuum theorem.
-- All probes are proof-carrying: the data travels embedded in the claim and the
-- verdict is recomputed in-DB. Idempotent.

-- Y1: BPS action bound S >= 8*pi^2*|Q| for the reported Q=1 instanton (S=128.76).
INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql)
SELECT 'yang_mills_instanton',
 '{"lab":"yang-mills","gauge":"SU(2)","Q":1,"S":128.76,"source":"YANG_MILLS_SU2_COMPLETE.md Phase 3 + BPS check"}'::jsonb,
 'yang-mills: reported SU(2) instanton action S=128.76 satisfies the BPS bound S >= 8*pi^2*|Q| (= 78.96 for Q=1) — exceeds it due to lattice discretization',
 'computational','comp_sql',
 $p$SELECT (s >= bound) AS ok,
    jsonb_build_object('S',s,'Q',1,'bound_8pi2',round(bound::numeric,3),
                       'exceeds_by',round((s-bound)::numeric,3),
                       'note','S > continuum BPS bound is expected on a discretized lattice') AS evidence
    FROM (SELECT 128.76::double precision AS s, 8*pi()*pi() AS bound) t$p$
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement LIKE 'yang-mills: reported SU(2) instanton action%');

-- Y2: the honest null — NO area law in the committed 25-point Wilson-loop dataset.
-- A genuine area law would show log(Value) decreasing linearly with Area (slope<0,
-- high R^2). Here Value ~ 0.424 regardless of Area => slope ~ 0, R^2 ~ 0.
INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql)
SELECT 'yang_mills_wilson',
 '{"lab":"yang-mills","dataset":"wilson_loops.csv","n":25,
   "finding":"area-independent loop value; no confinement/area-law signal",
   "refutes":"framework Day-26 roadmap reading of these data as an area law"}'::jsonb,
 'yang-mills: the 25-point Wilson-loop dataset shows NO area law — the loop value is area-independent (|regr_slope(Value,Area)| < 1e-3 and R^2 < 0.2), so this data carries no confinement signal',
 'computational','comp_sql',
 $p$WITH w(area,val) AS (VALUES
    (1,0.42769336),(2,0.42449886),(3,0.42297602),(4,0.42780985),(5,0.42443185),
    (2,0.42369971),(4,0.42005367),(6,0.42179615),(8,0.42133861),(10,0.42542317),
    (3,0.41824796),(6,0.42200384),(9,0.42213706),(12,0.42352240),(15,0.42626215),
    (4,0.42608215),(8,0.42484003),(12,0.42810244),(16,0.42192978),(20,0.42570060),
    (5,0.42493398),(10,0.42469186),(15,0.42666557),(20,0.42349879),(25,0.42503558))
  SELECT (abs(slope) < 1e-3 AND r2 < 0.2) AS ok,
    jsonb_build_object('n',cnt,'slope_value_vs_area',round(slope::numeric,6),
                       'r2',round(r2::numeric,4),'mean_value',round(mv::numeric,5),
                       'finding','area-INDEPENDENT loop value; no area law present') AS evidence
    FROM (SELECT regr_slope(val,area) AS slope, regr_r2(val,area) AS r2,
                 count(*) AS cnt, avg(val) AS mv FROM w) t$p$
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement LIKE 'yang-mills: the 25-point Wilson-loop dataset shows NO area law%');

-- Y3: exact decay constant between the only two correlation points (n=2, caveated).
INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql)
SELECT 'yang_mills_massgap_data',
 '{"lab":"yang-mills","dataset":"mass_gap_data.csv","n_points":2,
   "caveat":"two points only; exact arithmetic, NOT a statistically meaningful fit or mass-gap evidence"}'::jsonb,
 'yang-mills: the implied correlation decay constant between the only two data points (t=4,5) is exactly m = log C(4) - log C(5) = 0.5731 — exact arithmetic, not a fit (n=2)',
 'computational','comp_sql',
 $p$SELECT (abs(m - 0.573096) < 1e-4) AS ok,
    jsonb_build_object('logC_t4',-0.9999346491350642,'logC_t5',-1.5730305932573745,
                       'decay_constant_m',round(m::numeric,6),'n_points',2,
                       'caveat','exact between two points; carries no fit quality and is not mass-gap evidence') AS evidence
    FROM (SELECT (-0.9999346491350642) - (-1.5730305932573745) AS m) t$p$
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement LIKE 'yang-mills: the implied correlation decay constant%');

-- NOTE (pruned 2026-06-17): an earlier draft carried an explicit "the Yang-Mills mass
-- gap (Clay Millennium) is NOT resolved here" claim as formal_external -> unverified.
-- Per the no-unverified-commit policy it is removed: it is a genuinely open problem that
-- no bounded work validates, and its scope is already carried by the VALID claims above
-- (Y1 BPS bound, Y2 no-area-law, Y3 two-point caveat). The honesty lives in those, not in
-- a permanently-grey placeholder.

-- attest everything this file produced
DO $$ DECLARE c RECORD; BEGIN
  FOR c IN SELECT id FROM cert.claim WHERE statement LIKE 'yang-mills:%'
  LOOP PERFORM cert.check(c.id); END LOOP;
END $$;
