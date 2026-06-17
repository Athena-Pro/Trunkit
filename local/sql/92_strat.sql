-- Unified model, step 92: the `strat` stratification layer (cross-lab).
--
-- A thin layer between kan (structure) and cert (verdict), proposed in
-- CROSS_LAB_SHARED_SCHEMA.md after comparing the hypergroup, interlace, and TEL
-- labs. It makes three recurring abstractions first-class:
--   * a graded site (poset)            -> strat.site
--   * an iterated tower + stab. depth   -> strat.tower  (generalises kan_self)
--   * a frontier residual               -> strat.residual (structured cert evidence)
-- The VERDICT is NOT reinvented: strat produces subjects, cert judges them under
-- the same three-valued rule (valid / refuted / unverified) hardened in steps 40 & 79.
--
-- Provenance of the seeded instances:
--   * interlace tower/residual  -- SELF-RECOMPUTING in-DB (BFS below); proof-carrying.
--   * hypergroup tower/residual -- LOADED external results from the numpy program
--       (tools hg_towers.py + TypeIIIInvariants.haar_equation_residual()).
--       Finding surfaced on first contact: core/duality.py `_dualize` is an identity
--       stub, so every duality tower reports depth 0 (README Type I=1 / III=inf
--       are NOT realised) -- recorded honestly below. Idempotent.

CREATE SCHEMA IF NOT EXISTS strat;

CREATE TABLE IF NOT EXISTS strat.site     (id serial PRIMARY KEY, name text UNIQUE, kind text, meta jsonb);
CREATE TABLE IF NOT EXISTS strat.tower    (id serial PRIMARY KEY, site_id int, endofunctor text, max_depth int,
                                           stab_depth int, orbit jsonb, meta jsonb);  -- stab_depth NULL == inf/undefined
CREATE TABLE IF NOT EXISTS strat.residual (id serial PRIMARY KEY, site_id int, object text, metric text,
                                           value double precision, classified_zero boolean, meta jsonb);

-- ---- primitives ------------------------------------------------------------

-- 2-way Morton (interleave low/high nybble) on an 8-bit value; from interlace_lib.js.
CREATE OR REPLACE FUNCTION strat.morton2_8(x int) RETURNS int LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE lo int := x & 15; hi int := (x >> 4) & 15; r int := 0; outpos int := 0; bi int; b int;
BEGIN
  FOR bi IN 0..3 LOOP
    b := (lo >> bi) & 1; r := r | (b << outpos); outpos := outpos + 1;
    b := (hi >> bi) & 1; r := r | (b << outpos); outpos := outpos + 1;
  END LOOP;
  RETURN r;
END $$;

-- BFS closure of {add1, morton2} on the 8-bit space from state 0.
-- Returns the Cayley diameter, reached-count, and per-level new-state counts.
CREATE OR REPLACE FUNCTION strat.bfs_closure_8bit()
RETURNS TABLE(diameter int, reached int, frontier_orbit jsonb) LANGUAGE plpgsql AS $$
DECLARE dist int[]; q int[]; head int := 1; cur int; d int; maxd int := 0; cnt int := 1; nxt int; lvl int[];
BEGIN
  dist := array_fill(-1, ARRAY[256]); dist[1] := 0; q := ARRAY[0];
  lvl := array_fill(0, ARRAY[300]); lvl[1] := 1;
  WHILE head <= array_length(q,1) LOOP
    cur := q[head]; head := head + 1; d := dist[cur+1];
    FOREACH nxt IN ARRAY ARRAY[(cur+1) & 255, strat.morton2_8(cur)] LOOP
      IF dist[nxt+1] = -1 THEN
        dist[nxt+1] := d+1; cnt := cnt+1; IF d+1 > maxd THEN maxd := d+1; END IF;
        q := array_append(q, nxt); lvl[d+2] := lvl[d+2] + 1;
      END IF;
    END LOOP;
  END LOOP;
  RETURN QUERY SELECT maxd, cnt, to_jsonb(lvl[1:maxd+1]);
END $$;

