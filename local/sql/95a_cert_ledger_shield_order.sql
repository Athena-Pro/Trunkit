-- Trunkit local overlay, step 95a: ASSERT the exactness-shield / hash-chain
-- trigger ordering. This file no longer creates the trigger -- src/calx/sql/
-- 94_cert_exactness.sql owns it (installed as `aa_exactness_shield_trg` so
-- alphabetical BEFORE-trigger order puts it ahead of the ledger's
-- `cert_certificate_hash`). This file exists to make a regression LOUD.
--
-- Why an assertion and not a second CREATE TRIGGER: one object, one owner. If
-- this file also created the trigger, two files would silently compete for it
-- and the next person could "fix" either one without effect.
--
-- What it guards: applying the core schema from an UNPATCHED Trunk checkout
-- reinstates `exactness_shield_trg` ('e' > 'c'), which fires AFTER the hash.
-- Every float_heuristic claim whose probe returns TRUE then writes a
-- certificate that can never verify -- and cert.certificate is append-only, so
-- the damage is permanent per row. That is not hypothetical: it happened to
-- certificates 772 and 787 on the canonical ledger (see
-- local/sql/95b_cert_ledger_shield_scar.sql, which attests they are explained
-- by this defect rather than by tampering).
--
-- Apply order matters: the local overlay runs after the core schema, so this
-- assertion sees the final trigger set. Idempotent; raises rather than writes.

DO $$
DECLARE
    v_shield TEXT;
    v_hash   TEXT;
    v_all    TEXT;
BEGIN
    -- BEFORE INSERT row triggers on cert.certificate, in firing order.
    SELECT string_agg(tgname, ' -> ' ORDER BY tgname) INTO v_all
      FROM pg_trigger
     WHERE tgrelid = 'cert.certificate'::regclass
       AND NOT tgisinternal
       AND (tgtype::int & 4) = 4;      -- BEFORE

    SELECT min(tgname) INTO v_shield
      FROM pg_trigger
     WHERE tgrelid = 'cert.certificate'::regclass
       AND NOT tgisinternal
       AND (tgtype::int & 4) = 4
       AND tgname LIKE '%exactness_shield%';

    SELECT tgname INTO v_hash
      FROM pg_trigger
     WHERE tgrelid = 'cert.certificate'::regclass
       AND NOT tgisinternal
       AND (tgtype::int & 4) = 4
       AND tgname = 'cert_certificate_hash';

    IF v_hash IS NULL THEN
        RAISE NOTICE '95a: ledger hash trigger absent -- nothing to order against '
                     '(apply local/sql/95_cert_ledger.sql for the hash chain)';
        RETURN;
    END IF;

    IF v_shield IS NULL THEN
        RAISE EXCEPTION
            '95a: exactness shield trigger missing from cert.certificate. '
            'A float_heuristic claim could record a valid certificate. '
            'Apply src/calx/sql/94_cert_exactness.sql.';
    END IF;

    IF v_shield >= v_hash THEN
        RAISE EXCEPTION
            '95a: exactness shield (%) fires AFTER the ledger hash (%). Order: %. '
            'Every float_heuristic claim returning TRUE will write a permanently '
            'unverifiable certificate. Re-apply the PATCHED '
            'src/calx/sql/94_cert_exactness.sql, which installs the shield as '
            'aa_exactness_shield_trg.', v_shield, v_hash, v_all;
    END IF;

    RAISE NOTICE '95a: trigger order OK -- %', v_all;
END $$;
