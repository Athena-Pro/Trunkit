-- calx routines predate the move out of public and contain unqualified
-- references. Bind those references to their owning schemas even when a caller
-- invokes `calx.aliquot_step(...)` from a fresh external connection. This is
-- database-local and needs only routine ownership, not cluster role changes.
DO $$
DECLARE
    routine RECORD;
BEGIN
    FOR routine IN
        SELECT p.oid, p.prokind, p.proname,
               pg_get_function_identity_arguments(p.oid) AS arguments
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'calx' AND p.prokind IN ('f', 'p')
    LOOP
        EXECUTE format('ALTER %s calx.%I(%s) SET search_path = pg_catalog, calx, curry, kan, public',
            CASE WHEN routine.prokind = 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END,
            routine.proname, routine.arguments);
    END LOOP;
END;
$$;
