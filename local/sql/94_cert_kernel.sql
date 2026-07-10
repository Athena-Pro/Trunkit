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

-- DFA / graph Betti certificate (the LQLE topological-invariant bridge):
--   {"schema":"dfa_betti","V":4,"edges":[[0,1],[1,2],[2,0],[3,3]],
--    "asserts":{"beta0":2,"beta1":2}}
-- A DFA transition graph is a 1-complex: states = vertices, transitions = edges
-- (self-loops and parallel edges count). Its homology is fully determined by
--   beta0 = connected components (undirected)
--   beta1 = E - V + beta0   (circuit rank — independent cycles)
--   chi   = V - E = beta0 - beta1   (beta_n = 0 for n >= 2)
-- Building/minimizing the DFA is the work; verifying its Betti signature is one
-- union-find pass over the edge list — the canonical untrusted-certificate split.
CREATE OR REPLACE FUNCTION cert.kernel_dfa_betti(p_witness JSONB)
RETURNS TABLE (ok BOOLEAN, evidence JSONB) LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    v_V   INT;
    edges JSONB;
    e     JSONB;
    u INT; w INT; ru INT; rw INT;
    parent INT[];
    n_edges INT := 0;
    v_comp INT;
    v_beta0 INT; v_beta1 INT; v_chi INT;
    v_ok BOOLEAN := TRUE;
    i INT;
