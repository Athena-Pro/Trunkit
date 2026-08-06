-- Unified model, step 101: Chinese Remainder Theorem certificates.
--
-- A system of pairwise-coprime congruences x ≡ r_i (mod m_i) IS a compact
-- certificate for a claimed solution x: reconstruct the unique solution in
-- [0, M) — M = Π m_i — via CRT and compare. Cheap, EXACT, in-SQL, reusing the
-- ext_gcd / mod_inverse / crt machinery already built for the sieve in 04.
--
-- Three-valued in spirit: ok=true (x matches the reconstructed solution),
-- ok=false (mismatch OR the moduli are not pairwise coprime — mod_inverse has
-- no inverse to give, so the system has no CRT solution to certify). Never
-- raises out of the checker; a failed reconstruction is a refutation, not an
-- error, same stance as a vanishing leading coefficient in 93. Idempotent.

INSERT INTO cert.method (name, claim_kind, checker_kind, description)
VALUES ('comp_sql', 'computational', 'sql', 'in-DB probe returning (ok, evidence)')
ON CONFLICT (name) DO NOTHING;

-- Registry of congruence certificates. Self-contained: stores the congruence
-- system AND the claimed solution (no external dependency).
CREATE TABLE IF NOT EXISTS cert.congruence (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_id  TEXT NOT NULL UNIQUE,
    remainders  BIGINT[] NOT NULL,
    moduli      BIGINT[] NOT NULL,
    x           BIGINT NOT NULL,               -- claimed solution
    built_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION cert.register_congruence(
    p_subject_id TEXT, p_remainders BIGINT[], p_moduli BIGINT[], p_x BIGINT
) RETURNS cert.congruence
LANGUAGE plpgsql AS $$
DECLARE v_row cert.congruence%ROWTYPE;
BEGIN
    IF array_length(p_remainders, 1) IS DISTINCT FROM array_length(p_moduli, 1) THEN
        RAISE EXCEPTION 'remainders and moduli arrays must be the same length';
    END IF;
    INSERT INTO cert.congruence (subject_id, remainders, moduli, x)
    VALUES (p_subject_id, p_remainders, p_moduli, p_x)
    ON CONFLICT (subject_id) DO UPDATE
        SET remainders = EXCLUDED.remainders, moduli = EXCLUDED.moduli,
            x = EXCLUDED.x, built_at = now()
    RETURNING * INTO v_row;
    RETURN v_row;
END
$$;

-- Verify a stored congruence system's claimed solution via CRT reconstruction.
CREATE OR REPLACE FUNCTION cert.congruence_matches(p_id BIGINT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    r        cert.congruence%ROWTYPE;
    solution BIGINT;
    modulus  BIGINT;
BEGIN
    SELECT * INTO r FROM cert.congruence WHERE id = p_id;
    IF NOT FOUND THEN
        ok := false; evidence := jsonb_build_object('reason', 'no congruence', 'id', p_id);
        RETURN NEXT; RETURN;
    END IF;
    BEGIN
        solution := crt(r.remainders, r.moduli);
    EXCEPTION WHEN OTHERS THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', 'no CRT solution (moduli not pairwise coprime)', 'detail', SQLERRM);
        RETURN NEXT; RETURN;
    END;
    SELECT COALESCE(EXP(SUM(LN(m)))::BIGINT, 1) INTO modulus
    FROM unnest(r.moduli) m;
    IF solution != ((r.x % modulus) + modulus) % modulus THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', 'solution mismatch', 'expected', solution,
            'got', ((r.x % modulus) + modulus) % modulus);
        RETURN NEXT; RETURN;
    END IF;
    ok := true;
    evidence := jsonb_build_object(
        'congruences', array_length(r.moduli, 1), 'modulus', modulus, 'exact', true);
    RETURN NEXT;
END
$$;

-- Build a re-checkable comp_sql claim that x is the CRT solution of the
-- congruence system. The probe is self-contained (references the stored
-- congruence by id), so it re-verifies via cert.check and travels in export
-- bundles unchanged.
CREATE OR REPLACE FUNCTION cert.congruence_claim(p_id BIGINT)
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE
    r      cert.congruence%ROWTYPE;
    v_stmt TEXT; v_probe TEXT; v_id BIGINT;
BEGIN
    SELECT * INTO r FROM cert.congruence WHERE id = p_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'no congruence %', p_id; END IF;
    v_stmt := format(
        'x=%s is the CRT solution of %s pairwise-coprime congruences [congruence #%s]',
        r.x, array_length(r.moduli, 1), r.id);
    v_probe := format('SELECT ok, evidence FROM cert.congruence_matches(%s)', r.id);
    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
    VALUES ('congruence',
            jsonb_build_object('subject_id', r.subject_id, 'congruence_id', r.id),
            v_stmt, 'computational', 'comp_sql', v_probe)
    ON CONFLICT (statement) DO UPDATE SET probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;
