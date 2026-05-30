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

-- attest everything strat just produced
DO $$ DECLARE c RECORD; BEGIN
  FOR c IN SELECT id FROM cert.claim
           WHERE statement LIKE 'strat:%' OR statement = 'FlatType5 (Type III) duality depth is undefined (infinity)'
  LOOP PERFORM cert.check(c.id); END LOOP;
END $$;
