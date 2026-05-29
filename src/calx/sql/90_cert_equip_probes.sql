-- Unified model, step 90: equip dangling claims for verification.
--
-- The validation pass (2026-05-29) found ~24 claims with probe_sql IS NULL:
-- asserted but with no in-DB checker, so cert.standing can only ever call them
-- 'unverified'. "Unverified" should mean "checkable but data insufficient",
-- not "no way to check". This file qualifies the checkable ones by attaching
-- probes, using the same three-valued honesty as 79 (empty -> unverified,
-- violation -> refuted, holds -> valid). Idempotent.

-- ---- reusable verifiers -----------------------------------------------------

-- Single kan law-view checker (the per-engine analogue of kan_engines_all_true).
-- bool_and already separates empty (NULL) from violated (FALSE); we preserve it.
CREATE OR REPLACE FUNCTION cert.law_view_holds(p_view TEXT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql AS $$
DECLARE
    v_cols      TEXT;
    v_rowok     BOOLEAN;
    v_witnesses BIGINT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.views
                    WHERE table_schema='kan' AND table_name=p_view) THEN
        RETURN QUERY SELECT NULL::boolean,
            jsonb_build_object('error','no such kan law view','view',p_view);
        RETURN;
    END IF;
    SELECT string_agg(quote_ident(column_name), ' AND ') INTO v_cols
      FROM information_schema.columns
     WHERE table_schema='kan' AND table_name=p_view AND data_type='boolean';
    IF v_cols IS NULL THEN
        RETURN QUERY SELECT NULL::boolean,
            jsonb_build_object('note','no boolean law columns','view','kan.'||p_view);
        RETURN;
    END IF;
    EXECUTE format('SELECT bool_and(%s), count(*) FROM kan.%I', v_cols, p_view)
       INTO v_rowok, v_witnesses;
    RETURN QUERY SELECT v_rowok,   -- NULL (empty/all-null) -> unverified
        jsonb_build_object('view','kan.'||p_view, 'witnesses', v_witnesses,
                           'laws_hold', v_rowok);
END $$;

-- Perfect-number verifier (a concrete arithmetic checker; aliquot sum = n).
CREATE OR REPLACE FUNCTION cert.is_perfect(n BIGINT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE sql AS $$
    SELECT (s = n) AS ok,
           jsonb_build_object('n', n, 'aliquot_sum', s, 'perfect', (s = n))
      FROM (SELECT COALESCE(sum(d),0) AS s
              FROM generate_series(1, n-1) d WHERE n % d = 0) t;
$$;

-- ---- equip pass -------------------------------------------------------------

-- (1) every NULL-probe claim whose subject_kind has a matching kan law-view
UPDATE cert.claim cl
   SET probe_sql = format('SELECT ok, evidence FROM cert.law_view_holds(%L)',
                          cl.subject_kind || '_laws')
 WHERE cl.probe_sql IS NULL
   AND EXISTS (SELECT 1 FROM information_schema.views v
                WHERE v.table_schema='kan'
                  AND v.table_name = cl.subject_kind || '_laws');

-- (2) prime_members_functor -> prime_members_laws (name differs from subject_kind)
UPDATE cert.claim
   SET probe_sql = 'SELECT ok, evidence FROM cert.law_view_holds(''prime_members_laws'')'
 WHERE probe_sql IS NULL AND subject_kind = 'prime_members_functor';

-- (3) perfect-number facts -> arithmetic verifier (directly checkable now)
UPDATE cert.claim
   SET probe_sql = format('SELECT ok, evidence FROM cert.is_perfect(%s)',
                          (subject_ref->>'n')::bigint)
 WHERE probe_sql IS NULL AND subject_kind = 'number_fact'
   AND subject_ref->>'property' = 'perfect'
   AND subject_ref ? 'n';

-- Re-attest everything that just gained a probe.
DO $$
DECLARE c RECORD; n int := 0;
BEGIN
  FOR c IN SELECT s.claim_id FROM cert.standing s
           WHERE s.status='unverified' LOOP
    PERFORM cert.check(c.claim_id);
    n := n + 1;
  END LOOP;
  RAISE NOTICE 're-checked % claims after equip', n;
END $$;
