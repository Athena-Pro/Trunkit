-- Unified model, step 0: stand up the three sibling schemas and re-home the
-- existing calx objects (12 tables + ~20 routines) out of `public` into `calx`.
--
-- Data-preserving: ALTER ... SET SCHEMA moves objects in place, no copy, no
-- data loss. Idempotent: guarded so a second run is a no-op.

CREATE SCHEMA IF NOT EXISTS calx;
CREATE SCHEMA IF NOT EXISTS curry;
CREATE SCHEMA IF NOT EXISTS kan;

-- Unqualified names in the calx SQL (sql/01..07) must keep resolving once the
-- objects live in `calx`. Pin the role's search_path.
ALTER ROLE trunk SET search_path = calx, curry, kan, public;

-- Move every base table currently in public into calx.
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT tablename
          FROM pg_tables
         WHERE schemaname = 'public'
    LOOP
        EXECUTE format('ALTER TABLE public.%I SET SCHEMA calx', r.tablename);
    END LOOP;
END
$$;

-- Move every function / procedure currently in public into calx.
-- pg_get_function_identity_arguments gives an unambiguous signature.
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT p.oid,
               p.proname,
               pg_get_function_identity_arguments(p.oid) AS args,
               p.prokind
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public'
    LOOP
        IF r.prokind = 'p' THEN
            EXECUTE format('ALTER PROCEDURE public.%I(%s) SET SCHEMA calx',
                           r.proname, r.args);
        ELSE
            EXECUTE format('ALTER FUNCTION public.%I(%s) SET SCHEMA calx',
                           r.proname, r.args);
        END IF;
    END LOOP;
END
$$;
