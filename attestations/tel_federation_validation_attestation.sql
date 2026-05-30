-- Curry-in-curry / kan-in-kan: attest the 2026-05-29 validation/contradiction
-- passes over the federation cert ledger (tool-in-tool reflexive audit).
--
-- After the cert.standing LEFT JOIN fix surfaced 36 never-checked claims, a
-- cert.check sweep over them exposed that the federation's COMPUTATION layers
-- (kan engines, curry functions/constants) are unpopulated, while the
-- attestation layer asserts structure against populated engines. The kan-engine
-- bridge then read "empty" as "violated", manufacturing false contradictions.
-- Fixed in 79_cert_kan_engines.sql (three-valued: empty -> unverified). This
-- closes the loop: records the methodological findings + a regression guard.
-- comp_sql probes; idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql) VALUES

-- (A) Re-checkable soundness invariant: the kan-engine bridge must never report
--     refuted on emptiness alone (refuted requires a genuine violation).
('cert_soundness', '{"function":"cert.kan_engines_all_true","property":"empty != refuted"}'::jsonb,
 'kan-engine bridge reports refuted only on a genuine law violation, never on an empty/unpopulated engine (three-valued honesty)',
 'computational', 'comp_sql',
 $p$SELECT NOT (ok IS FALSE AND (evidence->>'violations')::int = 0) AS ok, evidence
      FROM cert.kan_engines_all_true()$p$),

-- (B) The reflexive findings from the tool-in-tool passes.
('trunkit_method', '{"source":"federation validation/contradiction passes","session":"2026-05-29"}'::jsonb,
 'kan-in-kan + curry-in-curry: validation passes surfaced claims-outrun-data and a recurring honest-null antipattern',
 'observational', 'comp_sql',
 $p$SELECT true AS ok, jsonb_build_object(
   'observations', jsonb_build_array(
     jsonb_build_object('id','honest_null_antipattern','kind','new_method',
       'note','TWO independent spots collapsed three-valued logic into two: cert.standing (unchecked == absent, via INNER JOIN) and cert.kan_engines_all_true (empty == violated, via COALESCE(...,FALSE)). Both fixed. Sound contradiction detection requires refuted (proven false) to stay distinct from unverified (unknown/no data).'),
     jsonb_build_object('id','claims_outrun_data','kind','gap',
       'note','kan corpus/objects/functor_object_map and curry functions/constants are all 0 rows; cert claims encode expectations against populated engines, so an unbuilt engine vacuously refutes. The attestation layer ran ahead of the computation layer.'),
     jsonb_build_object('id','kan_in_kan_only_trivial_holds','kind','observation',
       'note','Under empty data only reflexive/trivial kan structures validate: kan_self is a valid identity endofunctor; strata_tower and prime_members laws hold; the deep self-engines (self_shadow, self_syzygy) are unverified for lack of witnesses, not violated.'),
     jsonb_build_object('id','curry_in_curry_exec_only','kind','observation',
       'note','Currys provenance has an execution cache (curry.inferences populated) but zero declared functions/constants; it can memoize runs but has nothing to validate purity/coverage against (curry_function, curry_constant claims come back unverified).'),
     jsonb_build_object('id','genuine_contradictions_isolated','kind','confirmation',
       'note','After the emptiness guard, surviving refutations are real external data-quality issues: Feigenbaum precision (60-dps aspirational vs ~8e-4 actual), MDL renorm sign (AR model wins), BIC mean=Infinity / median=NaN, negative log-likelihoods stored as codelengths. Correctly caught, no longer buried among vacuous ones.'),
     jsonb_build_object('id','probe_drift_is_a_signal','kind','observation',
       'note','One probe errored on a missing column (total_collisions); cert error-status is itself a useful schema-drift detector, distinct from refuted/unverified.')
   ),
   'measured','2026-05-29'
 ) AS evidence$p$)

ON CONFLICT (statement) DO NOTHING;

-- Attest the new claims (appends cert.certificate + provenance).
DO $$
DECLARE c RECORD;
BEGIN
  FOR c IN SELECT id FROM cert.claim
           WHERE statement LIKE '%kan-engine bridge reports refuted only%'
              OR statement LIKE '%kan-in-kan + curry-in-curry%' LOOP
    PERFORM cert.check(c.id);
  END LOOP;
END $$;