-- The SHARED three-valued depth detector (both labs feed this).
--   'return'   (duality): min L>=1 with orbit[L]=orbit[0]; depth=L-1; NULL = undefined (Type III).
--   'saturate' (closure): last level with new states; NULL if it never closes.
CREATE OR REPLACE FUNCTION strat.tower_depth(orbit jsonb, mode text)
RETURNS int LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE n int := jsonb_array_length(orbit); i int;
BEGIN
  IF mode = 'return' THEN
    FOR i IN 1..n-1 LOOP IF orbit->i = orbit->0 THEN RETURN i-1; END IF; END LOOP;
    RETURN NULL;
  ELSIF mode = 'saturate' THEN
    FOR i IN REVERSE n-1..0 LOOP IF (orbit->>i)::int > 0 THEN RETURN i; END IF; END LOOP;
    RETURN NULL;
  END IF; RETURN NULL;
END $$;

-- ---- instance 1: interlace operator-closure (self-recomputing) -------------

INSERT INTO strat.site(name,kind,meta) VALUES
 ('interlace_8bit_addmorton','operator_reachability','{"ops":["add1","morton2"],"bits":8,"lab":"interlace"}')
ON CONFLICT (name) DO NOTHING;

INSERT INTO strat.tower(site_id, endofunctor, max_depth, stab_depth, orbit, meta)
SELECT s.id, 'add1+morton2', 255, b.diameter, b.frontier_orbit,
       jsonb_build_object('reached', b.reached, 'detector_depth', strat.tower_depth(b.frontier_orbit,'saturate'))
  FROM strat.site s, strat.bfs_closure_8bit() b
 WHERE s.name='interlace_8bit_addmorton' AND NOT EXISTS (SELECT 1 FROM strat.tower t WHERE t.site_id=s.id);

INSERT INTO strat.residual(site_id, object, metric, value, classified_zero, meta)
SELECT s.id, 'closure', 'unreachable_states', (256 - b.reached), true, '{}'::jsonb
  FROM strat.site s, strat.bfs_closure_8bit() b
 WHERE s.name='interlace_8bit_addmorton' AND NOT EXISTS (SELECT 1 FROM strat.residual r WHERE r.site_id=s.id);

INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql)
SELECT 'operator_graph','{"site":"interlace_8bit_addmorton","lab":"interlace"}'::jsonb,
 'strat: {add1, 2-way Morton} 8-bit Cayley diameter = 61 and the set generates all 256 states (closure residual = 0)',
 'computational','comp_sql',
 $p$SELECT (diameter = 61 AND reached = 256) AS ok,
    jsonb_build_object('diameter',diameter,'reached',reached,
                       'detector_depth', strat.tower_depth(frontier_orbit,'saturate')) AS evidence
    FROM strat.bfs_closure_8bit()$p$
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement LIKE 'strat: {add1, 2-way Morton} 8-bit Cayley%');

-- ---- instance 2: hypergroup duality (loaded; surfaced the _dualize stub) ----

INSERT INTO strat.site(name,kind,meta) VALUES
 ('hg_jacobi_so3','duality','{"lab":"hypergroup","type":"I","readme_depth":1}'),
 ('hg_flattype5', 'duality','{"lab":"hypergroup","type":"III","readme_depth":"undefined"}')
ON CONFLICT (name) DO NOTHING;

-- compact orbits (level signature: size + characters-present); identical levels => stub.
INSERT INTO strat.tower(site_id, endofunctor, max_depth, stab_depth, orbit, meta)
SELECT s.id, 'D (duality)', 4, strat.tower_depth(orb,'return'), orb,
       jsonb_build_object('dualize','IDENTITY STUB (returns input relabeled)',
                          'computed_depth', strat.tower_depth(orb,'return'),
                          'note','depth=0 is a stub artifact, not real Type II self-duality')
FROM (VALUES ('hg_jacobi_so3','["c9","c9","c9","c9","c9"]'::jsonb),
             ('hg_flattype5', '["u5","u5","u5","u5","u5"]'::jsonb)) v(nm,orb)
JOIN strat.site s ON s.name=v.nm
WHERE NOT EXISTS (SELECT 1 FROM strat.tower t WHERE t.site_id=s.id);

INSERT INTO strat.residual(site_id, object, metric, value, classified_zero, meta)
SELECT s.id, 'FlatType5', 'haar_equation_residual', 0.242718, true,
       '{"interpretation":"0 = exact invariant measure (Type I/II); >0 = Type III frontier"}'::jsonb
FROM strat.site s WHERE s.name='hg_flattype5'
  AND NOT EXISTS (SELECT 1 FROM strat.residual r WHERE r.site_id=s.id AND r.metric='haar_equation_residual');

