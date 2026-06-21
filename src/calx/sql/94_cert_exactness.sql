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

DROP TRIGGER IF EXISTS exactness_shield_trg ON cert.certificate;
CREATE TRIGGER exactness_shield_trg
    BEFORE INSERT ON cert.certificate
    FOR EACH ROW EXECUTE FUNCTION cert.exactness_shield();

-- Convenience view: claims with their domain and latest shielded status.
CREATE OR REPLACE VIEW cert.exact_standing AS
SELECT s.claim_id, c.domain, s.method, s.status, s.statement
  FROM cert.standing s JOIN cert.claim c ON c.id = s.claim_id;
