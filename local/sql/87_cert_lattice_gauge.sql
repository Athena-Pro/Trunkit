-- Unified model, step 87: SU(2) lattice-gauge holonomy intake.
--
-- Source: tel-research/yang-mills/YANG_MILLS_SU2_COMPLETE.md. SU(2) links are unit
-- quaternions; the Wilson plaquette (discrete holonomy around an elementary loop) is
--   U_plaq = U_mu * U_nu * U_mu^dagger * U_nu^dagger
-- and the local action is S = 1 - (1/2)Tr(U_plaq) = 1 - Re(U_plaq) (since for a unit
-- quaternion q, (1/2)Tr q = Re q). A connection is FLAT iff every plaquette holonomy
-- is the identity (S = 0).
--
-- Two exact, self-contained comp_sql claims (proof-carrying; quaternion arithmetic
-- recomputed in-DB over the rationals):
--   G1 (valid): the cold/vacuum lattice (all links = 1) is flat -- every plaquette
--               holonomy = identity, S = 0; and q*q^dagger = 1 (inverse correctness).
--   G2 (valid): curvature is REAL -- links i, j give plaquette holonomy = the
--               commutator i j i^-1 j^-1 = -1 (NOT the identity), S = 2. This is the
--               "the detector is not trivially always-flat" sanity check (cf. the
--               strat layer injecting a derivation to confirm beta1 can move).
-- Idempotent.

CREATE SCHEMA IF NOT EXISTS gauge;

-- quaternion product q1*q2, components [a,b,c,d] = a + b i + c j + d k.
CREATE OR REPLACE FUNCTION gauge.qmul(x numeric[], y numeric[]) RETURNS numeric[]
  LANGUAGE sql IMMUTABLE AS $$
  SELECT ARRAY[
    x[1]*y[1] - x[2]*y[2] - x[3]*y[3] - x[4]*y[4],
    x[1]*y[2] + x[2]*y[1] + x[3]*y[4] - x[4]*y[3],
    x[1]*y[3] - x[2]*y[4] + x[3]*y[1] + x[4]*y[2],
    x[1]*y[4] + x[2]*y[3] - x[3]*y[2] + x[4]*y[1]
  ]
$$;

CREATE OR REPLACE FUNCTION gauge.qconj(x numeric[]) RETURNS numeric[]
  LANGUAGE sql IMMUTABLE AS $$ SELECT ARRAY[x[1], -x[2], -x[3], -x[4]] $$;

-- Wilson plaquette holonomy U_mu U_nu U_mu^dagger U_nu^dagger for constant links a,b.
CREATE OR REPLACE FUNCTION gauge.plaquette(a numeric[], b numeric[]) RETURNS numeric[]
  LANGUAGE sql IMMUTABLE AS $$
  SELECT gauge.qmul(gauge.qmul(gauge.qmul(a, b), gauge.qconj(a)), gauge.qconj(b))
$$;

-- G1: cold lattice is flat (plaquette = identity, S = 0); unit-quaternion inverse.
INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql)
SELECT 'lattice_gauge',
 '{"lab":"yang-mills","gauge":"SU(2)","config":"cold/vacuum (all links = 1)",
   "facts":["plaquette holonomy = identity","S = 1 - Re = 0","q*q^dagger = 1"]}'::jsonb,
 'lattice-gauge: the cold SU(2) lattice (all links = identity quaternion) is flat — Wilson plaquette holonomy = identity and action S = 1 - Re = 0; unit-quaternion inverse q*q^dagger = 1 verified',
 'computational','comp_sql',
 $p$WITH id AS (SELECT ARRAY[1,0,0,0]::numeric[] AS q),
      i AS (SELECT ARRAY[0,1,0,0]::numeric[] AS q)
   SELECT (plaq = ARRAY[1,0,0,0]::numeric[] AND s = 0 AND inv = ARRAY[1,0,0,0]::numeric[]) AS ok,
     jsonb_build_object('plaquette',plaq,'action_S',s,'i_times_iconj',inv,
                        'flat', (plaq = ARRAY[1,0,0,0]::numeric[])) AS evidence
   FROM (SELECT gauge.plaquette((SELECT q FROM id),(SELECT q FROM id)) AS plaq,
                1 - (gauge.plaquette((SELECT q FROM id),(SELECT q FROM id)))[1] AS s,
                gauge.qmul((SELECT q FROM i), gauge.qconj((SELECT q FROM i))) AS inv) t$p$
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement LIKE 'lattice-gauge: the cold SU(2) lattice%');

-- G2: curvature is real — commutator of i and j gives plaquette holonomy = -1, S = 2.
INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql)
SELECT 'lattice_gauge',
 '{"lab":"yang-mills","gauge":"SU(2)","config":"links i, j (orthogonal generators)",
   "fact":"plaquette holonomy = i j i^-1 j^-1 = -1 (non-identity) => non-zero curvature, S = 2"}'::jsonb,
 'lattice-gauge: links i and j produce a non-flat plaquette — holonomy = commutator i*j*i^dagger*j^dagger = -1 (not the identity), giving curvature action S = 2 (the holonomy detector is genuinely sensitive, not trivially flat)',
 'computational','comp_sql',
 $p$WITH qi AS (SELECT ARRAY[0,1,0,0]::numeric[] AS q),
      qj AS (SELECT ARRAY[0,0,1,0]::numeric[] AS q)
   SELECT (plaq = ARRAY[-1,0,0,0]::numeric[] AND s = 2) AS ok,
     jsonb_build_object('plaquette',plaq,'action_S',s,
                        'commutator','i*j*i^dagger*j^dagger = -1',
                        'flat', (plaq = ARRAY[1,0,0,0]::numeric[])) AS evidence
   FROM (SELECT gauge.plaquette((SELECT q FROM qi),(SELECT q FROM qj)) AS plaq,
                1 - (gauge.plaquette((SELECT q FROM qi),(SELECT q FROM qj)))[1] AS s) t$p$
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement LIKE 'lattice-gauge: links i and j produce a non-flat plaquette%');

DO $$ DECLARE c RECORD; BEGIN
  FOR c IN SELECT id FROM cert.claim WHERE statement LIKE 'lattice-gauge:%'
  LOOP PERFORM cert.check(c.id); END LOOP;
END $$;