-- valid: document the stub gap (re-checkable)
INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql)
SELECT 'duality_depth','{"lab":"hypergroup"}'::jsonb,
 'strat: hypergroup duality operator D is an identity stub — every tower reports stab_depth=0, so README Type I depth=1 / Type III depth=infinity are NOT realized by the code',
 'computational','comp_sql',
 $p$SELECT (count(*)=2 AND bool_and(stab_depth=0)) AS ok,
    jsonb_build_object('towers',count(*),'all_depth_0',bool_and(stab_depth=0),'cause','_dualize identity stub') AS evidence
    FROM strat.tower t JOIN strat.site s ON s.id=t.site_id WHERE s.kind='duality'$p$
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement LIKE 'strat: hypergroup duality operator D is an identity stub%');

-- unverified: the Type III frontier cannot be determined while dualize is a stub
INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql)
SELECT 'duality_depth','{"lab":"hypergroup","hypergroup":"FlatType5","type":"III"}'::jsonb,
 'FlatType5 (Type III) duality depth is undefined (infinity)','formal','formal_external', NULL
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement = 'FlatType5 (Type III) duality depth is undefined (infinity)');

-- valid: the real frontier residual (Type III has no exact invariant measure)
INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql)
SELECT 'frontier_residual','{"lab":"hypergroup","hypergroup":"FlatType5"}'::jsonb,
 'strat: FlatType5 Haar-equation residual > 0 (Type III — no exact invariant measure exists)',
 'computational','comp_sql',
 $p$SELECT (value > 1e-6) AS ok,
    jsonb_build_object('residual',value,'classified_zero_case',classified_zero) AS evidence
    FROM strat.residual WHERE metric='haar_equation_residual'$p$
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement LIKE 'strat: FlatType5 Haar-equation residual%');

