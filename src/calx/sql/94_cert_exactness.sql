-- Unified model, step 94: exact-domain type shields (#3).
--
-- Formalises the float-quarantine practised ad hoc elsewhere (cosine is a
-- heuristic → only ever a *candidate*; exact terms decide). Every claim carries
-- a `domain` tag; a ledger-level shield guarantees a `float_heuristic` claim can
-- NEVER record a `valid` certificate — it is downgraded to `unverified` at
-- insert time. "No irrational / floating-point leakage into a valid verdict."
--
-- Domains: exact_int | rational | algebraic | interval | float_heuristic | unspecified
-- (recurrence certs (93) are exact_int; the OEIS cosine *candidate* is
-- float_heuristic, while its exact-prefix confirm claim is exact_int.)
-- Additive + idempotent: ALTER … IF NOT EXISTS, CREATE OR REPLACE, append-only-safe
-- (the BEFORE INSERT trigger edits the NEW row, never history).

ALTER TABLE cert.claim ADD COLUMN IF NOT EXISTS domain TEXT NOT NULL DEFAULT 'unspecified';

COMMENT ON COLUMN cert.claim.domain IS
    'Trust-path numeric domain: exact_int|rational|algebraic|interval|float_heuristic|unspecified. '
    'float_heuristic claims are shielded from ever recording a valid certificate.';

CREATE OR REPLACE FUNCTION cert.set_domain(p_claim_id BIGINT, p_domain TEXT)
RETURNS cert.claim LANGUAGE plpgsql AS $$
DECLARE v_row cert.claim%ROWTYPE;
BEGIN
    IF p_domain NOT IN ('exact_int','rational','algebraic','interval','float_heuristic','unspecified') THEN
        RAISE EXCEPTION 'unknown domain %', p_domain;
    END IF;
    UPDATE cert.claim SET domain = p_domain WHERE id = p_claim_id RETURNING * INTO v_row;
    RETURN v_row;
END $$;

-- The shield: downgrade valid→unverified for float_heuristic claims at record time.
CREATE OR REPLACE FUNCTION cert.exactness_shield() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_domain TEXT;
BEGIN
    SELECT domain INTO v_domain FROM cert.claim WHERE id = NEW.claim_id;
    IF v_domain = 'float_heuristic' AND NEW.status = 'valid' THEN
        NEW.status := 'unverified';
        NEW.evidence := COALESCE(NEW.evidence, '{}'::jsonb)
            || jsonb_build_object(
                 'shield', 'float_heuristic downgraded: a heuristic cannot yield a valid verdict',
                 'original_status', 'valid');
    END IF;
    RETURN NEW;
END $$;

-- TRIGGER NAME IS LOAD-BEARING -- do not rename without reading this.
--
-- This trigger MUTATES NEW.status and NEW.evidence. The ledger overlay
-- (local/sql/95_cert_ledger.sql) installs `cert_certificate_hash`, also BEFORE
-- INSERT, which computes NEW.row_hash over exactly those two columns.
-- PostgreSQL fires BEFORE triggers in ALPHABETICAL NAME ORDER, so the shield
-- must sort before 'cert_certificate_hash' or the hash commits to content that
-- never reaches disk -- and because cert.certificate is append-only, the
-- resulting row can never be repaired, only accounted for.
--
-- That is not hypothetical: under the old name 'exactness_shield_trg'
-- ('e' > 'c') every float_heuristic claim whose probe returned TRUE wrote a
-- certificate that failed cert.verify_chain() forever after, reported as
-- "content hash mismatch ... (row altered)" -- an accusation of tampering
-- where there was none. Observed on the canonical ledger 2026-07-25 at
-- certificates 772 and 787; see local/sql/95b_cert_ledger_shield_scar.sql,
-- which attests that those two rows are explained by this defect rather than
-- by alteration, and local/tests/test_cert_ledger_shield_order.py, which
-- fails if the ordering ever regresses.
--
-- The 'aa_' prefix is deliberate and collation-robust: it sorts first under
-- C and under any ICU/glibc locale, whereas a digit prefix does not portably.
DROP TRIGGER IF EXISTS exactness_shield_trg    ON cert.certificate;
DROP TRIGGER IF EXISTS aa_exactness_shield_trg ON cert.certificate;
CREATE TRIGGER aa_exactness_shield_trg
    BEFORE INSERT ON cert.certificate
    FOR EACH ROW EXECUTE FUNCTION cert.exactness_shield();

COMMENT ON FUNCTION cert.exactness_shield() IS
    'Exact-domain shield: downgrades a float_heuristic claim''s valid verdict to '
    'unverified. Mutates NEW.status and NEW.evidence, so its trigger MUST fire '
    'before the ledger''s cert_certificate_hash -- installed as '
    'aa_exactness_shield_trg so alphabetical trigger order puts it first.';

-- Convenience view: claims with their domain and latest shielded status.
CREATE OR REPLACE VIEW cert.exact_standing AS
SELECT s.claim_id, c.domain, s.method, s.status, s.statement
  FROM cert.standing s JOIN cert.claim c ON c.id = s.claim_id;
