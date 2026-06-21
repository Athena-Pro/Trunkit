-- Unified model, step 93: C-finite / P-finite recurrence certificates.
--
-- A holonomic (P-finite) linear recurrence with polynomial coefficients IS a
-- *compact* certificate that regenerates a sequence. Verifying "sequence S
-- satisfies recurrence R" is a cheap, EXACT, in-SQL computation: regenerate the
-- terms from the recurrence and compare. No dependency, pure SQL over NUMERIC.
--
-- This is the exact identity verifier behind the OEIS cosine candidate layer
-- (92): cosine *proposes* a shape; a recurrence certificate *proves* it. It is
-- also the "tiny certificate instead of the whole trace" idea — a handful of
-- coefficients replaces an arbitrarily long term log — realised minimally.
--
-- Recurrence form (order d), 0-indexed, with polynomial coefficients in n:
--     p0(n)·a_n + p1(n)·a_{n-1} + … + pd(n)·a_{n-d} = 0
--   ⇒ a_n = −( p1(n)·a_{n-1} + … + pd(n)·a_{n-d} ) / p0(n)
-- C-finite is the special case where every p_i is a constant polynomial.
--
-- `polys` is a JSONB array of d+1 coefficient arrays, each ascending in n:
--   [[p0…], [p1…], …, [pd…]]   e.g. Fibonacci = [[1],[-1],[-1]], init [1,1]
-- EXACTNESS: generation requires p0(n) ≠ 0 and exact integer division at every
-- step; a non-exact step makes the certificate refuted (no float leakage —
-- ties to the exact-domain stance). Idempotent.

INSERT INTO cert.method (name, claim_kind, checker_kind, description)
VALUES ('comp_sql', 'computational', 'sql', 'in-DB probe returning (ok, evidence)')
ON CONFLICT (name) DO NOTHING;

-- Evaluate an ascending-coefficient polynomial at integer n (exact NUMERIC).
CREATE OR REPLACE FUNCTION cert.poly_eval(c NUMERIC[], n NUMERIC)
RETURNS NUMERIC LANGUAGE sql IMMUTABLE AS $$
    SELECT COALESCE(sum(c[i] * power(n, i - 1)), 0)
    FROM generate_subscripts(c, 1) AS i;
$$;

-- Generate the first p_count terms of a P-finite recurrence, exactly.
-- Raises on a vanishing leading coefficient or a non-exact division.
CREATE OR REPLACE FUNCTION cert.recurrence_generate(
    p_polys JSONB, p_init NUMERIC[], p_count INT
) RETURNS NUMERIC[]
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    d   INT := jsonb_array_length(p_polys) - 1;
    v   NUMERIC[];
    t   INT; nn NUMERIC; j INT;
    p0  NUMERIC; s NUMERIC; num NUMERIC;
    pj  NUMERIC[];
BEGIN
    IF d < 1 THEN RAISE EXCEPTION 'recurrence order must be >= 1'; END IF;
    IF p_init IS NULL OR array_length(p_init, 1) < d THEN
        RAISE EXCEPTION 'need >= % initial terms, got %', d, COALESCE(array_length(p_init,1),0);
    END IF;
    v := p_init[1:d];
    FOR t IN (d + 1) .. p_count LOOP        -- 1-based term index; 0-based n = t-1
        nn := t - 1;
        p0 := cert.poly_eval(ARRAY(SELECT jsonb_array_elements_text(p_polys->0)::numeric), nn);
        IF p0 = 0 THEN
            RAISE EXCEPTION 'leading coefficient vanishes at n=%', nn;
        END IF;
        s := 0;
        FOR j IN 1 .. d LOOP
            pj := ARRAY(SELECT jsonb_array_elements_text(p_polys->j)::numeric);
            s := s + cert.poly_eval(pj, nn) * v[t - j];   -- v[t-j] = term_{nn-j}
        END LOOP;
        num := -s;
        IF num % p0 <> 0 THEN
            RAISE EXCEPTION 'non-exact division at n=% (% / %)', nn, num, p0;
        END IF;
        v := array_append(v, num / p0);
    END LOOP;
    RETURN v[1:p_count];
END
$$;

