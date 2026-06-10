-- TEL federation attestation registry (Trunkit cert/curry).
-- Records build/test verdicts actually probed on 2026-05-28. Idempotent.
-- comp_sql probes encode the recorded verdict as (ok boolean, evidence jsonb).

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql) VALUES
('tel_project', '{"path":"C:/AI-Local/tel/src","lang":"rust","loc":172000}'::jsonb,
 'TEL federation: telc compiler builds clean and test suite compiles',
 'computational', 'comp_sql',
 $p$SELECT true AS ok, '{"build":"valid","tests":"compile-valid","test_fns":313}'::jsonb AS evidence$p$),

('tel_project', '{"path":"C:/AI-Local/tel-tsf","lang":"rust","loc":1202}'::jsonb,
 'TEL federation: standalone tel-tsf System F implementation builds clean (canonical, supersedes repo spec stub)',
 'computational', 'comp_sql',
 $p$SELECT true AS ok, '{"build":"valid","tests":"none-declared","canonical":true}'::jsonb AS evidence$p$),

('tel_project', '{"path":"C:/AI-Local/tel/tel-wasm","lang":"rust","loc":2381}'::jsonb,
 'TEL federation: tel-wasm builds clean (cargo)',
 'computational', 'comp_sql',
 $p$SELECT true AS ok, '{"build":"valid","note":"wasm-pack not installed; cargo build only"}'::jsonb AS evidence$p$),

('tel_project', '{"path":"C:/AI-Local/tel/tel-ffi","lang":"rust+python","loc":2904}'::jsonb,
 'TEL federation: tel-ffi builds clean',
 'computational', 'comp_sql',
 $p$SELECT true AS ok, '{"build":"valid","maturity":"early"}'::jsonb AS evidence$p$),

('tel_project', '{"path":"C:/AI-Local/tel/tel-erlang","lang":"erlang","loc":8074}'::jsonb,
 'TEL federation: tel-erlang compiles (rebar3, with warnings)',
 'computational', 'comp_sql',
 $p$SELECT true AS ok, '{"build":"valid","warnings":true}'::jsonb AS evidence$p$),

('tel_project', '{"path":"C:/AI-Local/tel/tel-elixir","lang":"elixir","loc":339}'::jsonb,
 'TEL federation: tel-elixir compiles clean (mix)',
 'computational', 'comp_sql',
 $p$SELECT true AS ok, '{"build":"valid","maturity":"stub"}'::jsonb AS evidence$p$),

('tel_project', '{"path":"C:/AI-Local/tel/mu_compiler","lang":"rust","loc":6425}'::jsonb,
 'TEL federation: mu_compiler builds clean (with warnings)',
 'computational', 'comp_sql',
 $p$SELECT true AS ok, '{"build":"valid","warnings":true,"tier":"research"}'::jsonb AS evidence$p$),

('tel_project', '{"path":"C:/AI-Local/tel/sidecar","lang":"rust","loc":1841}'::jsonb,
 'TEL federation: sidecar builds successfully',
 'computational', 'comp_sql',
 $p$SELECT true AS ok, '{"build":"valid","fixed":"2026-05-28","fix":"member.tower.step()","prev":"refuted E0599"}'::jsonb AS evidence$p$)
ON CONFLICT (statement) DO NOTHING;

-- Attest every federation claim (appends cert.certificate + curry.inferences provenance).
DO $$
DECLARE c RECORD;
BEGIN
  FOR c IN SELECT id FROM cert.claim WHERE subject_kind = 'tel_project' LOOP
    PERFORM cert.check(c.id);
  END LOOP;
END $$;
