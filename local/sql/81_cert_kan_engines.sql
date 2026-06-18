-- Unified model, step 81: an honest, growth-robust kan-engines law claim.
--
-- The kan-work sweep found cert.kan_engines_all_true() returning false because the
-- chromatic engine reported all_converge=false. Root cause: kan.sequence_terms was
-- EMPTY (its seeder, 32_kan_sequence_terms.sql, had never been applied to this DB), so
-- the convergence view's top_cum = n_terms test compared the populated chromatic_layer
-- against zero terms. Applying step 32 populates 60 terms each for A000040/45/90; the
-- chromatic engine then converges and the violation clears (violations 1 -> 0).
--
-- The pre-existing claims #4/#29 assert "every kan engine law-view is all-true", which
-- cannot be valid while 4 engines (lithon, shadow, grading, identity_decomposition) are
-- empty (no data) — by design empty != refuted, so those claims stay honestly unverified.
-- This records the verifiable property instead: NO non-empty engine is violated. It does
-- not hardcode the engine counts (avoiding the stale-snapshot trap of claim #274).
-- Idempotent. Requires step 32 applied first (sequence_terms populated).

INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql)
SELECT 'kan_engines',
 '{"checker":"cert.kan_engines_all_true",
   "property":"violations = 0 — every NON-EMPTY engine law-view holds",
   "note":"empty engines (no data) are excluded, not counted as passing; supersedes the all-true intent of #4/#29 which cannot be valid while engines are empty",
   "fix":"applied step 32 (kan.sequence_terms) — cleared the spurious chromatic non-convergence"}'::jsonb,
 'cert: no kan engine law-view is violated — every non-empty kan engine law-view holds (cert.kan_engines_all_true reports violations = 0); empty engines are honestly excluded, not counted as passing',
 'computational','comp_sql',
 $p$SELECT ((evidence->>'violations')::int = 0) AS ok,
    jsonb_build_object('violations', evidence->'violations',
                       'engines_checked', evidence->'engines_checked',
                       'engines_empty', evidence->'engines_empty',
                       'per_engine', evidence->'engines') AS evidence
    FROM cert.kan_engines_all_true()$p$
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement LIKE 'cert: no kan engine law-view is violated%');

DO $$ DECLARE c RECORD; BEGIN
  FOR c IN SELECT id FROM cert.claim WHERE statement LIKE 'cert: no kan engine law-view is violated%'
  LOOP PERFORM cert.check(c.id); END LOOP;
END $$;
