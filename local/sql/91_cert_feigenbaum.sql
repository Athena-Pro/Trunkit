-- Unified model, step 91: attest a corrected sibling-project (TEL) claim.
--
-- The 2026-05-29 audit found TEL docs asserting Feigenbaum delta is
-- "construction-dependent" with sine=4.5410 / cubic=4.7783 -- presented as
-- evidence that the *map* changes the value. That is wrong: by Feigenbaum
-- universality, delta is fixed by the ORDER z of the critical point, not the
-- functional form. logistic/sine/cubic all have a quadratic maximum (z=2) and
-- all converge to 4.6692; the cited values were under-converged transients.
-- TEL docs were corrected; this file makes the correction re-checkable in-DB
-- by recomputing the period-doubling cascade itself (proof-carrying). Idempotent.

-- f^{2^n}(0.5)-0.5 for f(x)=r*(1-|2x-1|^z); critical point 0.5 has order z.
CREATE OR REPLACE FUNCTION cert.feig_g(z numeric, r double precision, n int)
RETURNS double precision LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE x double precision := 0.5; i int; k int := (1 << n);
BEGIN
  FOR i IN 1..k LOOP x := r * (1 - abs(2*x-1) ^ z); END LOOP;
  RETURN x - 0.5;
END $$;

-- first superstable root of g(.,n) in (lo,hi): scan for a sign change, bisect.
CREATE OR REPLACE FUNCTION cert.feig_findR(z numeric, n int,
                                            lo double precision, hi double precision, scan int)
RETURNS double precision LANGUAGE plpgsql AS $$
DECLARE step double precision; pr double precision; prev double precision;
        r double precision; cur double precision;
        a double precision; b double precision; m double precision;
        ga double precision; gm double precision; i int; j int;
BEGIN
  step := (hi-lo)/scan; pr := lo+step*0.5; prev := cert.feig_g(z,pr,n);
  FOR i IN 1..scan-1 LOOP
    r := lo+step*(i+0.5); cur := cert.feig_g(z,r,n);
    IF (prev<0) <> (cur<0) THEN
      a:=pr; b:=r; ga:=cert.feig_g(z,a,n);
      FOR j IN 1..80 LOOP
        m := 0.5*(a+b); gm := cert.feig_g(z,m,n);
        IF (ga<0)<>(gm<0) THEN b:=m; ELSE a:=m; ga:=gm; END IF;
      END LOOP;
      RETURN 0.5*(a+b);
    END IF;
    prev:=cur; pr:=r;
  END LOOP;
  RETURN NULL;
END $$;

-- Feigenbaum delta for maximum order z, via the superstable cascade.
CREATE OR REPLACE FUNCTION cert.feigenbaum_delta(z numeric, depth int DEFAULT 6)
RETURNS double precision LANGUAGE plpgsql AS $$
DECLARE R double precision[]; n int; gap double precision; Rn double precision;
BEGIN
  R[0] := 0.5;                                       -- period-1 superstable: f(0.5)=r=0.5
  R[1] := cert.feig_findR(z,1,0.5001,0.999,4000);
  FOR n IN 2..depth LOOP
    gap := R[n-1]-R[n-2];
    Rn  := cert.feig_findR(z,n, R[n-1]+gap*0.02, R[n-1]+gap*0.98, 4000);
    EXIT WHEN Rn IS NULL;
    R[n] := Rn;
  END LOOP;
  RETURN (R[depth-1]-R[depth-2])/(R[depth]-R[depth-1]);
END $$;

-- Checkable claim: re-derives both universality classes from scratch.
INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'tel_claim',
       '{"project":"TEL","docs":["CONSTRUCTION_BRIDGES.md","ARCHIMEDEAN_SPECIFICITY.md","DAY29_COMPLETE_SYNTHESIS.md","TRANSCENDENTAL_ANALYSIS.md","TRANSCENDENTAL_SUMMARY.md"],"corrected":"2026-05-29"}'::jsonb,
       'TEL Feigenbaum claim corrected: delta is universal within a universality class (z=2 -> 4.669, refuting the cited sine=4.5410/cubic=4.7783) and varies only with the order z of the maximum (z=4 -> 7.285)',
       'computational', 'comp_sql',
       $p$SELECT (d2 BETWEEN 4.60 AND 4.75 AND d4 BETWEEN 7.0 AND 7.5) AS ok,
                 jsonb_build_object(
                   'delta_z2', round(d2::numeric,4), 'delta_z4', round(d4::numeric,4),
                   'true_z2', 4.66920, 'true_z4', 7.28469,
                   'within_class_universal', (d2 BETWEEN 4.60 AND 4.75),
                   'refutes', 'sine=4.5410 / cubic=4.7783 (under-converged transients); within-class delta is universal',
                   'fix', 'order of the critical point sets the value, not the functional form'
                 ) AS evidence
            FROM (SELECT cert.feigenbaum_delta(2,6) AS d2, cert.feigenbaum_delta(4,6) AS d4) t$p$
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement LIKE 'TEL Feigenbaum claim corrected:%');

SELECT cert.check(id) IS NOT NULL AS attested FROM cert.claim WHERE statement LIKE 'TEL Feigenbaum claim corrected:%';
