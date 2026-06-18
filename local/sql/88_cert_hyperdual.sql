-- Unified model, step 88: hyper-dual automatic-differentiation intake.
--
-- Source: tel-research/hyperdual/ (HYPERDUAL_ALGEBRA_GUIDE.md). A hyper-dual
-- number z = a + b*eps1 + c*eps2 + d*eps1*eps2 with eps1^2=eps2^2=0, eps1*eps2!=0
-- carries a function value plus first and second derivatives exactly:
--   f(x + y*eps1 + z*eps2 + w*eps1*eps2)
--     = f(x) + f'(x)y eps1 + f'(x)z eps2 + [f''(x)yz + f'(x)w] eps1*eps2.
-- Setting x = (val, 1, 1, 0) ("variable"), the eps1/eps2 slots read off f'(val)
-- and the eps1*eps2 slot reads off f''(val) -- with NO finite differences.
--
-- This is exact rational arithmetic, so it is proof-carrying comp_sql: the probe
-- recomputes x^2 and x^3 by hyper-dual multiplication in-DB and checks the
-- derivative slots against the known closed-form values. Idempotent.

CREATE SCHEMA IF NOT EXISTS hd;

-- hyper-dual product, components [a, b, c, d] = a + b*eps1 + c*eps2 + d*eps1*eps2.
-- (a+..)(a'+..) = aa' + (ab'+ba')eps1 + (ac'+ca')eps2 + (ad'+da'+bc'+cb')eps1eps2.
CREATE OR REPLACE FUNCTION hd.mul(x numeric[], y numeric[]) RETURNS numeric[]
  LANGUAGE sql IMMUTABLE AS $$
  SELECT ARRAY[
    x[1]*y[1],
    x[1]*y[2] + x[2]*y[1],
    x[1]*y[3] + x[3]*y[1],
    x[1]*y[4] + x[4]*y[1] + x[2]*y[3] + x[3]*y[2]
  ]
$$;

-- valid (comp_sql, proof-carrying): forward-mode AD via hyper-dual multiplication
-- reproduces f, f', f'' exactly for f=x^3 at x=2 (8,12,12) and f=x^2 at x=5 (25,10,2).
INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql)
SELECT 'hyperdual_ad',
 '{"lab":"hyperdual","source":"HYPERDUAL_ALGEBRA_GUIDE.md",
   "checks":[{"f_expr":"x^3","x":2,"f":8,"f1":12,"f2":12},
             {"f_expr":"x^2","x":5,"f":25,"f1":10,"f2":2}],
   "note":"variable seed (val,1,1,0); eps1 and eps2 slots read off f1(val), eps1eps2 slot reads off f2(val)"}'::jsonb,
 'hyperdual: forward-mode automatic differentiation (hyper-dual multiplication, eps^2=0) reproduces f, f'' and f'''' exactly — x^3 at 2 -> (8,12,12) and x^2 at 5 -> (25,10,2)',
 'computational','comp_sql',
 $p$WITH x3 AS (SELECT hd.mul(hd.mul(ARRAY[2,1,1,0]::numeric[], ARRAY[2,1,1,0]::numeric[]),
                               ARRAY[2,1,1,0]::numeric[]) AS v),
      x2 AS (SELECT hd.mul(ARRAY[5,1,1,0]::numeric[], ARRAY[5,1,1,0]::numeric[]) AS v)
   SELECT (x3.v = ARRAY[8,12,12,12]::numeric[] AND x2.v = ARRAY[25,10,10,2]::numeric[]) AS ok,
     jsonb_build_object(
       'x3_at_2', jsonb_build_object('value',x3.v[1],'d1_eps1',x3.v[2],'d1_eps2',x3.v[3],'d2_eps1eps2',x3.v[4]),
       'x2_at_5', jsonb_build_object('value',x2.v[1],'d1_eps1',x2.v[2],'d1_eps2',x2.v[3],'d2_eps1eps2',x2.v[4]),
       'expected', '[x^3:(8,12,12,12), x^2:(25,10,10,2)] (eps1=eps2=f''; eps1eps2=f'''')') AS evidence
   FROM x3, x2$p$
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement LIKE 'hyperdual: forward-mode automatic differentiation%');

DO $$ DECLARE c RECORD; BEGIN
  FOR c IN SELECT id FROM cert.claim WHERE statement LIKE 'hyperdual:%'
  LOOP PERFORM cert.check(c.id); END LOOP;
END $$;