-- ---- instance 3: Whitney stratification of algebraic varieties (self-recomputing) ----
--
-- From paper-whitney-stratifications (WHITNEY_STRATIFICATION_MAPPING.md). The paper's
-- headline (Part VII §7.1) is that Whitney conditions A/B come "for free from the
-- certificate functor" -- "1000+ lines -> ~50 lines, verification = type check". That
-- regularity claim is NOT checkable here: it needs limits of tangent planes / secant
-- lines, which this layer does not compute. What IS exactly recomputable in-DB is the
-- skeleton the paper builds on (Part IV §4.2): the Jacobian-rank stratification of an
-- affine variety V(f) and its canonical filtration by iterated singular locus.
--
-- We attest that skeleton (proof-carrying, comp_sql) on the paper's two named examples
-- (Part II §2.3): the cone and the Whitney umbrella -- and record the regularity claim
-- honestly as UNVERIFIED (the third claims-outrun-implementation finding's successor:
-- the README's "automatic Whitney A/B" outruns any in-DB checker).

-- gradient of f = x^2 + y^2 - z^2 (the cone) at an integer point
CREATE OR REPLACE FUNCTION strat.cone_grad(x int, y int, z int) RETURNS int[]
  LANGUAGE sql IMMUTABLE AS $$ SELECT ARRAY[2*x, 2*y, -2*z] $$;
CREATE OR REPLACE FUNCTION strat.on_cone(x int, y int, z int) RETURNS boolean
  LANGUAGE sql IMMUTABLE AS $$ SELECT x*x + y*y - z*z = 0 $$;

-- gradient of f = x^2 - y^2*z (the Whitney umbrella) at an integer point
CREATE OR REPLACE FUNCTION strat.umbrella_grad(x int, y int, z int) RETURNS int[]
  LANGUAGE sql IMMUTABLE AS $$ SELECT ARRAY[2*x, -2*y*z, -y*y] $$;
CREATE OR REPLACE FUNCTION strat.on_umbrella(x int, y int, z int) RETURNS boolean
  LANGUAGE sql IMMUTABLE AS $$ SELECT x*x - y*y*z = 0 $$;

-- rank of a hypersurface Jacobian (a single gradient row): 0 if it vanishes, else 1.
CREATE OR REPLACE FUNCTION strat.vrank(g int[]) RETURNS int
  LANGUAGE sql IMMUTABLE AS $$ SELECT CASE WHEN g[1]=0 AND g[2]=0 AND g[3]=0 THEN 0 ELSE 1 END $$;

INSERT INTO strat.site(name,kind,meta) VALUES
 ('whitney_cone','algebraic_variety',
  '{"lab":"whitney","f":"x^2+y^2-z^2","ambient_dim":2,"sing":"{apex}","example":"WHITNEY_STRATIFICATION_MAPPING.md Part II 2.3 (1)"}'),
 ('whitney_umbrella','algebraic_variety',
  '{"lab":"whitney","f":"x^2-y^2*z","ambient_dim":2,"sing":"z-axis","example":"WHITNEY_STRATIFICATION_MAPPING.md Part II 2.3 (2)"}')
ON CONFLICT (name) DO NOTHING;

-- tower = canonical filtration by iterated singular locus; orbit = dimension sequence,
-- stab_depth = number of proper substrata steps (cone [2,0]->1, umbrella [2,1]->1).
INSERT INTO strat.tower(site_id, endofunctor, max_depth, stab_depth, orbit, meta)
SELECT s.id, 'Sing (iterated singular locus)', 2, 1, v.orbit::jsonb,
       jsonb_build_object('filtration_dims', v.orbit,
                          'note','depth = #strata - 1; sing-locus is smooth so the filtration stabilises after one step')
FROM (VALUES ('whitney_cone','[2,0]'), ('whitney_umbrella','[2,1]')) v(nm,orbit)
JOIN strat.site s ON s.name=v.nm
WHERE NOT EXISTS (SELECT 1 FROM strat.tower t WHERE t.site_id=s.id);

-- residual = proper-frontier defect: max(0, dim(Sing) - (dim(X)-1)). 0 = Sing has
-- codim >=1 in X (the frontier condition holds, classified case).
INSERT INTO strat.residual(site_id, object, metric, value, classified_zero, meta)
SELECT s.id, 'singular_locus', 'frontier_codim_defect', 0.0, true,
       jsonb_build_object('dim_X',2,'dim_Sing',v.dsing,
                          'interpretation','0 = singular locus is a proper (lower-dim) substratum; frontier condition holds')
FROM (VALUES ('whitney_cone',0), ('whitney_umbrella',1)) v(nm,dsing)
JOIN strat.site s ON s.name=v.nm
WHERE NOT EXISTS (SELECT 1 FROM strat.residual r WHERE r.site_id=s.id AND r.metric='frontier_codim_defect');

-- valid (comp_sql, proof-carrying): re-derive the cone's Jacobian-rank stratification.
-- gradient vanishes exactly at the apex (rank 0, singular) and is nonzero at sample
-- smooth points on V (rank 1) => Sing = {apex}, dim 0, codim 2 in the dim-2 cone.
INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql)
SELECT 'algebraic_variety','{"lab":"whitney","variety":"cone","f":"x^2+y^2-z^2"}'::jsonb,
 'strat: cone x^2+y^2-z^2 Jacobian-rank stratification — gradient vanishes only at the apex (rank 0) and is full-rank on the smooth surface (rank 1), so Sing = {apex}, filtration dims [2,0]',
 'computational','comp_sql',
 $p$SELECT (apex_on AND apex_rank=0 AND smooth_on AND smooth_full) AS ok,
    jsonb_build_object('apex_on_variety',apex_on,'apex_rank',apex_rank,
                       'smooth_pts_on_variety',smooth_on,'smooth_pts_full_rank',smooth_full,
                       'filtration_dims', ARRAY[2,0]) AS evidence
    FROM (
      SELECT strat.on_cone(0,0,0) AS apex_on,
             strat.vrank(strat.cone_grad(0,0,0)) AS apex_rank,
             (SELECT bool_and(strat.on_cone(x,y,z))      FROM (VALUES (3,4,5),(5,12,13),(8,6,10)) v(x,y,z)) AS smooth_on,
             (SELECT bool_and(strat.vrank(strat.cone_grad(x,y,z))=1) FROM (VALUES (3,4,5),(5,12,13),(8,6,10)) v(x,y,z)) AS smooth_full
    ) t$p$
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement LIKE 'strat: cone x^2+y^2-z^2 Jacobian-rank%');

