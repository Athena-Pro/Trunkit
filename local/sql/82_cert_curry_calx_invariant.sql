-- Unified model, step 82: a growth-robust invariant for the curry_to_calx functor.
--
-- The Tier-2 outrun sweep found claim #274 ("curry_to_calx maps exactly 19 objects")
-- is REFUTED: the functor now maps 20 objects. Inspection shows all 20 are clean
-- calx_X -> X renamings (no junk) — i.e. the "19" was a stale snapshot count, not a
-- real invariant, and the functor legitimately grew by one. A hardcoded count is a
-- brittle claim that goes stale on every addition.
--
-- This supersedes it with the STRUCTURAL property the functor actually guarantees and
-- which survives growth: every mapped object's source is the calx_-prefixed name of its
-- target. Proof-carrying comp_sql over the live map. (The old #274 is left in the ledger
-- as an honest stale-count finding for the maintainer to retire.) Idempotent.

INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql)
SELECT 'kan_functor',
 '{"functor":"curry_to_calx","supersedes":"claim 274 (stale exact-count snapshot: said 19, now 20)",
   "invariant":"src = ''calx_'' || tgt for every mapped object — robust to growth"}'::jsonb,
 'cert: the curry_to_calx functor is a clean calx-prefix renaming — every object it maps has src = ''calx_'' || tgt (growth-robust; supersedes the stale exact-19 count)',
 'computational','comp_sql',
 $p$SELECT (bool_and(src_object = 'calx_' || tgt_object) AND count(*) > 0) AS ok,
    jsonb_build_object('objects_mapped', count(*),
                       'all_calx_prefixed', bool_and(src_object = 'calx_' || tgt_object),
                       'note','structural invariant replaces the brittle exact-19 snapshot; functor currently maps this many objects, all clean renamings') AS evidence
    FROM kan.functor_object_map WHERE functor = 'curry_to_calx'$p$
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement LIKE 'cert: the curry_to_calx functor is a clean calx-prefix renaming%');

DO $$ DECLARE c RECORD; BEGIN
  FOR c IN SELECT id FROM cert.claim WHERE statement LIKE 'cert: the curry_to_calx functor is a clean calx-prefix renaming%'
  LOOP PERFORM cert.check(c.id); END LOOP;
END $$;

-- Close out the stale snapshot it supersedes: mark claim #274 refuted (its probe
-- recomputes the live map count = 20 != asserted 19). Recorded honestly rather than
-- left grey; the corrected invariant above is the replacement.
DO $$ DECLARE c RECORD; BEGIN
  FOR c IN SELECT id FROM cert.claim WHERE statement = 'kan functor curry_to_calx maps exactly 19 objects'
  LOOP PERFORM cert.check(c.id); END LOOP;
END $$;
