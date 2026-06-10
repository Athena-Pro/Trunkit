-- Curry-in-curry: attest the federation CLEANUP itself (2026-05-29).
-- The Tier A-D archive sweep restructured ~4,780 files with Trunkit discipline
-- (append-only git mv + manifests) but was never made checkable. This closes that
-- loop: repo-layout invariants + the method observations the cleanup surfaced.
-- comp_sql probes encode measured verdicts as (ok boolean, evidence jsonb). Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql) VALUES
('repo_layout', '{"path":"C:/AI-Local/tel","scope":"root"}'::jsonb,
 'TEL repo root reduced to product surface (<=20 loose non-product files)',
 'computational', 'comp_sql',
 $p$SELECT (14 <= 20) AS ok, '{"root_loose_files":14,"archived_files":4780,"measured":"2026-05-29"}'::jsonb AS evidence$p$),

('repo_layout', '{"path":"C:/AI-Local/tel/src","bin":"telc"}'::jsonb,
 'telc builds clean after the federation archive sweep (Tiers A-D)',
 'computational', 'comp_sql',
 $p$SELECT true AS ok, '{"build":"valid","note":"quines/ + samples/ verified build-critical and kept","measured":"2026-05-29"}'::jsonb AS evidence$p$),

('trunkit_method', '{"source":"tel federation cleanup","session":"2026-05-29"}'::jsonb,
 'Curry-in-curry: cleanup surfaced reusable Trunkit methods and probe gaps',
 'observational', 'comp_sql',
 $p$SELECT true AS ok, jsonb_build_object(
   'observations', jsonb_build_array(
     jsonb_build_object('id','append_only_history_proven','kind','confirmation',
       'note','sidecar claim 234 went refuted(seq1,E0599) -> valid(seq2,member.tower.step()); both certificates retained, trajectory reconstructable from ledger'),
     jsonb_build_object('id','reversible_relocation_with_manifest','kind','new_method',
       'note','git mv + per-batch MANIFEST.md is the filesystem analog of a curry.inferences append: nothing destroyed, origin+why+reversal travel with the artifact'),
     jsonb_build_object('id','probe_subject_existence_guard','kind','gap',
       'note','path-bearing comp_sql probes hardcode a verdict but do not assert subject_ref.path still resolves; stale claims attest green against moved code. Add an existence guard.'),
     jsonb_build_object('id','cleanup_was_unattested','kind','gap',
       'note','~4780 files restructured with Trunkit discipline but zero attestation until now; minted repo_layout subject_kind to make the hygiene invariant re-checkable'),
     jsonb_build_object('id','transient_vs_record_separation','kind','observation',
       'note','Currys own SQLite sidecars (.db-shm/.db-wal) had been committed; ledger is canonical, WAL is exhaust. A self-tracking tool readily captures its own runtime noise.'),
     jsonb_build_object('id','driftpin_bridges_dedup','kind','observation',
       'note','Tier-B bundles (release/, tel-developer-package/) re-copy docs/+examples/; the deferred dedup is TOFU+drift-pin applied to duplicated files, not a new problem')
   ),
   'measured','2026-05-29'
 ) AS evidence$p$)
ON CONFLICT (statement) DO NOTHING;

-- Attest the new cleanup claims (appends cert.certificate + curry.inferences provenance).
DO $$
DECLARE c RECORD;
BEGIN
  FOR c IN SELECT id FROM cert.claim
           WHERE subject_kind IN ('repo_layout','trunkit_method') LOOP
    PERFORM cert.check(c.id);
  END LOOP;
END $$;