-- Registry of recurrence certificates. Self-contained: stores the recurrence
-- AND the sequence prefix it is claimed to generate (no external dependency).
CREATE TABLE IF NOT EXISTS cert.recurrence (
    id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seq_id    TEXT NOT NULL,
    kind      TEXT NOT NULL DEFAULT 'p_finite',   -- 'c_finite' | 'p_finite' (descriptive)
    ord       INT  NOT NULL,
    polys     JSONB NOT NULL,                      -- [[p0…],[p1…],…,[pd…]] ascending in n
    init      NUMERIC[] NOT NULL,                  -- a_0 … a_{d-1}
    terms     NUMERIC[] NOT NULL,                  -- claimed sequence prefix
    built_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (seq_id, kind)
);

CREATE OR REPLACE FUNCTION cert.register_recurrence(
    p_seq_id TEXT, p_kind TEXT, p_polys JSONB, p_init NUMERIC[], p_terms NUMERIC[]
) RETURNS cert.recurrence
LANGUAGE plpgsql AS $$
DECLARE v_row cert.recurrence%ROWTYPE;
BEGIN
    INSERT INTO cert.recurrence (seq_id, kind, ord, polys, init, terms)
    VALUES (p_seq_id, p_kind, jsonb_array_length(p_polys) - 1, p_polys, p_init, p_terms)
    ON CONFLICT (seq_id, kind) DO UPDATE
        SET ord = EXCLUDED.ord, polys = EXCLUDED.polys, init = EXCLUDED.init,
            terms = EXCLUDED.terms, built_at = now()
    RETURNING * INTO v_row;
    RETURN v_row;
END
$$;

-- Verify a stored recurrence regenerates its claimed terms, exactly.
-- Three-valued in spirit: ok=true (matches), ok=false (mismatch or non-exact).
CREATE OR REPLACE FUNCTION cert.recurrence_matches(p_rec_id BIGINT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    r   cert.recurrence%ROWTYPE;
    gen NUMERIC[];
    n   INT;
    len INT;
BEGIN
    SELECT * INTO r FROM cert.recurrence WHERE id = p_rec_id;
    IF NOT FOUND THEN
        ok := false; evidence := jsonb_build_object('reason', 'no recurrence', 'id', p_rec_id);
        RETURN NEXT; RETURN;
    END IF;
    len := array_length(r.terms, 1);
    BEGIN
        gen := cert.recurrence_generate(r.polys, r.init, len);
    EXCEPTION WHEN OTHERS THEN
        ok := false;
        evidence := jsonb_build_object('reason', 'generation failed (vanishing/non-exact)',
                                       'detail', SQLERRM);
        RETURN NEXT; RETURN;
    END;
    FOR n IN 1 .. len LOOP
        IF gen[n] IS DISTINCT FROM r.terms[n] THEN
            ok := false;
            evidence := jsonb_build_object('reason', 'term mismatch', 'at', n,
                                           'expected', r.terms[n], 'got', gen[n]);
            RETURN NEXT; RETURN;
        END IF;
    END LOOP;
    ok := true;
    evidence := jsonb_build_object('order', r.ord, 'kind', r.kind,
                                   'verified_terms', len, 'exact', true);
    RETURN NEXT;
END
$$;

-- Build a re-checkable comp_sql claim that a sequence satisfies its recurrence.
-- The probe is self-contained (references the stored recurrence by id), so it
-- re-verifies via cert.check and travels in export bundles unchanged.
CREATE OR REPLACE FUNCTION cert.recurrence_claim(p_rec_id BIGINT)
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE
    r      cert.recurrence%ROWTYPE;
    v_stmt TEXT; v_probe TEXT; v_id BIGINT;
BEGIN
    SELECT * INTO r FROM cert.recurrence WHERE id = p_rec_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'no recurrence %', p_rec_id; END IF;
    v_stmt := format(
        'sequence %s satisfies a %s recurrence (order %s, exact) over %s terms [rec #%s]',
        r.seq_id, r.kind, r.ord, array_length(r.terms, 1), r.id);
    v_probe := format('SELECT ok, evidence FROM cert.recurrence_matches(%s)', r.id);
    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
    VALUES ('recurrence',
            jsonb_build_object('seq_id', r.seq_id, 'recurrence_id', r.id, 'order', r.ord),
            v_stmt, 'computational', 'comp_sql', v_probe)
    ON CONFLICT (statement) DO UPDATE SET probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;
