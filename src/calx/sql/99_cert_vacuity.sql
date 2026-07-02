-- Unified model, step 99: the vacuous-truth meta-probe.
--
-- Bug class, observed twice before this probe existed: a kan engine law-view
-- read TRUE (or FALSE) when its underlying tables were empty --
--   * 36c0d04  chromatic convergence returned FALSE on an empty source
--              (an unpopulated engine manufactured a refutation);
--   * a725a7b  shadow resolves-kernel needed a CASE guard so that "never
--              computed" reads NULL while "computed, zero collisions" reads
--              the legitimate vacuous TRUE.
-- The scott engine (98) then shipped with the discipline built in. This file
-- turns that discipline from a per-engine convention into a re-runnable,
-- auto-discovering attestation: for EVERY kan '%_laws' view, when every kan
-- base table it reads is empty, each of its boolean columns must read NULL
-- (unverified) -- never TRUE (vacuous truth), never FALSE (false refutation).
--
-- What this does NOT police: conditional vacuity inside populated states.
-- Shadow's "computed, zero collisions -> TRUE" fires only when kan.shadow_term
-- has rows; this probe empties the tables entirely, so that branch is never
-- reached. The probe asserts only "an engine that never ran reads unknown."
--
-- Mechanics:
--   * Discovery mirrors 79_cert_kan_engines: every kan view named '%_laws',
--     boolean columns only; counts-only views are surfaced, not failed.
--   * Base tables are resolved recursively through views (a laws view may
--     read kan.<x>_summary which reads the real table), restricted to the
--     kan schema: engine state lives in kan; upstream calx corpora are out
--     of scope for the emptiness experiment.
--   * The emptying happens inside a savepoint block that ALWAYS rolls back
--     (a deliberate RAISE with custom SQLSTATE 'TRVAC', caught immediately).
--     No caller ever observes emptied tables, whether or not it wraps the
--     probe in its own transaction. DELETEs run in up to 3 passes so FK
--     ordering between engine tables cannot wedge the probe; a table that
--     still cannot be emptied marks the view 'undeletable' (untested -> the
--     overall verdict degrades to unverified, never to refuted).
--   * Columns are judged individually: ANDing them first (79's read pattern)
--     would let one legitimate NULL mask another column's vacuous TRUE.
--
-- Three-valued verdict: FALSE only on a genuine vacuous TRUE / FALSE-on-empty;
-- NULL when nothing was testable; TRUE when every tested law-view reads
-- honestly NULL on empty state. Idempotent.

CREATE OR REPLACE FUNCTION cert.kan_laws_vacuity()
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql AS $$
DECLARE
    v           RECORD;
    col         RECORD;
    v_cols      TEXT[];
    v_tables    TEXT[];
    v_remaining TEXT[];
    v_next      TEXT[];
    v_tbl       TEXT;
    v_state     BOOLEAN;
    v_colstates JSONB;
    v_status    TEXT;
    v_views     JSONB := '{}'::jsonb;
    v_tested    INTEGER := 0;   -- views actually driven to empty state
    v_violated  INTEGER := 0;   -- views with a vacuous TRUE / FALSE-on-empty
    v_untested  INTEGER := 0;   -- views we could not test (undeletable)
    v_pass      INTEGER;
BEGIN
    FOR v IN
        SELECT table_name
          FROM information_schema.views
         WHERE table_schema = 'kan' AND table_name ~ '_laws$'
         ORDER BY table_name
    LOOP
        -- boolean law columns, judged one by one
        SELECT array_agg(column_name ORDER BY ordinal_position)
          INTO v_cols
          FROM information_schema.columns
         WHERE table_schema = 'kan'
           AND table_name = v.table_name
           AND data_type = 'boolean';

        IF v_cols IS NULL THEN
            v_views := v_views || jsonb_build_object(v.table_name, 'no_boolean_laws');
            CONTINUE;
        END IF;

        -- kan base TABLES, resolved recursively through intermediate views
        WITH RECURSIVE deps(relname, relkind) AS (
            SELECT c.relname, c.relkind
              FROM information_schema.view_table_usage vtu
              JOIN pg_class c ON c.relname = vtu.table_name
              JOIN pg_namespace n ON n.oid = c.relnamespace
                                 AND n.nspname = vtu.table_schema
             WHERE vtu.view_schema = 'kan'
               AND vtu.view_name = v.table_name
               AND vtu.table_schema = 'kan'
            UNION
            SELECT c.relname, c.relkind
              FROM deps d
              JOIN information_schema.view_table_usage vtu
                ON vtu.view_schema = 'kan'
               AND vtu.view_name = d.relname
               AND vtu.table_schema = 'kan'
              JOIN pg_class c ON c.relname = vtu.table_name
              JOIN pg_namespace n ON n.oid = c.relnamespace
                                 AND n.nspname = vtu.table_schema
             WHERE d.relkind = 'v'
        )
        SELECT array_agg(DISTINCT relname) INTO v_tables
          FROM deps WHERE relkind = 'r';

        IF v_tables IS NULL THEN
            v_views := v_views || jsonb_build_object(v.table_name, 'no_base_tables');
            CONTINUE;
        END IF;

        -- empty the base tables, read every law column, ALWAYS roll back
        v_colstates := NULL;
        v_remaining := v_tables;
        BEGIN
            FOR v_pass IN 1..3 LOOP
                EXIT WHEN v_remaining = '{}';
                v_next := '{}';
                FOREACH v_tbl IN ARRAY v_remaining LOOP
                    BEGIN
                        EXECUTE format('DELETE FROM kan.%I', v_tbl);
                    EXCEPTION WHEN foreign_key_violation THEN
                        v_next := v_next || v_tbl;   -- retry next pass
                    END;
                END LOOP;
                v_remaining := v_next;
            END LOOP;

            IF v_remaining = '{}' THEN
                EXECUTE format(
                    'SELECT jsonb_build_object(%s) FROM kan.%I',
                    (SELECT string_agg(format('%L, %I', c, c), ', ')
                       FROM unnest(v_cols) AS c),
                    v.table_name
                ) INTO v_colstates;
                -- zero-row view on empty state == every law NULL: honest
                v_colstates := COALESCE(v_colstates, '{}'::jsonb);
            END IF;

            RAISE EXCEPTION 'vacuity probe rollback' USING ERRCODE = 'TRVAC';
        EXCEPTION WHEN SQLSTATE 'TRVAC' THEN
            NULL;   -- deletes rolled back; v_colstates / v_remaining survive
        END;

        IF v_remaining <> '{}' THEN
            v_status := 'undeletable';
            v_untested := v_untested + 1;
        ELSIF EXISTS (SELECT 1 FROM jsonb_each(v_colstates) e
                       WHERE e.value = 'true'::jsonb) THEN
            v_status := 'vacuous_true';
            v_violated := v_violated + 1;
        ELSIF EXISTS (SELECT 1 FROM jsonb_each(v_colstates) e
                       WHERE e.value = 'false'::jsonb) THEN
            v_status := 'false_on_empty';
            v_violated := v_violated + 1;
        ELSE
            v_status := 'null_on_empty';
        END IF;
        IF v_status IN ('null_on_empty', 'vacuous_true', 'false_on_empty') THEN
            v_tested := v_tested + 1;
        END IF;

        v_views := v_views || jsonb_build_object(
            v.table_name,
            jsonb_build_object('status', v_status,
                               'base_tables', to_jsonb(v_tables),
                               'columns_on_empty', v_colstates)
        );
    END LOOP;

    RETURN QUERY SELECT
        CASE WHEN v_violated > 0                  THEN FALSE
             WHEN v_tested = 0 OR v_untested > 0  THEN NULL
             ELSE TRUE END,
        jsonb_build_object('views_tested',   v_tested,
                           'violations',     v_violated,
                           'untested',       v_untested,
                           'views',          v_views);
END
$$;

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'kan_bridge',
       '{"bridge":"vacuous-truth meta-probe","tier":"comp_sql",
         "auto_discovers":"kan.%_laws boolean columns on emptied kan base tables"}'::jsonb,
       'no kan engine law-view reads true or false when its kan base tables are empty: an engine that never ran attests unverified, never a vacuous truth or a manufactured refutation',
       'computational', 'comp_sql',
       'SELECT ok, evidence FROM cert.kan_laws_vacuity()'
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'no kan engine law-view reads true or false when its kan base tables are empty: an engine that never ran attests unverified, never a vacuous truth or a manufactured refutation'
);
