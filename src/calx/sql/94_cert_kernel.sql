-- Unified model, step 94: the cert_kernel tier — untrusted certificates.
--
-- The four original tiers (comp_sql, struct_kan, formal_external, empirical)
-- all *re-run the producer*: comp_sql/struct_kan replay the producer's SQL,
-- formal_external re-runs the producer's script (and only pins its hash).
-- That defends against drift, but NOT against a producer whose probe begs the
-- question — the checker IS the producer.
--
-- cert_kernel closes that gap. It follows the classic untrusted-certificate
-- discipline (Necula & Lee PCC; Li/Passmore/Paulson, arXiv:1506.08238;
-- rational ReLU certificates, arXiv:2512.24339): SEPARATE SOLVING FROM
-- VERIFYING. The producer submits a proof object (cert.proof_obligation); a
-- small, independent kernel (cert.kernel_verify) checks the object. The kernel
-- is provably simpler than the producer:
--   * factorization : producer FACTORS n (sieve/trial division);
--                      kernel MULTIPLIES p^e back and SUMS sigma.  O(#factors).
--   * crt           : producer LIFTS via extended-gcd;
--                      kernel checks x mod m == r and pairwise-coprime moduli.
--
-- Three-valued honesty is preserved: a malformed/absent/unknown-schema witness
-- is `unverified` (ok = NULL), never a manufactured `refuted`.
--
-- Idempotent: CREATE ... IF NOT EXISTS / OR REPLACE; seeds guarded.

-- ---------------------------------------------------------------------------
-- 0. tiny trusted arithmetic primitives (the whole trusted base of the tier)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION cert.kernel_is_prime(p NUMERIC)
RETURNS BOOLEAN LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE i NUMERIC := 3;
BEGIN
    IF p IS NULL OR p <> trunc(p) OR p < 2 THEN RETURN FALSE; END IF;
    IF p = 2 THEN RETURN TRUE; END IF;
    IF p % 2 = 0 THEN RETURN FALSE; END IF;
    WHILE i * i <= p LOOP
        IF p % i = 0 THEN RETURN FALSE; END IF;
        i := i + 2;
    END LOOP;
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION cert.kernel_gcd(a NUMERIC, b NUMERIC)
RETURNS NUMERIC LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE t NUMERIC;
BEGIN
    a := abs(a); b := abs(b);
    WHILE b <> 0 LOOP
        t := b; b := a % b; a := t;
    END LOOP;
    RETURN a;
END;
$$;

-- ---------------------------------------------------------------------------
-- 1. kernel checkers — each takes a proof object, returns (ok, evidence).
--    ok = NULL means "not checkable" (unverified), never a fake refutation.
-- ---------------------------------------------------------------------------

-- factorization certificate:
--   {"schema":"factorization","n":28,"factors":[[2,2],[7,1]],
--    "asserts":{"perfect":true,"sigma":56}}   -- asserts optional
CREATE OR REPLACE FUNCTION cert.kernel_factorization(p_witness JSONB)
RETURNS TABLE (ok BOOLEAN, evidence JSONB) LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    v_n     NUMERIC;
    v_prod  NUMERIC := 1;
    v_sigma NUMERIC := 1;
    fac     JSONB;
    p       NUMERIC;
    e       NUMERIC;
    v_all_prime BOOLEAN := TRUE;
    v_nfac  INT := 0;
    v_ok    BOOLEAN;
    v_perfect BOOLEAN;
BEGIN
    IF p_witness IS NULL
       OR jsonb_typeof(p_witness->'factors') <> 'array'
       OR jsonb_array_length(p_witness->'factors') = 0
       OR (p_witness->>'n') IS NULL THEN
        RETURN QUERY SELECT NULL::BOOLEAN,
            jsonb_build_object('error', 'missing n or empty/!array factors');
        RETURN;
    END IF;

    v_n := (p_witness->>'n')::NUMERIC;
    FOR fac IN SELECT * FROM jsonb_array_elements(p_witness->'factors') LOOP
        p := (fac->>0)::NUMERIC;
        e := (fac->>1)::NUMERIC;
        IF p < 2 OR e < 1 OR NOT cert.kernel_is_prime(p) THEN
            v_all_prime := FALSE;
        END IF;
        v_prod  := v_prod  * power(p, e);
        v_sigma := v_sigma * ((power(p, e + 1) - 1) / (p - 1));   -- divisor sum formula
        v_nfac  := v_nfac + 1;
    END LOOP;

    v_ok := (v_prod = v_n) AND v_all_prime;

    IF p_witness->'asserts' ? 'sigma' THEN
        v_ok := v_ok AND (v_sigma = (p_witness#>>'{asserts,sigma}')::NUMERIC);
    END IF;
    IF p_witness->'asserts' ? 'perfect' THEN
        v_perfect := (v_sigma - v_n = v_n);
        v_ok := v_ok AND (v_perfect = (p_witness#>>'{asserts,perfect}')::BOOLEAN);
    END IF;

    RETURN QUERY SELECT v_ok, jsonb_build_object(
        'kernel', 'factorization',
        'n', v_n,
        'recomputed_product', v_prod,
        'recomputed_sigma', v_sigma,
        'aliquot_sum', v_sigma - v_n,
        'all_bases_prime', v_all_prime,
        'num_factors', v_nfac
    );
EXCEPTION WHEN OTHERS THEN
    RETURN QUERY SELECT NULL::BOOLEAN, jsonb_build_object('error', SQLERRM);
END;
$$;

-- unit-fraction (Egyptian fraction) certificate:
--   {"schema":"unit_fraction","target":1,"denominators":[3,5,7,9,11,15,35,45,231],
--    "constraints":{"distinct":true,"odd":true}}
-- Motivated by Elsholtz, "Egyptian Fractions with odd denominators" (arXiv:1606.02117):
-- FINDING a distinct-odd decomposition of 1 is hard (the paper bounds how many exist);
-- the kernel only SUMS the unit fractions exactly and checks the side constraints —
-- independent of, and far cheaper than, the search.
CREATE OR REPLACE FUNCTION cert.kernel_unit_fraction(p_witness JSONB)
RETURNS TABLE (ok BOOLEAN, evidence JSONB) LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    arr JSONB; d NUMERIC; tgt NUMERIC;
    num NUMERIC := 0; den NUMERIC := 1; g NUMERIC;   -- running exact rational num/den
    n INT; i INT; j INT;
    v_distinct BOOLEAN := TRUE; v_all_odd BOOLEAN := TRUE; v_all_pos BOOLEAN := TRUE;
    req_distinct BOOLEAN; req_odd BOOLEAN;
BEGIN
    arr := p_witness->'denominators';
    IF arr IS NULL OR jsonb_typeof(arr) <> 'array' OR jsonb_array_length(arr) = 0
       OR (p_witness->>'target') IS NULL THEN
        RETURN QUERY SELECT NULL::BOOLEAN,
            jsonb_build_object('error', 'missing target or empty/!array denominators');
        RETURN;
    END IF;

    tgt := (p_witness->>'target')::NUMERIC;
    FOR d IN SELECT (value)::text::NUMERIC FROM jsonb_array_elements(arr) LOOP
        IF d = 0 THEN v_all_pos := FALSE; CONTINUE; END IF;
        IF d <= 0 THEN v_all_pos := FALSE; END IF;
        IF d % 2 = 0 THEN v_all_odd := FALSE; END IF;
        num := num * d + den;        -- num/den + 1/d = (num*d + den)/(den*d)
        den := den * d;
        g := cert.kernel_gcd(num, den);
        IF g > 1 THEN num := num / g; den := den / g; END IF;
    END LOOP;

    n := jsonb_array_length(arr);
    FOR i IN 0 .. n - 1 LOOP
        FOR j IN i + 1 .. n - 1 LOOP
            IF (arr->>i)::NUMERIC = (arr->>j)::NUMERIC THEN v_distinct := FALSE; END IF;
        END LOOP;
    END LOOP;

    req_distinct := COALESCE((p_witness#>>'{constraints,distinct}')::BOOLEAN, FALSE);
    req_odd      := COALESCE((p_witness#>>'{constraints,odd}')::BOOLEAN, FALSE);

    RETURN QUERY SELECT
        (num = tgt * den AND v_all_pos
         AND (NOT req_distinct OR v_distinct)
         AND (NOT req_odd OR v_all_odd)),
        jsonb_build_object(
            'kernel', 'unit_fraction',
            'target', tgt,
            'sum_num', num, 'sum_den', den,
            'sum_equals_target', (num = tgt * den),
            'all_distinct', v_distinct, 'all_odd', v_all_odd, 'all_positive', v_all_pos,
            'num_terms', n);
EXCEPTION WHEN OTHERS THEN
    RETURN QUERY SELECT NULL::BOOLEAN, jsonb_build_object('error', SQLERRM);
END;
$$;

-- matrix-word certificate (matrix-semigroup membership; cf. arXiv:2604.15386):
--   {"schema":"matrix_word","generators":{"a":[[1,1],[0,1]],"b":[[1,0],[1,1]]},
--    "word":["a","b","a"],"target":[[2,3],[1,2]]}
-- FINDING a word that reaches the target is hard (undecidable in general); the
-- kernel just multiplies the chain and compares. Integer matrices only.
CREATE OR REPLACE FUNCTION cert.kernel_matmul(a JSONB, b JSONB)
RETURNS JSONB LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    ra INT := jsonb_array_length(a);
    ca INT := jsonb_array_length(a->0);
    rb INT := jsonb_array_length(b);
    cb INT := jsonb_array_length(b->0);
    i INT; j INT; k INT; s NUMERIC; row JSONB; res JSONB := '[]'::jsonb;
BEGIN
    IF ca <> rb THEN RAISE EXCEPTION 'non-conformable: % x % times % x %', ra, ca, rb, cb; END IF;
    FOR i IN 0 .. ra - 1 LOOP
        row := '[]'::jsonb;
        FOR j IN 0 .. cb - 1 LOOP
            s := 0;
            FOR k IN 0 .. ca - 1 LOOP
                s := s + (a->i->>k)::NUMERIC * (b->k->>j)::NUMERIC;
            END LOOP;
            row := row || to_jsonb(s);
        END LOOP;
        res := res || jsonb_build_array(row);
    END LOOP;
    RETURN res;
END;
$$;

CREATE OR REPLACE FUNCTION cert.kernel_matrix_word(p_witness JSONB)
RETURNS TABLE (ok BOOLEAN, evidence JSONB) LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    gens JSONB; word JSONB; tgt JSONB; prod JSONB; sym TEXT; i INT; n INT;
BEGIN
    gens := p_witness->'generators';
    word := p_witness->'word';
    tgt  := p_witness->'target';
    IF gens IS NULL OR word IS NULL OR tgt IS NULL
       OR jsonb_typeof(word) <> 'array' OR jsonb_array_length(word) = 0 THEN
        RETURN QUERY SELECT NULL::BOOLEAN,
            jsonb_build_object('error', 'missing generators/word/target or empty word');
        RETURN;
    END IF;

    n := jsonb_array_length(word);
    FOR i IN 0 .. n - 1 LOOP
        IF NOT (gens ? (word->>i)) THEN
            RETURN QUERY SELECT NULL::BOOLEAN,
                jsonb_build_object('error', format('undefined generator %L', word->>i));
            RETURN;
        END IF;
    END LOOP;

    prod := gens->(word->>0);
    FOR i IN 1 .. n - 1 LOOP
        sym  := word->>i;
        prod := cert.kernel_matmul(prod, gens->sym);
    END LOOP;

    RETURN QUERY SELECT (prod = tgt), jsonb_build_object(
        'kernel', 'matrix_word', 'word_length', n,
        'num_generators', (SELECT count(*) FROM jsonb_object_keys(gens)),
        'recomputed_product', prod, 'matches_target', (prod = tgt));
EXCEPTION WHEN OTHERS THEN
    RETURN QUERY SELECT NULL::BOOLEAN, jsonb_build_object('error', SQLERRM);
END;
$$;

-- CRT certificate:
--   {"schema":"crt","x":8,"congruences":[[2,3],[3,5]]}
CREATE OR REPLACE FUNCTION cert.kernel_crt(p_witness JSONB)
RETURNS TABLE (ok BOOLEAN, evidence JSONB) LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    arr JSONB;
    v_x NUMERIC;
    cong JSONB;
    r NUMERIC; m NUMERIC;
    n INT; i INT; j INT;
    v_hold BOOLEAN := TRUE;
    v_coprime BOOLEAN := TRUE;
BEGIN
    arr := p_witness->'congruences';
    IF arr IS NULL OR jsonb_typeof(arr) <> 'array'
       OR jsonb_array_length(arr) = 0 OR (p_witness->>'x') IS NULL THEN
        RETURN QUERY SELECT NULL::BOOLEAN,
            jsonb_build_object('error', 'missing x or empty/!array congruences');
        RETURN;
    END IF;

    v_x := (p_witness->>'x')::NUMERIC;
    FOR cong IN SELECT * FROM jsonb_array_elements(arr) LOOP
        r := (cong->>0)::NUMERIC;
        m := (cong->>1)::NUMERIC;
        IF m <= 0 OR (((v_x % m) + m) % m) <> (((r % m) + m) % m) THEN
            v_hold := FALSE;
        END IF;
    END LOOP;

    n := jsonb_array_length(arr);
    FOR i IN 0 .. n - 1 LOOP
        FOR j IN i + 1 .. n - 1 LOOP
            IF cert.kernel_gcd((arr->i->>1)::NUMERIC, (arr->j->>1)::NUMERIC) <> 1 THEN
                v_coprime := FALSE;
            END IF;
        END LOOP;
    END LOOP;

    RETURN QUERY SELECT (v_hold AND v_coprime), jsonb_build_object(
        'kernel', 'crt', 'x', v_x,
        'congruences_hold', v_hold, 'moduli_pairwise_coprime', v_coprime
    );
EXCEPTION WHEN OTHERS THEN
    RETURN QUERY SELECT NULL::BOOLEAN, jsonb_build_object('error', SQLERRM);
END;
$$;

-- ---------------------------------------------------------------------------
-- 2. kernel registry + dispatcher
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS cert.kernel (
    schema      TEXT PRIMARY KEY,
    checker_fn  TEXT NOT NULL,           -- schema-qualified function name
    description TEXT
);

INSERT INTO cert.kernel (schema, checker_fn, description) VALUES
    ('factorization', 'cert.kernel_factorization',
     'Recompute n = prod p^e and sigma(n) from the asserted prime factorization; '
     'trial-check each base is prime. Independent of, and cheaper than, factoring n.'),
    ('crt', 'cert.kernel_crt',
     'Verify x = r (mod m) for every congruence and that moduli are pairwise coprime. '
     'Independent of, and cheaper than, computing the CRT lift.'),
    ('unit_fraction', 'cert.kernel_unit_fraction',
     'Sum the unit fractions 1/d_i as an exact rational and check it equals the target, '
     'plus the asserted distinct/odd/positive constraints. Independent of, and far '
     'cheaper than, the Egyptian-fraction search (cf. arXiv:1606.02117).'),
    ('matrix_word', 'cert.kernel_matrix_word',
     'Multiply the generator matrices in word order and compare to target. '
     'Independent of, and far cheaper than, solving matrix-semigroup membership / '
     'the word problem (cf. arXiv:2604.15386).')
ON CONFLICT (schema) DO UPDATE
    SET checker_fn = EXCLUDED.checker_fn, description = EXCLUDED.description;

CREATE OR REPLACE FUNCTION cert.kernel_verify(p_witness JSONB)
RETURNS TABLE (ok BOOLEAN, evidence JSONB) LANGUAGE plpgsql AS $$
DECLARE v_schema TEXT; v_fn TEXT;
BEGIN
    v_schema := p_witness->>'schema';
    IF v_schema IS NULL THEN
        RETURN QUERY SELECT NULL::BOOLEAN, jsonb_build_object('error', 'witness has no schema');
        RETURN;
    END IF;
    SELECT checker_fn INTO v_fn FROM cert.kernel WHERE schema = v_schema;
    IF v_fn IS NULL THEN
        RETURN QUERY SELECT NULL::BOOLEAN,
            jsonb_build_object('error', format('no kernel registered for schema %L', v_schema));
        RETURN;
    END IF;
    RETURN QUERY EXECUTE format('SELECT ok, evidence FROM %s($1)', v_fn) USING p_witness;
END;
$$;

COMMENT ON FUNCTION cert.kernel_verify(JSONB) IS
    'Untrusted-certificate dispatcher. Routes a proof object to the registered '
    'independent kernel for its schema. Unknown/missing schema -> unverified (NULL).';

-- ---------------------------------------------------------------------------
-- 3. proof obligations: where a producer submits a candidate proof object
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS cert.proof_obligation (
    claim_id     BIGINT PRIMARY KEY REFERENCES cert.claim(id) ON DELETE CASCADE,
    witness      JSONB NOT NULL,
    submitted_by TEXT,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION cert.submit_proof(p_claim_id BIGINT, p_witness JSONB, p_by TEXT DEFAULT NULL)
RETURNS VOID LANGUAGE sql AS $$
    INSERT INTO cert.proof_obligation (claim_id, witness, submitted_by)
    VALUES (p_claim_id, p_witness, p_by)
    ON CONFLICT (claim_id) DO UPDATE
        SET witness = EXCLUDED.witness, submitted_by = EXCLUDED.submitted_by, submitted_at = now();
$$;

-- ---------------------------------------------------------------------------
-- 4. method tier + check function
-- ---------------------------------------------------------------------------

INSERT INTO cert.method (name, claim_kind, checker_kind, description) VALUES
    ('cert_kernel', 'formal', 'sql',
     'Untrusted-certificate tier. Producer submits a proof object via '
     'cert.submit_proof(); an independent in-DB kernel (cert.kernel_verify) '
     're-checks the object, not the producer. Checker is provably simpler than '
     'the producer (multiply/sum vs. factor/search).')
ON CONFLICT (name) DO NOTHING;

-- Check a cert_kernel claim: run the kernel over its submitted proof object,
-- append a certificate, and store the *checked* object as a travelling witness.
CREATE OR REPLACE FUNCTION cert.check_kernel(p_claim_id BIGINT)
RETURNS cert.certificate LANGUAGE plpgsql AS $$
DECLARE
    v_claim   cert.claim%ROWTYPE;
    v_witness JSONB;
    v_ok      BOOLEAN;
    v_ev      JSONB;
    v_status  TEXT;
    v_seq     INTEGER;
    v_inf     TEXT;
    v_under   JSONB;
    v_cert    cert.certificate%ROWTYPE;
BEGIN
    SELECT * INTO v_claim FROM cert.claim WHERE id = p_claim_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'cert.check_kernel: no claim %', p_claim_id;
    END IF;
    IF v_claim.method <> 'cert_kernel' THEN
        RETURN cert.check(p_claim_id);   -- delegate non-kernel claims unchanged
    END IF;

    SELECT witness INTO v_witness FROM cert.proof_obligation WHERE claim_id = p_claim_id;
    IF v_witness IS NULL THEN
        v_ok := NULL;
        v_ev := jsonb_build_object('note', 'no proof object submitted (cert.submit_proof)');
    ELSE
        SELECT kv.ok, kv.evidence INTO v_ok, v_ev FROM cert.kernel_verify(v_witness) kv;
    END IF;

    v_status := CASE WHEN v_ok IS TRUE  THEN 'valid'
                     WHEN v_ok IS FALSE THEN 'refuted'
                     ELSE 'unverified' END;

    v_under := jsonb_build_object(
        'curry_entities', (SELECT count(*) FROM curry.constants) + (SELECT count(*) FROM curry.functions),
        'kan_objects', (SELECT count(*) FROM kan.object),
        'tier', 'cert_kernel');

    v_inf := gen_random_uuid()::text;
    INSERT INTO curry.inferences
        (inference_id, model_name, model_version, input_tokens,
         output_tokens, temperature_used, seed, metadata)
    VALUES (
        v_inf, 'cert-checker-model', 1,
        jsonb_build_object('claim_id', p_claim_id, 'statement', v_claim.statement)::text,
        convert_to(v_status, 'UTF8'), 0.0, 0,
        jsonb_build_object('method', 'cert_kernel', 'schema', v_witness->>'schema'));

    SELECT COALESCE(MAX(seq), 0) + 1 INTO v_seq FROM cert.certificate WHERE claim_id = p_claim_id;
    INSERT INTO cert.certificate
        (claim_id, seq, status, evidence, valid_under, checker_inference_id)
    VALUES (p_claim_id, v_seq, v_status, COALESCE(v_ev, '{}'::jsonb), v_under, v_inf)
    RETURNING * INTO v_cert;

    -- The checked proof object travels with the certificate (so export_bundle
    -- carries it and a consumer can re-run the kernel offline).
    IF v_witness IS NOT NULL THEN
        INSERT INTO cert.witness (certificate_id, kind, body, schema_version)
        VALUES (v_cert.id, 'term', v_witness, v_under);
    END IF;

    RETURN v_cert;
END;
$$;

-- ---------------------------------------------------------------------------
-- 5. upgrade cert.verify: a witness that carries a registered kernel schema is
--    re-checked by the kernel, instead of the old "exists => true" fallback.
--    (Supersedes the no-probe branch from step 86; backward compatible.)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION cert.verify(p_claim_id BIGINT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB, witness JSONB) AS $$
DECLARE
    v_claim   cert.claim%ROWTYPE;
    v_ok      BOOLEAN;
    v_ev      JSONB;
    v_witness JSONB;
BEGIN
    SELECT * INTO v_claim FROM cert.claim WHERE id = p_claim_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT FALSE,
            jsonb_build_object('error', format('claim %s not found', p_claim_id)),
            NULL::JSONB;
        RETURN;
    END IF;

    SELECT w.body INTO v_witness
      FROM cert.witness w
      JOIN cert.certificate ce ON ce.id = w.certificate_id
     WHERE ce.claim_id = p_claim_id
     ORDER BY ce.seq DESC LIMIT 1;

    -- For an unchecked kernel claim, fall back to its submitted obligation.
    IF v_witness IS NULL AND v_claim.method = 'cert_kernel' THEN
        SELECT witness INTO v_witness FROM cert.proof_obligation WHERE claim_id = p_claim_id;
    END IF;

    IF v_claim.probe_sql IS NOT NULL THEN
        BEGIN
            EXECUTE v_claim.probe_sql INTO v_ok, v_ev;
        EXCEPTION WHEN OTHERS THEN
            v_ok := FALSE;
            v_ev := jsonb_build_object('error', SQLERRM);
        END;
    ELSIF v_witness IS NOT NULL
          AND v_witness ? 'schema'
          AND EXISTS (SELECT 1 FROM cert.kernel k WHERE k.schema = v_witness->>'schema') THEN
        -- Untrusted-certificate path: independently re-check the proof object.
        SELECT kv.ok, kv.evidence INTO v_ok, v_ev FROM cert.kernel_verify(v_witness) kv;
        v_ev := COALESCE(v_ev, '{}'::jsonb)
                || jsonb_build_object('verified_by', 'cert.kernel_verify');
    ELSE
        -- Legacy fallback: a witness with no checkable schema.
        v_ok := (v_witness IS NOT NULL);
        v_ev := COALESCE(v_witness, jsonb_build_object(
            'note', 'no probe_sql and no checkable witness; formal attestation required'));
    END IF;

    IF EXISTS (SELECT 1 FROM cert.derivation WHERE conclusion_id = p_claim_id) THEN
        DECLARE
            v_deriv_ok BOOLEAN; v_deriv_ev JSONB; v_deriv_id BIGINT;
        BEGIN
            SELECT id INTO v_deriv_id FROM cert.derivation WHERE conclusion_id = p_claim_id LIMIT 1;
            SELECT d.ok, d.evidence INTO v_deriv_ok, v_deriv_ev
              FROM cert.derivation_valid(v_deriv_id) d;
            v_ok := COALESCE(v_ok, TRUE) AND COALESCE(v_deriv_ok, TRUE);
            v_ev := v_ev || jsonb_build_object('derivation', v_deriv_ev);
        END;
    END IF;

    RETURN QUERY SELECT v_ok, v_ev, v_witness;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION cert.verify(BIGINT) IS
    'Side-effect-free re-verification. comp_sql/struct_kan replay probe_sql; '
    'cert_kernel (and any witness carrying a registered kernel schema) is '
    're-checked by an independent kernel via cert.kernel_verify; otherwise the '
    'stored witness is returned. Validates derivation premises when present. '
    'Produces no INSERTs.';

-- ---------------------------------------------------------------------------
-- 6. worked examples (checking is a harness step: tools/cert_kernel.py --write)
-- ---------------------------------------------------------------------------

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'calx_expr', '{"n":28}'::jsonb,
       '28 is perfect — untrusted factorization certificate (28 = 2^2*7, sigma=56)',
       'formal', 'cert_kernel', NULL
WHERE NOT EXISTS (SELECT 1 FROM cert.claim
                  WHERE statement = '28 is perfect — untrusted factorization certificate (28 = 2^2*7, sigma=56)');

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'calx_expr', '{"x":8}'::jsonb,
       'CRT certificate: x=8 solves x=2(mod 3), x=3(mod 5) with coprime moduli',
       'formal', 'cert_kernel', NULL
WHERE NOT EXISTS (SELECT 1 FROM cert.claim
                  WHERE statement = 'CRT certificate: x=8 solves x=2(mod 3), x=3(mod 5) with coprime moduli');

SELECT cert.submit_proof(c.id,
    '{"schema":"factorization","n":28,"factors":[[2,2],[7,1]],"asserts":{"perfect":true,"sigma":56}}'::jsonb,
    '94_cert_kernel.sql seed')
FROM cert.claim c
WHERE c.statement = '28 is perfect — untrusted factorization certificate (28 = 2^2*7, sigma=56)';

SELECT cert.submit_proof(c.id,
    '{"schema":"crt","x":8,"congruences":[[2,3],[3,5]]}'::jsonb,
    '94_cert_kernel.sql seed')
FROM cert.claim c
WHERE c.statement = 'CRT certificate: x=8 solves x=2(mod 3), x=3(mod 5) with coprime moduli';

-- Corpus demonstration: Egyptian-fraction certificate (Elsholtz, arXiv:1606.02117).
INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'calx_expr', '{"target":1,"arxiv":"1606.02117"}'::jsonb,
       '1 has a 9-term distinct-odd Egyptian-fraction decomposition (cf. arXiv:1606.02117)',
       'formal', 'cert_kernel', NULL
WHERE NOT EXISTS (SELECT 1 FROM cert.claim
                  WHERE statement = '1 has a 9-term distinct-odd Egyptian-fraction decomposition (cf. arXiv:1606.02117)');

SELECT cert.submit_proof(c.id,
    '{"schema":"unit_fraction","target":1,"denominators":[3,5,7,9,11,15,35,45,231],'
    '"constraints":{"distinct":true,"odd":true}}'::jsonb,
    '94_cert_kernel.sql corpus seed (1606.02117)')
FROM cert.claim c
WHERE c.statement = '1 has a 9-term distinct-odd Egyptian-fraction decomposition (cf. arXiv:1606.02117)';

-- Corpus demonstration: matrix-semigroup word certificate (Bell et al., arXiv:2604.15386).
INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'matrix_word', '{"group":"SL2Z","arxiv":"2604.15386"}'::jsonb,
       'word a*b*a over SL(2,Z) generators equals [[2,3],[1,2]] (cf. arXiv:2604.15386)',
       'formal', 'cert_kernel', NULL
WHERE NOT EXISTS (SELECT 1 FROM cert.claim
                  WHERE statement = 'word a*b*a over SL(2,Z) generators equals [[2,3],[1,2]] (cf. arXiv:2604.15386)');

SELECT cert.submit_proof(c.id,
    '{"schema":"matrix_word","generators":{"a":[[1,1],[0,1]],"b":[[1,0],[1,1]]},'
    '"word":["a","b","a"],"target":[[2,3],[1,2]]}'::jsonb,
    '94_cert_kernel.sql corpus seed (2604.15386)')
FROM cert.claim c
WHERE c.statement = 'word a*b*a over SL(2,Z) generators equals [[2,3],[1,2]] (cf. arXiv:2604.15386)';

-- Corpus demonstration: matrix-semigroup word certificate (Bell et al., arXiv:2604.15386).
INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'calx_expr', '{"group":"SL2Z","arxiv":"2604.15386"}'::jsonb,
       'the SL(2,Z) word a*b*a equals [[2,3],[1,2]] (matrix-semigroup membership, cf. arXiv:2604.15386)',
       'formal', 'cert_kernel', NULL
WHERE NOT EXISTS (SELECT 1 FROM cert.claim
                  WHERE statement = 'the SL(2,Z) word a*b*a equals [[2,3],[1,2]] (matrix-semigroup membership, cf. arXiv:2604.15386)');

SELECT cert.submit_proof(c.id,
    '{"schema":"matrix_word","generators":{"a":[[1,1],[0,1]],"b":[[1,0],[1,1]]},'
    '"word":["a","b","a"],"target":[[2,3],[1,2]]}'::jsonb,
    '94_cert_kernel.sql corpus seed (2604.15386)')
FROM cert.claim c
WHERE c.statement = 'the SL(2,Z) word a*b*a equals [[2,3],[1,2]] (matrix-semigroup membership, cf. arXiv:2604.15386)';
