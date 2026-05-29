-- Unified model, step 79: the kan-engine -> cert bridge (the missing one).
--
-- Audit finding: every mathematical step (42..78) has TWO encodings of its
-- laws -- the LIVE in-DB engine result (kan.<x>_laws, populated by
-- tools/build_<x>.py against the live corpus) and an EXTERNAL hash-pinned
-- proof (proofs/<x>.py, formal tier). The formal claim trusts the external
-- artifact; nothing attested the live engine, and nothing cross-checked the
-- two. If build_<x>.py and proofs/<x>.py silently diverged, cert would not
-- notice.
--
-- This is the one unbuilt pillar transition. Every other bridge exists:
--   calx/curry/kan -> kan   kan.sync_category
--   curry <-> calx          kan.populate_curry_calx_functor
--   any pillar -> cert       cert.check probe_sql + curry.inferences
--   formal artifact -> cert  cert_formal.py + cert.artifact (hash-pinned)
--   counts -> attestation    kan_in_kan.py Phase 6
-- ...but kan-engine-result -> cert was missing. This file builds it: a
-- comp_sql claim whose trust root is the DB itself, corroborating each
-- external formal proof with the live computation (defense in depth) and
-- making engine drift a probe-detectable staleness event.
--
-- Auto-discovering: it ANDs every boolean column of every kan '%_laws'
-- view, so any future engine is covered the moment its laws view exists --
-- no per-engine wiring. Idempotent.

CREATE OR REPLACE FUNCTION cert.kan_engines_all_true()
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql AS $$
DECLARE
    v          RECORD;
    v_andcols  TEXT;
    v_rowok    BOOLEAN;
    v_engines  JSONB := '{}'::jsonb;
    v_n        INTEGER := 0;   -- engines with law data (non-empty)
    v_empty    INTEGER := 0;   -- engines whose laws view has no witnesses
    v_violated INTEGER := 0;   -- engines with a genuinely-false law
BEGIN
    FOR v IN
        SELECT table_name
          FROM information_schema.views
         WHERE table_schema = 'kan' AND table_name ~ '_laws$'
         ORDER BY table_name
    LOOP
        SELECT string_agg(quote_ident(column_name), ' AND ')
          INTO v_andcols
          FROM information_schema.columns
         WHERE table_schema = 'kan'
           AND table_name = v.table_name
           AND data_type = 'boolean';

        IF v_andcols IS NULL THEN
            -- counts-only view: surfaced, not failed (not a law assertion)
            v_engines := v_engines
                || jsonb_build_object(v.table_name, 'no_boolean_laws');
            CONTINUE;
        END IF;

        EXECUTE format(
            'SELECT bool_and(%s) FROM kan.%I', v_andcols, v.table_name
        ) INTO v_rowok;
        -- NULL bool_and == no witnesses / all-null row == laws UNKNOWN (empty
        -- engine), NOT violated. Conflating the two manufactures false
        -- contradictions and masks genuine ones; keep the three-valued signal.
        IF v_rowok IS NULL THEN
            v_engines := v_engines
                || jsonb_build_object(v.table_name, 'empty');
            v_empty := v_empty + 1;
        ELSE
            v_engines := v_engines
                || jsonb_build_object(v.table_name, v_rowok);
            v_n := v_n + 1;
            IF NOT v_rowok THEN
                v_violated := v_violated + 1;
            END IF;
        END IF;
    END LOOP;

    -- refuted only on a genuine violation; unverified when populated engines
    -- hold but some are empty (or nothing populated); valid when every
    -- populated engine holds and none are empty.
    RETURN QUERY SELECT
        CASE WHEN v_violated > 0         THEN FALSE
             WHEN v_empty > 0 OR v_n = 0 THEN NULL
             ELSE TRUE END,
        jsonb_build_object('engines_checked', v_n,
                           'engines_empty',  v_empty,
                           'violations',     v_violated,
                           'all_true', (v_violated = 0 AND v_empty = 0 AND v_n > 0),
                           'engines', v_engines);
END
$$;

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'kan_bridge',
       '{"bridge":"kan-engine-result -> cert","tier":"comp_sql",
         "auto_discovers":"kan.%_laws boolean columns"}'::jsonb,
       'every kan engine law-view is all-true in the live DB: the in-DB computation corroborates each external formal proof (the kan-engine -> cert bridge)',
       'computational', 'comp_sql',
       'SELECT ok, evidence FROM cert.kan_engines_all_true()'
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'every kan engine law-view is all-true in the live DB: the in-DB computation corroborates each external formal proof (the kan-engine -> cert bridge)'
);