BEGIN
    IF p_witness IS NULL OR (p_witness->>'V') IS NULL
       OR jsonb_typeof(p_witness->'edges') <> 'array' THEN
        RETURN QUERY SELECT NULL::BOOLEAN,
            jsonb_build_object('error', 'missing V or !array edges');
        RETURN;
    END IF;

    v_V := (p_witness->>'V')::INT;
    IF v_V < 1 THEN
        RETURN QUERY SELECT NULL::BOOLEAN,
            jsonb_build_object('error', 'V must be >= 1');
        RETURN;
    END IF;
    edges := p_witness->'edges';

    -- union-find over vertices 0..V-1
    parent := ARRAY(SELECT g FROM generate_series(0, v_V - 1) AS g);  -- parent[k] at index k+1

    FOR e IN SELECT * FROM jsonb_array_elements(edges) LOOP
        u := (e->>0)::INT;
        w := (e->>1)::INT;
        IF u < 0 OR u >= v_V OR w < 0 OR w >= v_V THEN
            RETURN QUERY SELECT NULL::BOOLEAN,
                jsonb_build_object('error', format('edge endpoint out of range: [%s,%s]', u, w));
            RETURN;
        END IF;
        n_edges := n_edges + 1;
        -- find(u)
        ru := u;
        WHILE parent[ru + 1] <> ru LOOP ru := parent[ru + 1]; END LOOP;
        -- find(w)
        rw := w;
        WHILE parent[rw + 1] <> rw LOOP rw := parent[rw + 1]; END LOOP;
        IF ru <> rw THEN parent[ru + 1] := rw; END IF;
    END LOOP;

    -- count distinct roots
    v_comp := 0;
    FOR i IN 0 .. v_V - 1 LOOP
        ru := i;
        WHILE parent[ru + 1] <> ru LOOP ru := parent[ru + 1]; END LOOP;
        IF ru = i THEN v_comp := v_comp + 1; END IF;
    END LOOP;

    v_beta0 := v_comp;
    v_beta1 := n_edges - v_V + v_beta0;   -- circuit rank
    v_chi   := v_V - n_edges;

    IF p_witness->'asserts' ? 'beta0' THEN
        v_ok := v_ok AND (v_beta0 = (p_witness#>>'{asserts,beta0}')::INT);
    END IF;
    IF p_witness->'asserts' ? 'beta1' THEN
        v_ok := v_ok AND (v_beta1 = (p_witness#>>'{asserts,beta1}')::INT);
    END IF;
    IF p_witness->'asserts' ? 'chi' THEN
        v_ok := v_ok AND (v_chi = (p_witness#>>'{asserts,chi}')::INT);
    END IF;

    RETURN QUERY SELECT v_ok, jsonb_build_object(
        'kernel', 'dfa_betti',
        'V', v_V, 'E', n_edges,
        'beta0', v_beta0, 'beta1', v_beta1, 'euler_char', v_chi);
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
     'the word problem (cf. arXiv:2604.15386).'),
    ('dfa_betti', 'cert.kernel_dfa_betti',
     'Recompute the Betti signature (beta0,beta1,chi) of a DFA/graph from its '
     'edge list (beta0 via union-find; beta1 = E-V+beta0). Independent of, and '
     'far cheaper than, building/minimizing the automaton (LQLE topological bridge).')
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

    -- Latest stored witness, skipping any that ride on a revoked certificate
    -- (step 100 lifecycle).
    SELECT w.body INTO v_witness
      FROM cert.witness w
      JOIN cert.certificate ce ON ce.id = w.certificate_id
     WHERE ce.claim_id = p_claim_id
       AND NOT EXISTS (SELECT 1 FROM cert.revocation r WHERE r.certificate_id = ce.id)
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
        -- Legacy fallback: a witness with no checkable schema. No witness at
        -- all means there is nothing to check: UNVERIFIED (NULL), never
        -- refuted — absence of evidence is not refutation.
        v_ok := CASE WHEN v_witness IS NOT NULL THEN TRUE END;
        v_ev := COALESCE(v_witness, jsonb_build_object(
            'note', 'no probe_sql and no checkable witness; formal attestation required'));
    END IF;

    -- Surface lifecycle state of the LATEST certificate (step 100;
    -- informational — probe replay is fresh evidence and stands on its own).
    DECLARE v_life JSONB;
    BEGIN
        SELECT jsonb_build_object('revoked_at', rv.revoked_at, 'reason', rv.reason)
          INTO v_life
          FROM cert.certificate ce
          JOIN cert.revocation rv ON rv.certificate_id = ce.id
         WHERE ce.claim_id = p_claim_id
         ORDER BY ce.seq DESC LIMIT 1;
        IF v_life IS NOT NULL THEN
            v_ev := v_ev || jsonb_build_object(
                'lifecycle', v_life || '{"state":"revoked"}'::jsonb);
        END IF;
    END;

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

-- ---- LQLE Hecke-move topological invariance (reuses the dfa_betti kernel above) ----
--
-- LQLE (research-kernel/hecke.py) defines a HeckeMove as a degree-preserving
-- two-edge swap on 4 distinct vertices (remove {(a,b),(c,d)}; add {(a,c),(b,d)}
-- or {(a,d),(b,c)}), admitted only if the result is simple and CONNECTED. Such a
-- swap keeps V and E fixed and preserves connectivity, so beta0 stays 1 and
-- beta1 = E - V + beta0 is invariant. hecke.py asserts exactly this ("the
-- certified sector is captured by (beta0,beta1) constraints").
--
-- We attest the invariance THROUGH the dfa_betti kernel defined above (the
-- LQLE->Trunkit kernel that is already imported): compute the signature before
-- and after a concrete admissible move and require equality. Proof-carrying:
-- both graphs travel in the probe; the verdict re-runs the kernel. Idempotent.
--
-- Concrete move: g0 = 4-cycle 0-1-2-3-0 (V=4,E=4,beta0=1,beta1=1). Swap removes
-- {(0,1),(2,3)} and adds {(0,2),(1,3)} (the diagonals); result 0-2-1-3-0 is again
-- a single 4-cycle, same signature. (The other rewiring {(0,3),(1,2)} is rejected
-- by hecke.py because both edges already exist in g0 — noted here for fidelity.)
INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'hecke_move',
 '{"lab":"LQLE","source":"research-kernel/hecke.py HeckeMove (degree-preserving 2-edge swap)",
   "g0":"4-cycle 0-1-2-3-0","move":{"removed":[[0,1],[2,3]],"added":[[0,2],[1,3]]},
   "kernel":"cert.kernel_dfa_betti (imported from LQLE QuineGraph TopologicalSignature)"}'::jsonb,
 'hecke-move: an LQLE Hecke move (degree-preserving 2-edge swap, connectivity-preserving) leaves the dfa_betti signature (beta0,beta1,euler_char) invariant — verified through the imported kernel on a concrete 4-cycle swap',
 'computational','comp_sql',
 $p$WITH bef AS (SELECT evidence AS e FROM cert.kernel_dfa_betti(
                '{"schema":"dfa_betti","V":4,"edges":[[0,1],[1,2],[2,3],[3,0]]}'::jsonb)),
      aft AS (SELECT evidence AS e FROM cert.kernel_dfa_betti(
                '{"schema":"dfa_betti","V":4,"edges":[[0,2],[0,3],[1,2],[1,3]]}'::jsonb))
   SELECT ( (bef.e->>'beta0') = (aft.e->>'beta0')
        AND (bef.e->>'beta1') = (aft.e->>'beta1')
        AND (bef.e->>'euler_char') = (aft.e->>'euler_char') ) AS ok,
     jsonb_build_object('before',bef.e,'after',aft.e,
       'move','remove {(0,1),(2,3)}, add {(0,2),(1,3)} — degree-preserving 2-edge swap',
       'invariant_preserved', jsonb_build_object('beta0',bef.e->'beta0','beta1',bef.e->'beta1','euler_char',bef.e->'euler_char')) AS evidence
   FROM bef, aft$p$
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement LIKE 'hecke-move: an LQLE Hecke move%');

DO $$ DECLARE c RECORD; BEGIN
  FOR c IN SELECT id FROM cert.claim WHERE statement LIKE 'hecke-move:%'
  LOOP PERFORM cert.check(c.id); END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- 3. knot_alexander kernel — Alexander polynomial of a braid closure via the
--    reduced Burau representation. Genuine knot/link invariant, exact integer
--    Laurent-polynomial arithmetic. Mirrors src/calx/kernel.py:check_knot_alexander
--    (validated there against unknot/trefoil/figure-8). Identity used (up to a
--    unit +- t^k):  det(reducedBurau(beta) - I) = Delta_L(t) * (1+t+...+t^{n-1}).
--    *Finding* a braid for a knot is hard; *checking* an asserted Alexander
--    polynomial is one matrix-chain product + determinant.
-- ---------------------------------------------------------------------------

-- Laurent polynomials as jsonb {exponent_text: coeff}; zero coeffs dropped.
CREATE OR REPLACE FUNCTION cert.lp_add(a jsonb, b jsonb) RETURNS jsonb LANGUAGE plpgsql IMMUTABLE AS $f$
DECLARE r jsonb := COALESCE(a,'{}'::jsonb); k text; v bigint; nv bigint;
BEGIN
  FOR k, v IN SELECT key, value::bigint FROM jsonb_each_text(COALESCE(b,'{}'::jsonb)) LOOP
    nv := COALESCE((r->>k)::bigint,0) + v;
    IF nv = 0 THEN r := r - k; ELSE r := jsonb_set(r, ARRAY[k], to_jsonb(nv)); END IF;
  END LOOP;
  RETURN r;
END $f$;

CREATE OR REPLACE FUNCTION cert.lp_mul(a jsonb, b jsonb) RETURNS jsonb LANGUAGE plpgsql IMMUTABLE AS $f$
DECLARE r jsonb := '{}'::jsonb; ka text; va bigint; kb text; vb bigint; e text; cur bigint;
BEGIN
  FOR ka, va IN SELECT key, value::bigint FROM jsonb_each_text(COALESCE(a,'{}'::jsonb)) LOOP
    FOR kb, vb IN SELECT key, value::bigint FROM jsonb_each_text(COALESCE(b,'{}'::jsonb)) LOOP
      e := (ka::int + kb::int)::text;
      cur := COALESCE((r->>e)::bigint,0) + va*vb;
      IF cur = 0 THEN r := r - e; ELSE r := jsonb_set(r, ARRAY[e], to_jsonb(cur)); END IF;
    END LOOP;
  END LOOP;
  RETURN r;
END $f$;

-- canonical form up to a unit (+- t^k): shift min exponent to 0, sign-fix lowest coeff.
CREATE OR REPLACE FUNCTION cert.lp_canon(p jsonb) RETURNS jsonb LANGUAGE plpgsql IMMUTABLE AS $f$
DECLARE lo int; r jsonb := '{}'::jsonb; k text; v bigint;
BEGIN
  IF p IS NULL OR p = '{}'::jsonb THEN RETURN '{}'::jsonb; END IF;
  SELECT min(key::int) INTO lo FROM jsonb_each_text(p);
  FOR k, v IN SELECT key, value::bigint FROM jsonb_each_text(p) LOOP
    r := jsonb_set(r, ARRAY[(k::int - lo)::text], to_jsonb(v));
  END LOOP;
  IF (r->>'0')::bigint < 0 THEN
    SELECT jsonb_object_agg(key, (-(value::bigint))) INTO r FROM jsonb_each_text(r);
  END IF;
  RETURN r;
END $f$;

-- reduced Burau matrix (size n-1) of generator g (+i = sigma_i, -i = inverse).
CREATE OR REPLACE FUNCTION cert.lp_burau_gen(n int, g int) RETURNS jsonb LANGUAGE plpgsql IMMUTABLE AS $f$
DECLARE m int := n-1; mat jsonb := '[]'::jsonb; r int; c int; i int := abs(g); inv boolean := g<0; i0 int; row jsonb;
        T jsonb:='{"1":1}'; ONE jsonb:='{"0":1}'; NEGT jsonb:='{"1":-1}';
        TINV jsonb:='{"-1":1}'; NEGTINV jsonb:='{"-1":-1}'; EMPTY jsonb:='{}';
BEGIN
  IF i<1 OR i>n-1 THEN RAISE EXCEPTION 'generator % out of range for B_%', g, n; END IF;
  FOR r IN 0..m-1 LOOP
    row := '[]'::jsonb;
    FOR c IN 0..m-1 LOOP row := row || jsonb_build_array(CASE WHEN r=c THEN ONE ELSE EMPTY END); END LOOP;
    mat := mat || jsonb_build_array(row);
  END LOOP;
  IF m = 1 THEN
    mat := jsonb_set(mat, ARRAY['0','0'], CASE WHEN inv THEN NEGTINV ELSE NEGT END);
    RETURN mat;
  END IF;
  IF i = 1 THEN
    IF inv THEN
      mat:=jsonb_set(mat,ARRAY['0','0'],NEGTINV); mat:=jsonb_set(mat,ARRAY['1','0'],TINV); mat:=jsonb_set(mat,ARRAY['1','1'],ONE);
    ELSE
      mat:=jsonb_set(mat,ARRAY['0','0'],NEGT);    mat:=jsonb_set(mat,ARRAY['1','0'],ONE);  mat:=jsonb_set(mat,ARRAY['1','1'],ONE);
    END IF;
  ELSIF i = n-1 THEN
    i0:=n-3;
    IF inv THEN
      mat:=jsonb_set(mat,ARRAY[i0::text,i0::text],ONE); mat:=jsonb_set(mat,ARRAY[i0::text,(i0+1)::text],ONE); mat:=jsonb_set(mat,ARRAY[(i0+1)::text,(i0+1)::text],NEGTINV);
    ELSE
      mat:=jsonb_set(mat,ARRAY[i0::text,i0::text],ONE); mat:=jsonb_set(mat,ARRAY[i0::text,(i0+1)::text],T);   mat:=jsonb_set(mat,ARRAY[(i0+1)::text,(i0+1)::text],NEGT);
    END IF;
  ELSE
    i0:=i-2;
    IF inv THEN -- [[1,1,0],[0,-t^-1,0],[0,t^-1,1]]
      mat:=jsonb_set(mat,ARRAY[i0::text,(i0+1)::text],ONE);
      mat:=jsonb_set(mat,ARRAY[(i0+1)::text,(i0+1)::text],NEGTINV);
      mat:=jsonb_set(mat,ARRAY[(i0+2)::text,(i0+1)::text],TINV);
    ELSE -- [[1,t,0],[0,-t,0],[0,1,1]]
      mat:=jsonb_set(mat,ARRAY[i0::text,(i0+1)::text],T);
      mat:=jsonb_set(mat,ARRAY[(i0+1)::text,(i0+1)::text],NEGT);
      mat:=jsonb_set(mat,ARRAY[(i0+2)::text,(i0+1)::text],ONE);
    END IF;
  END IF;
  RETURN mat;
END $f$;

CREATE OR REPLACE FUNCTION cert.lp_matmul(A jsonb, B jsonb) RETURNS jsonb LANGUAGE plpgsql IMMUTABLE AS $f$
DECLARE n int := jsonb_array_length(A); r int; c int; k int; acc jsonb; row jsonb; M jsonb := '[]'::jsonb;
BEGIN
  FOR r IN 0..n-1 LOOP
    row := '[]'::jsonb;
    FOR c IN 0..n-1 LOOP
      acc := '{}'::jsonb;
      FOR k IN 0..n-1 LOOP acc := cert.lp_add(acc, cert.lp_mul((A->r)->k, (B->k)->c)); END LOOP;
      row := row || jsonb_build_array(acc);
    END LOOP;
    M := M || jsonb_build_array(row);
  END LOOP;
  RETURN M;
END $f$;

CREATE OR REPLACE FUNCTION cert.lp_det(M jsonb) RETURNS jsonb LANGUAGE plpgsql IMMUTABLE AS $f$
DECLARE n int := jsonb_array_length(M); c int; rr int; cc int; minor jsonb; row jsonb;
        total jsonb := '{}'::jsonb; term jsonb; NEG1 jsonb := '{"0":-1}';
BEGIN
  IF n = 1 THEN RETURN (M->0)->0; END IF;
  IF n = 2 THEN
    RETURN cert.lp_add(cert.lp_mul((M->0)->0,(M->1)->1),
                       cert.lp_mul(NEG1, cert.lp_mul((M->0)->1,(M->1)->0)));
  END IF;
  FOR c IN 0..n-1 LOOP
    minor := '[]'::jsonb;
    FOR rr IN 1..n-1 LOOP
      row := '[]'::jsonb;
      FOR cc IN 0..n-1 LOOP IF cc<>c THEN row := row || jsonb_build_array((M->rr)->cc); END IF; END LOOP;
      minor := minor || jsonb_build_array(row);
    END LOOP;
    term := cert.lp_mul((M->0)->c, cert.lp_det(minor));
    IF c % 2 = 0 THEN total := cert.lp_add(total, term);
    ELSE total := cert.lp_add(total, cert.lp_mul(NEG1, term)); END IF;
  END LOOP;
  RETURN total;
END $f$;

CREATE OR REPLACE FUNCTION cert.kernel_knot_alexander(p_witness jsonb)
RETURNS TABLE(ok boolean, evidence jsonb) LANGUAGE plpgsql AS $f$
DECLARE n int; braid jsonb; m int; prod jsonb; g int; r int; c int; row jsonb;
        MmI jsonb; lhs jsonb; cycl jsonb := '{}'::jsonb; e int;
        alex jsonb; min_exp int; delta jsonb := '{}'::jsonb; j int := 0;
        rhs jsonb; v_ok boolean; det_m1 bigint; k text; v bigint;
BEGIN
  IF p_witness->>'n' IS NULL OR jsonb_typeof(p_witness->'braid') <> 'array' THEN
    RETURN QUERY SELECT NULL::boolean, jsonb_build_object('error','need n and braid array'); RETURN;
  END IF;
  n := (p_witness->>'n')::int; braid := p_witness->'braid'; m := n-1;
  IF n < 2 THEN RETURN QUERY SELECT NULL::boolean, jsonb_build_object('error','n>=2'); RETURN; END IF;
  prod := '[]'::jsonb;                                            -- identity (size m)
  FOR r IN 0..m-1 LOOP
    row := '[]'::jsonb;
    FOR c IN 0..m-1 LOOP row := row || jsonb_build_array(CASE WHEN r=c THEN '{"0":1}'::jsonb ELSE '{}'::jsonb END); END LOOP;
    prod := prod || jsonb_build_array(row);
  END LOOP;
  FOR g IN SELECT value::int FROM jsonb_array_elements_text(braid) LOOP
    prod := cert.lp_matmul(prod, cert.lp_burau_gen(n, g));
  END LOOP;
  MmI := '[]'::jsonb;                                             -- prod - I
  FOR r IN 0..m-1 LOOP
    row := '[]'::jsonb;
    FOR c IN 0..m-1 LOOP
      IF r=c THEN row := row || jsonb_build_array(cert.lp_add((prod->r)->c, '{"0":-1}'::jsonb));
      ELSE         row := row || jsonb_build_array((prod->r)->c); END IF;
    END LOOP;
    MmI := MmI || jsonb_build_array(row);
  END LOOP;
  lhs := cert.lp_det(MmI);
  FOR e IN 0..n-1 LOOP cycl := jsonb_set(cycl, ARRAY[e::text], to_jsonb(1)); END LOOP;
  alex := p_witness->'alexander';
  IF alex IS NULL OR jsonb_typeof(alex) <> 'object' THEN
    RETURN QUERY SELECT NULL::boolean, jsonb_build_object('error','no asserted alexander','recomputed_det',lhs); RETURN;
  END IF;
  min_exp := COALESCE((alex->>'min_exp')::int, 0);
  FOR v IN SELECT value::bigint FROM jsonb_array_elements_text(alex->'coeffs') LOOP
    IF v <> 0 THEN delta := jsonb_set(delta, ARRAY[(min_exp+j)::text], to_jsonb(v)); END IF;
    j := j+1;
  END LOOP;
  rhs := cert.lp_mul(delta, cycl);
  v_ok := cert.lp_canon(lhs) = cert.lp_canon(rhs);
  det_m1 := NULL;
  IF p_witness->'asserts' ? 'determinant' THEN
    det_m1 := 0;
    FOR k, v IN SELECT key, value::bigint FROM jsonb_each_text(delta) LOOP
      det_m1 := det_m1 + v * (CASE WHEN (k::int) % 2 = 0 THEN 1 ELSE -1 END);
    END LOOP;
    det_m1 := abs(det_m1);
    v_ok := v_ok AND det_m1 = (p_witness#>>'{asserts,determinant}')::bigint;
  END IF;
  RETURN QUERY SELECT v_ok, jsonb_build_object(
    'kernel','knot_alexander','n',n,'braid_length',jsonb_array_length(braid),
    'recomputed_det',lhs,'rhs_delta_times_cyclotomic',rhs,
    'matches_up_to_units', cert.lp_canon(lhs)=cert.lp_canon(rhs),
    'knot_determinant_abs_delta_at_-1', det_m1);
END $f$;

INSERT INTO cert.kernel (schema, checker_fn, description) VALUES
    ('knot_alexander', 'cert.kernel_knot_alexander',
     'Recompute det(reducedBurau(beta) - I) for a braid beta and check it equals the '
     'asserted Alexander polynomial times (1+t+...+t^{n-1}) up to a unit; optional knot '
     'determinant |Delta(-1)|. Independent of, and far cheaper than, finding the braid.')
ON CONFLICT (schema) DO UPDATE
    SET checker_fn = EXCLUDED.checker_fn, description = EXCLUDED.description;

-- Corpus demonstrations: classic knots as braid closures (cert_kernel tier).
INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'knot', '{"knot":"trefoil 3_1","braid":"sigma_1^3 in B_2","determinant":3}'::jsonb,
       'the trefoil (closure of sigma_1^3 in B_2) has Alexander polynomial t^2 - t + 1 and determinant 3',
       'formal', 'cert_kernel', NULL
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement LIKE 'the trefoil (closure of sigma_1^3%');
SELECT cert.submit_proof(c.id,
    '{"schema":"knot_alexander","n":2,"braid":[1,1,1],"alexander":{"min_exp":0,"coeffs":[1,-1,1]},"asserts":{"determinant":3}}'::jsonb,
    '94_cert_kernel.sql knot corpus seed')
FROM cert.claim c WHERE c.statement LIKE 'the trefoil (closure of sigma_1^3%';

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'knot', '{"knot":"figure-eight 4_1","braid":"(sigma_1 sigma_2^-1)^2 in B_3","determinant":5}'::jsonb,
       'the figure-eight knot (closure of (sigma_1 sigma_2^-1)^2 in B_3) has Alexander polynomial t^2 - 3t + 1 and determinant 5',
       'formal', 'cert_kernel', NULL
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement LIKE 'the figure-eight knot (closure%');
SELECT cert.submit_proof(c.id,
    '{"schema":"knot_alexander","n":3,"braid":[1,-2,1,-2],"alexander":{"min_exp":0,"coeffs":[1,-3,1]},"asserts":{"determinant":5}}'::jsonb,
    '94_cert_kernel.sql knot corpus seed')
FROM cert.claim c WHERE c.statement LIKE 'the figure-eight knot (closure%';

DO $$ DECLARE c RECORD; BEGIN
  FOR c IN SELECT id FROM cert.claim WHERE statement LIKE 'the trefoil (closure of sigma_1^3%'
                                        OR statement LIKE 'the figure-eight knot (closure%'
  LOOP PERFORM cert.check_kernel(c.id); END LOOP;   -- cert_kernel tier checker
END $$;

-- ---------------------------------------------------------------------------
-- 4. derivation DAG activation — the first real cert.derivation (modus ponens).
--    The ledger has been a pure hash CHAIN (beta1 = 0); per docs/TOOL_ON_TOOL_TOPOLOGY.md
--    a single modus_ponens conclusion C <= {P, P->Q} raises the tamper-entanglement
--    beta1 from 0 to 2 (one cycle per premise, since each premise is already
--    chain-linked to the conclusion). We compose the knot kernel's own output:
--      P    : trefoil Alexander polynomial = t^2 - t + 1            (cert_kernel)
--      P->Q : if Delta = t^2 - t + 1 then determinant |Delta(-1)| = 3 (comp_sql, exact)
--      Q    : the trefoil knot determinant = 3                      (cert_kernel)
--    ORDER MATTERS: a certificate snapshots its premise hashes at attestation time,
--    so the derivation must be recorded BEFORE the conclusion Q is checked.
-- ---------------------------------------------------------------------------

-- P: Alexander polynomial only (no determinant assert)
INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'knot', '{"knot":"trefoil","role":"premise P"}'::jsonb,
       'derivation-demo: the trefoil (sigma_1^3 in B_2) has Alexander polynomial t^2 - t + 1',
       'formal', 'cert_kernel', NULL
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement = 'derivation-demo: the trefoil (sigma_1^3 in B_2) has Alexander polynomial t^2 - t + 1');
SELECT cert.submit_proof(c.id,
    '{"schema":"knot_alexander","n":2,"braid":[1,1,1],"alexander":{"min_exp":0,"coeffs":[1,-1,1]}}'::jsonb,
    'derivation-demo premise P')
FROM cert.claim c WHERE c.statement = 'derivation-demo: the trefoil (sigma_1^3 in B_2) has Alexander polynomial t^2 - t + 1';

-- P->Q: the arithmetic implication, exact and self-contained (|(-1)^2 - (-1) + 1| = 3)
INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'knot', '{"knot":"trefoil","role":"implication P->Q","rule":"determinant = |Delta(-1)|"}'::jsonb,
       'derivation-demo: if a knot has Alexander polynomial t^2 - t + 1 then its determinant |Delta(-1)| = 3',
       'computational', 'comp_sql',
       $p$SELECT (d = 3) AS ok, jsonb_build_object('delta_at_-1', d, 'abs', abs(d)) AS evidence
          FROM (SELECT abs(1*(-1)^2 - 1*(-1) + 1) AS d) t$p$
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement = 'derivation-demo: if a knot has Alexander polynomial t^2 - t + 1 then its determinant |Delta(-1)| = 3');

-- Q: the conclusion (also independently kernel-checkable, with the determinant assert)
INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'knot', '{"knot":"trefoil","role":"conclusion Q"}'::jsonb,
       'derivation-demo: the trefoil knot determinant is 3',
       'formal', 'cert_kernel', NULL
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement = 'derivation-demo: the trefoil knot determinant is 3');
SELECT cert.submit_proof(c.id,
    '{"schema":"knot_alexander","n":2,"braid":[1,1,1],"alexander":{"min_exp":0,"coeffs":[1,-1,1]},"asserts":{"determinant":3}}'::jsonb,
    'derivation-demo conclusion Q')
