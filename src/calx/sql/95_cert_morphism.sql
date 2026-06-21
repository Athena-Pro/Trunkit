-- Unified model, step 95: verified sequence morphisms (#2, functorial hooks).
--
-- The principled successor to cosine: where the OEIS cosine layer (92) only
-- *proposes* that two sequences are related ("Fib looks like Fib×2"), a morphism
-- certificate *proves* the exact structural map between them. This is the
-- tractable, minimal slice of the functorial direction — a verified morphism in
-- the category of sequences — checked exactly over the stored prefixes.
--
-- Map kinds (params JSONB):
--   'affine'      {a, b}  ->  y_n = a·x_n + b
--   'scale'       {c}     ->  y_n = c·x_n        (special affine; the cosine-flagged case)
--   'index_shift' {s}     ->  y_n = x_{n+s}      (s >= 0)
--
-- Deeper extension (noted, not built here): automaton-level morphisms via Nerode
-- 70_morphism + kan struct_kan. This layer is exact (domain exact_int → passes
-- the 94 shield) and pure SQL. Idempotent.

INSERT INTO cert.method (name, claim_kind, checker_kind, description)
VALUES ('comp_sql', 'computational', 'sql', 'in-DB probe returning (ok, evidence)')
ON CONFLICT (name) DO NOTHING;

-- Apply a morphism to a source term vector (exact NUMERIC, order-preserving).
CREATE OR REPLACE FUNCTION cert.morphism_apply(p_kind TEXT, p_params JSONB, p_src NUMERIC[])
RETURNS NUMERIC[] LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE a NUMERIC; b NUMERIC; s INT; n INT := COALESCE(array_length(p_src,1),0);
BEGIN
    IF p_kind IN ('affine','scale') THEN
        a := COALESCE((p_params->>'a')::numeric, (p_params->>'c')::numeric, 1);
        b := COALESCE((p_params->>'b')::numeric, 0);
        RETURN ARRAY(SELECT a*val + b FROM unnest(p_src) WITH ORDINALITY t(val,o) ORDER BY o);
    ELSIF p_kind = 'index_shift' THEN
        s := (p_params->>'s')::int;
        IF s < 0 THEN RAISE EXCEPTION 'index_shift requires s >= 0'; END IF;
        RETURN p_src[(1+s):n];
    ELSE
        RAISE EXCEPTION 'unknown morphism kind %', p_kind;
    END IF;
END $$;

-- Self-contained registry of morphism certificates between two sequences.
CREATE TABLE IF NOT EXISTS cert.morphism (
    id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    src_seq   TEXT NOT NULL,
    dst_seq   TEXT NOT NULL,
    kind      TEXT NOT NULL,
    params    JSONB NOT NULL DEFAULT '{}'::jsonb,
    src_terms NUMERIC[] NOT NULL,
    dst_terms NUMERIC[] NOT NULL,
    built_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (src_seq, dst_seq, kind)
);

CREATE OR REPLACE FUNCTION cert.register_morphism(
    p_src TEXT, p_dst TEXT, p_kind TEXT, p_params JSONB,
    p_src_terms NUMERIC[], p_dst_terms NUMERIC[]
) RETURNS cert.morphism
LANGUAGE plpgsql AS $$
DECLARE v_row cert.morphism%ROWTYPE;
BEGIN
    INSERT INTO cert.morphism (src_seq, dst_seq, kind, params, src_terms, dst_terms)
    VALUES (p_src, p_dst, p_kind, p_params, p_src_terms, p_dst_terms)
    ON CONFLICT (src_seq, dst_seq, kind) DO UPDATE
        SET params = EXCLUDED.params, src_terms = EXCLUDED.src_terms,
            dst_terms = EXCLUDED.dst_terms, built_at = now()
    RETURNING * INTO v_row;
    RETURN v_row;
END $$;

-- Verify the morphism maps src onto dst exactly over the common prefix.
CREATE OR REPLACE FUNCTION cert.morphism_matches(p_id BIGINT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE m cert.morphism%ROWTYPE; mapped NUMERIC[]; len INT; i INT;
BEGIN
    SELECT * INTO m FROM cert.morphism WHERE id = p_id;
    IF NOT FOUND THEN
        ok := false; evidence := jsonb_build_object('reason','no morphism','id',p_id);
        RETURN NEXT; RETURN;
    END IF;
    BEGIN
        mapped := cert.morphism_apply(m.kind, m.params, m.src_terms);
    EXCEPTION WHEN OTHERS THEN
        ok := false; evidence := jsonb_build_object('reason','apply failed','detail',SQLERRM);
        RETURN NEXT; RETURN;
    END;
    len := LEAST(COALESCE(array_length(mapped,1),0), COALESCE(array_length(m.dst_terms,1),0));
    IF len = 0 THEN
        ok := false; evidence := jsonb_build_object('reason','empty overlap');
        RETURN NEXT; RETURN;
    END IF;
    FOR i IN 1 .. len LOOP
        IF mapped[i] IS DISTINCT FROM m.dst_terms[i] THEN
            ok := false;
            evidence := jsonb_build_object('reason','morphism mismatch','at',i,
                                           'expected',m.dst_terms[i],'got',mapped[i]);
            RETURN NEXT; RETURN;
        END IF;
    END LOOP;
    ok := true;
    evidence := jsonb_build_object('kind',m.kind,'params',m.params,'verified_terms',len,'exact',true);
    RETURN NEXT;
END $$;

-- Build a re-checkable comp_sql claim for the morphism; tag it exact_int (94).
CREATE OR REPLACE FUNCTION cert.morphism_claim(p_id BIGINT)
RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE m cert.morphism%ROWTYPE; v_stmt TEXT; v_probe TEXT; v_id BIGINT;
BEGIN
    SELECT * INTO m FROM cert.morphism WHERE id = p_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'no morphism %', p_id; END IF;
    v_stmt := format('%s = %s(%s) via %s morphism [morph #%s]',
                     m.dst_seq, m.kind, m.src_seq, m.kind, m.id);
    v_probe := format('SELECT ok, evidence FROM cert.morphism_matches(%s)', m.id);
    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql, domain)
    VALUES ('morphism',
            jsonb_build_object('src', m.src_seq, 'dst', m.dst_seq, 'kind', m.kind, 'morphism_id', m.id),
            v_stmt, 'computational', 'comp_sql', v_probe, 'exact_int')
    ON CONFLICT (statement) DO UPDATE SET probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_id;
    RETURN v_id;
END $$;