-- valid (comp_sql, proof-carrying): the Whitney umbrella. Gradient vanishes exactly on
-- the z-axis (rank 0) and is full-rank on the smooth surface (rank 1) => Sing = z-axis,
-- dim 1, codim 1; filtration dims [2,1] — a different orbit, so the tower discriminates.
INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql)
SELECT 'algebraic_variety','{"lab":"whitney","variety":"umbrella","f":"x^2-y^2*z"}'::jsonb,
 'strat: Whitney umbrella x^2-y^2*z Jacobian-rank stratification — gradient vanishes only on the z-axis (rank 0) and is full-rank on the smooth surface (rank 1), so Sing = z-axis, filtration dims [2,1]',
 'computational','comp_sql',
 $p$SELECT (axis_on AND axis_rank0 AND smooth_on AND smooth_full) AS ok,
    jsonb_build_object('zaxis_on_variety',axis_on,'zaxis_all_rank0',axis_rank0,
                       'smooth_pts_on_variety',smooth_on,'smooth_pts_full_rank',smooth_full,
                       'filtration_dims', ARRAY[2,1]) AS evidence
    FROM (
      SELECT (SELECT bool_and(strat.on_umbrella(0,0,t))            FROM (VALUES (-1),(0),(1),(5)) a(t)) AS axis_on,
             (SELECT bool_and(strat.vrank(strat.umbrella_grad(0,0,t))=0) FROM (VALUES (-1),(0),(1),(5)) a(t)) AS axis_rank0,
             (SELECT bool_and(strat.on_umbrella(x,y,z))            FROM (VALUES (2,1,4),(3,1,9),(2,2,1)) v(x,y,z)) AS smooth_on,
             (SELECT bool_and(strat.vrank(strat.umbrella_grad(x,y,z))=1) FROM (VALUES (2,1,4),(3,1,9),(2,2,1)) v(x,y,z)) AS smooth_full
    ) t$p$
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement LIKE 'strat: Whitney umbrella x^2-y^2*z Jacobian-rank%');

-- valid (comp_sql): Whitney regularity of the CONE at the apex, proved via homogeneity.
-- The cone is homogeneous of degree 2, so Euler's identity gives x . grad f = 2 f, which
-- is 0 on the variety. The tangent plane at a smooth point x is grad f^perp, so the radial
-- direction x (= the secant from the apex to x) lies in the tangent plane. Hence every
-- apex-secant is tangent => Whitney condition B holds at the apex; Whitney A is automatic
-- because the lower stratum is the 0-dim apex (tangent space {0} is contained in anything).
-- The probe confirms the Euler identity x.grad f = 2f = 0 at sample cone points.
--
-- NOTE: the Whitney UMBRELLA x^2 - y^2 z is NOT homogeneous (degrees 2 and 3 mix), so this
-- argument does not apply. Its Whitney regularity at the origin is the subtle textbook case
-- and is deliberately NOT asserted here (deferred to a careful tangent/secant-limit checker),
-- rather than claimed unverified. This is the honest replacement for the paper's blanket
-- "Whitney A/B automatic" claim (WHITNEY_STRATIFICATION_MAPPING.md Part VII 7.1).
INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql)
SELECT 'whitney_regularity',
 '{"lab":"whitney","variety":"cone","method":"Euler homogeneity (deg 2): x.grad f = 2f = 0 on V => radial secant in tangent plane",
   "scope":"cone apex only; umbrella deferred (non-homogeneous)"}'::jsonb,
 'strat: the cone x^2+y^2-z^2 is Whitney-regular at the apex — Whitney A is automatic (0-dim lower stratum) and Whitney B holds by homogeneity (every apex-secant x satisfies x.grad f = 2f = 0, so it lies in the tangent plane)',
 'computational','comp_sql',
 $p$WITH pts(x,y,z) AS (VALUES (3,4,5),(5,12,13),(8,6,10),(1,0,1))
   SELECT (bool_and(radial_dot_grad = 0 AND on_variety) AND count(*) = 4) AS ok,
     jsonb_build_object('checked', count(*),
       'all_radial_in_tangent', bool_and(radial_dot_grad = 0),
       'argument','homogeneous deg 2 => x.grad f = 2f = 0 on V => radial (apex-secant) lies in tangent plane => Whitney B at apex') AS evidence
   FROM (SELECT (2*x*x + 2*y*y - 2*z*z) AS radial_dot_grad, (x*x + y*y - z*z = 0) AS on_variety
           FROM pts) t$p$
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement LIKE 'strat: the cone x^2+y^2-z^2 is Whitney-regular at the apex%');

-- attest everything strat just produced
DO $$ DECLARE c RECORD; BEGIN
  FOR c IN SELECT id FROM cert.claim
           WHERE statement LIKE 'strat:%' OR statement = 'FlatType5 (Type III) duality depth is undefined (infinity)'
  LOOP PERFORM cert.check(c.id); END LOOP;
END $$;