FROM cert.claim c WHERE c.statement = 'derivation-demo: the trefoil knot determinant is 3';

-- attest the premises first (no derivation yet)
DO $$ DECLARE c RECORD; BEGIN
  FOR c IN SELECT id FROM cert.claim WHERE statement = 'derivation-demo: the trefoil (sigma_1^3 in B_2) has Alexander polynomial t^2 - t + 1'
  LOOP PERFORM cert.check_kernel(c.id); END LOOP;
  FOR c IN SELECT id FROM cert.claim WHERE statement = 'derivation-demo: if a knot has Alexander polynomial t^2 - t + 1 then its determinant |Delta(-1)| = 3'
  LOOP PERFORM cert.check(c.id); END LOOP;
END $$;

-- record the derivation Q <= {P, P->Q}, THEN attest Q so it snapshots premise hashes
INSERT INTO cert.derivation (conclusion_id, premise_ids, rule)
SELECT q.id, ARRAY[p.id, pq.id], 'modus_ponens'
FROM cert.claim q, cert.claim p, cert.claim pq
WHERE q.statement  = 'derivation-demo: the trefoil knot determinant is 3'
  AND p.statement  = 'derivation-demo: the trefoil (sigma_1^3 in B_2) has Alexander polynomial t^2 - t + 1'
  AND pq.statement = 'derivation-demo: if a knot has Alexander polynomial t^2 - t + 1 then its determinant |Delta(-1)| = 3'
  AND NOT EXISTS (SELECT 1 FROM cert.derivation d WHERE d.conclusion_id = q.id);

DO $$ DECLARE c RECORD; BEGIN
  FOR c IN SELECT id FROM cert.claim WHERE statement = 'derivation-demo: the trefoil knot determinant is 3'
  LOOP PERFORM cert.check_kernel(c.id); END LOOP;
END $$;
