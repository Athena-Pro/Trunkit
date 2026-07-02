-- =============================================================================
--  nerode — Step 14: DFA Morphisms
--
--  A DFA homomorphism (transition morphism) from M1=(Q1,Σ,δ1,q01,F1) to
--  M2=(Q2,Σ,δ2,q02,F2) is a function f: Q1→Q2 such that:
--    (i)  f(q01) = q02                                      (initial-state)
--    (ii) f(δ1(q,s)) = δ2(f(q),s)  for all q∈Q1, s∈Σ      (transition)
--
--  NOTE: Condition (iii) — acceptance preservation q∈F1 ↔ f(q)∈F2 — is NOT
--  required here.  The product DFA recognises L(cycle_m) ∩ L(cycle_n) (strings
--  of length divisible by lcm(m,n)), whereas each factor recognises a strictly
--  larger language (multiples of m or n).  The projection maps f(q1,q2)=q1 and
--  f(q1,q2)=q2 are valid transition morphisms (quotient maps) but do not
--  preserve acceptance.  This is the intended behaviour: we classify structural
--  relationships between automata, not just language-isomorphisms.
--
--  For cycle languages: the projection product→factor always exists and is an
--  epimorphism.  Direct cycle_m→cycle_n morphisms exist only when the BFS
--  finds no transition contradiction (equivalently when n | m).
--
--  The product DFA of cycle_m ∩ cycle_n has lcm(m,n) reachable states.
--  It admits two canonical epimorphisms: one to cycle_m, one to cycle_n.
--  (These are quotient maps — the projection onto each factor.)
--
--  Functions:
--    nerode.find_dfa_morphism(src_id, tgt_id) → JSONB | NULL
--        BFS from the initial state pair (q0_src, q0_tgt).
--        Greedily builds the forced mapping; returns NULL on contradiction.
--        The morphism is unique when it exists (reachability forces every value).
--
--    nerode.register_morphism(src_id, tgt_id) → BIGINT | NULL
--        Calls find_dfa_morphism; if found, classifies kind, inserts into
--        nerode.morphisms, issues cert.claim + cert.witness(state_map).
--        Returns morphism id, or NULL if no morphism exists.
--
--    nerode.build_morphism_corpus() → TABLE(...)
--        For every product pair: register the morphism product→lhs and
--        product→rhs.  Idempotent.
--
--  Table:
--    nerode.morphisms  (src_id, tgt_id, kind, state_map, cert_claim_id)
--
--  Morphism kinds:
--    epimorphism   surjective  (image = Q_tgt)
--    embedding     injective   (no two src states share a tgt state)
--    isomorphism   bijective
--    homomorphism  general
-- =============================================================================

-- The 'state_map' witness kind this layer writes is part of the canonical
-- cert_witness_kind_check vocabulary owned by 00_bootstrap.sql (applied
-- earlier in the same pass). Do not drop/re-add the constraint here.

-- ---------------------------------------------------------------------------
-- Register cert method for morphisms.
-- ---------------------------------------------------------------------------
INSERT INTO cert.method (name, claim_kind, checker_kind, description)
VALUES (
    'nerode_morphism',
    'structural',
    'sql',
    'DFA homomorphism: a function f: Q_src → Q_tgt that preserves the initial '
    'state, transition structure, and language membership (accepting iff mapped '
    'state accepts).  BFS from the initial pair (q0_src, q0_tgt) discovers the '
    'unique forced mapping; returns NULL if any contradiction arises.  '
    'kind = epimorphism (surjective onto Q_tgt), embedding (injective), '
    'isomorphism (bijective), or homomorphism (general).  '
    'Witness kind = state_map.'
) ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- nerode.morphisms — registry of certified DFA homomorphisms
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nerode.morphisms (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    src_id        BIGINT NOT NULL REFERENCES nerode.automata(id),
    tgt_id        BIGINT NOT NULL REFERENCES nerode.automata(id),
    kind          TEXT   NOT NULL DEFAULT 'homomorphism'
                  CHECK (kind IN ('homomorphism', 'epimorphism', 'embedding', 'isomorphism')),
    state_map     JSONB  NOT NULL,   -- {src_state_id::text → tgt_state_id}
    cert_claim_id BIGINT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (src_id, tgt_id)
);

COMMENT ON TABLE  nerode.morphisms IS
    'Certified DFA homomorphisms.  state_map encodes f: Q_src → Q_tgt as a '
    'JSONB object {src_state_id::text: tgt_state_id}.';
COMMENT ON COLUMN nerode.morphisms.kind IS
    'epimorphism: surjective (image covers all of Q_tgt).  '
    'embedding: injective (no two src states share a tgt image).  '
    'isomorphism: bijective.  homomorphism: general.';

-- ---------------------------------------------------------------------------
-- nerode.find_dfa_morphism(p_src, p_tgt)
--
-- Returns the unique DFA transition-morphism f: Q_src → Q_tgt as a JSONB
-- object {src_state::text: tgt_state}, or NULL if none exists.
--
-- Algorithm:
--   Seed  f(q0_src) = q0_tgt.
--   BFS from q0_src: for each reachable state q and each symbol s,
--     the morphism is forced: f(δ(q,s)) must equal δ(f(q),s).
--     If f(δ(q,s)) is already set to a different value → contradiction → NULL.
-- Acceptance preservation is NOT checked (see top-of-file note).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.find_dfa_morphism(
    p_src BIGINT,
    p_tgt BIGINT
)
RETURNS JSONB AS $$
DECLARE
    v_src       nerode.automata%ROWTYPE;
    v_tgt       nerode.automata%ROWTYPE;
    v_symbols   TEXT[];
    v_init_src  INTEGER;
    v_init_tgt  INTEGER;
    v_map       JSONB    := '{}';
    v_queue     INTEGER[];
    v_seen      INTEGER[];
    v_cur_src   INTEGER;
    v_cur_tgt   INTEGER;
    v_sym       TEXT;
    v_nxt_src   INTEGER;
    v_nxt_tgt   INTEGER;
    v_prev_tgt  INTEGER;
BEGIN
    -- -----------------------------------------------------------------------
    -- Fetch and validate automata
    -- -----------------------------------------------------------------------
    SELECT * INTO v_src FROM nerode.automata WHERE id = p_src;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'find_dfa_morphism: src automaton % not found', p_src;
    END IF;
    SELECT * INTO v_tgt FROM nerode.automata WHERE id = p_tgt;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'find_dfa_morphism: tgt automaton % not found', p_tgt;
    END IF;
    IF v_src.type != 'DFA' OR v_tgt.type != 'DFA' THEN
        RETURN NULL;
    END IF;
    IF v_src.alphabet_id != v_tgt.alphabet_id THEN
        RETURN NULL;
    END IF;

    SELECT symbols INTO v_symbols
    FROM nerode.alphabets WHERE id = v_src.alphabet_id;

    -- -----------------------------------------------------------------------
    -- Seed: f(q0_src) = q0_tgt
    -- -----------------------------------------------------------------------
    SELECT state_id INTO v_init_src
    FROM nerode.states WHERE automaton_id = p_src AND is_initial LIMIT 1;
    SELECT state_id INTO v_init_tgt
    FROM nerode.states WHERE automaton_id = p_tgt AND is_initial LIMIT 1;

    v_map   := jsonb_build_object(v_init_src::text, v_init_tgt);
    v_queue := ARRAY[v_init_src];
    v_seen  := ARRAY[]::INTEGER[];

    -- -----------------------------------------------------------------------
    -- BFS: greedily extend the forced mapping
    -- -----------------------------------------------------------------------
    WHILE array_length(v_queue, 1) IS NOT NULL AND array_length(v_queue, 1) > 0 LOOP
        v_cur_src := v_queue[1];
        v_queue   := v_queue[2:];

        IF v_cur_src = ANY(v_seen) THEN CONTINUE; END IF;
        v_seen    := v_seen || v_cur_src;
        v_cur_tgt := (v_map->>(v_cur_src::text))::integer;

        FOREACH v_sym IN ARRAY v_symbols LOOP
            -- Where does src go?
            SELECT to_state INTO v_nxt_src
            FROM nerode.transitions
            WHERE automaton_id = p_src AND from_state = v_cur_src AND symbol = v_sym;
            IF NOT FOUND THEN CONTINUE; END IF;

            -- Where does tgt go from the mapped state?
            SELECT to_state INTO v_nxt_tgt
            FROM nerode.transitions
            WHERE automaton_id = p_tgt AND from_state = v_cur_tgt AND symbol = v_sym;
            IF NOT FOUND THEN CONTINUE; END IF;

            IF v_map ? (v_nxt_src::text) THEN
                -- Already mapped: check for contradiction
                v_prev_tgt := (v_map->>(v_nxt_src::text))::integer;
                IF v_prev_tgt != v_nxt_tgt THEN
                    RETURN NULL;    -- contradiction → no morphism exists
                END IF;
            ELSE
                -- New mapping: record and enqueue
                v_map   := v_map || jsonb_build_object(v_nxt_src::text, v_nxt_tgt);
                IF NOT (v_nxt_src = ANY(v_seen)) THEN
                    v_queue := v_queue || ARRAY[v_nxt_src];
                END IF;
            END IF;
        END LOOP;
    END LOOP;

    RETURN v_map;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.find_dfa_morphism(BIGINT, BIGINT) IS
    'BFS-based DFA transition-morphism finder.  Returns state_map JSONB or NULL.  '
    'Preserves initial state and transition structure.  Acceptance is not required '
    'to be preserved (quotient/projection maps are valid morphisms).  '
    'The morphism is unique: BFS from the initial state forces every value.';

-- ---------------------------------------------------------------------------
-- nerode.register_morphism(p_src, p_tgt)
--
-- Calls find_dfa_morphism.  If a morphism exists:
--   • Classifies kind (epimorphism / embedding / isomorphism / homomorphism).
--   • Upserts a row in nerode.morphisms.
--   • Issues cert.claim + cert.certificate + cert.witness(state_map).
-- Returns the morphism id, or NULL if no morphism exists.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.register_morphism(
    p_src BIGINT,
    p_tgt BIGINT
)
RETURNS BIGINT AS $$
DECLARE
    v_map        JSONB;
    v_kind       TEXT;
    v_domain     INTEGER;
    v_image      INTEGER;
    v_tgt_sc     INTEGER;
    v_morph_id   BIGINT;
    v_claim_id   BIGINT;
    v_cert_id    BIGINT;
    v_seq        INTEGER;
    v_injective  BOOLEAN;
    v_surjective BOOLEAN;
BEGIN
    v_map := nerode.find_dfa_morphism(p_src, p_tgt);
    IF v_map IS NULL THEN RETURN NULL; END IF;

    -- Count domain (# keys) and image (# distinct values)
    SELECT count(*)          INTO v_domain FROM jsonb_object_keys(v_map);
    SELECT count(DISTINCT value) INTO v_image  FROM jsonb_each_text(v_map);
    SELECT state_count       INTO v_tgt_sc FROM nerode.automata WHERE id = p_tgt;

    v_injective  := (v_domain = v_image);
    v_surjective := (v_image  = v_tgt_sc);

    IF    v_injective AND v_surjective THEN v_kind := 'isomorphism';
    ELSIF v_surjective                 THEN v_kind := 'epimorphism';
    ELSIF v_injective                  THEN v_kind := 'embedding';
    ELSE                                    v_kind := 'homomorphism';
    END IF;

    -- Upsert morphism row
    INSERT INTO nerode.morphisms (src_id, tgt_id, kind, state_map)
    VALUES (p_src, p_tgt, v_kind, v_map)
    ON CONFLICT (src_id, tgt_id) DO UPDATE
        SET kind      = EXCLUDED.kind,
            state_map = EXCLUDED.state_map
    RETURNING id INTO v_morph_id;

    -- Certify: insert directly to avoid claim-statement collision when two
    -- morphisms share the same src automaton within the same transaction
    -- (nerode.certify() embeds only p_automaton_id in the statement, not p_tgt,
    -- so two morphisms from the same product DFA would share one claim row).
    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method)
    VALUES (
        'nerode_automaton',
        jsonb_build_object('automaton_id', p_src, 'tgt_id', p_tgt, 'operation', 'morphism'),
        format('nerode.morphism from %s to %s certified at %s', p_src, p_tgt, now()),
        'structural',
        'nerode_morphism'
    )
    ON CONFLICT (statement) DO UPDATE SET subject_ref = EXCLUDED.subject_ref
    RETURNING id INTO v_claim_id;

    SELECT COALESCE(max(seq), 0) + 1 INTO v_seq
    FROM cert.certificate WHERE claim_id = v_claim_id;

    INSERT INTO cert.certificate (claim_id, seq, status, evidence, valid_under)
    VALUES (
        v_claim_id, v_seq, 'valid',
        jsonb_build_object(
            'src_id',      p_src,
            'tgt_id',      p_tgt,
            'kind',        v_kind,
            'morphism_id', v_morph_id
        ),
        jsonb_build_object('nerode_schema_version', 1)
    )
    RETURNING id INTO v_cert_id;

    INSERT INTO cert.witness (certificate_id, kind, body, schema_version)
    VALUES (
        v_cert_id,
        'state_map',
        v_map,
        jsonb_build_object('nerode_schema_version', 1)
    );

    UPDATE nerode.morphisms SET cert_claim_id = v_claim_id WHERE id = v_morph_id;

    RETURN v_morph_id;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.register_morphism(BIGINT, BIGINT) IS
    'Find, classify, persist, and certify the DFA morphism from p_src to p_tgt.  '
    'Returns morphism id, or NULL if no morphism exists.';

-- ---------------------------------------------------------------------------
-- nerode.build_morphism_corpus()
--
-- For every row in nerode.product_pairs (where product_id IS NOT NULL):
--   register_morphism(product_id → lhs_corpus_automaton)
--   register_morphism(product_id → rhs_corpus_automaton)
-- Idempotent: register_morphism uses ON CONFLICT DO UPDATE.
-- Returns one summary row per registered morphism.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.build_morphism_corpus()
RETURNS TABLE (
    src_slug    TEXT,
    tgt_slug    TEXT,
    morphism_id BIGINT,
    kind        TEXT,
    domain_size INTEGER,
    image_size  INTEGER
) AS $$
DECLARE
    v_pair     nerode.product_pairs%ROWTYPE;
    v_lhs_aid  BIGINT;
    v_rhs_aid  BIGINT;
    v_mid      BIGINT;
    v_m        nerode.morphisms%ROWTYPE;
BEGIN
    FOR v_pair IN
        SELECT * FROM nerode.product_pairs WHERE product_id IS NOT NULL ORDER BY id
    LOOP
        SELECT automaton_id INTO v_lhs_aid
        FROM nerode.corpus WHERE slug = v_pair.lhs_slug;
        SELECT automaton_id INTO v_rhs_aid
        FROM nerode.corpus WHERE slug = v_pair.rhs_slug;

        IF v_lhs_aid IS NULL OR v_rhs_aid IS NULL THEN CONTINUE; END IF;

        -- product → lhs
        v_mid := nerode.register_morphism(v_pair.product_id, v_lhs_aid);
        IF v_mid IS NOT NULL THEN
            SELECT * INTO v_m FROM nerode.morphisms WHERE id = v_mid;
            src_slug    := v_pair.lhs_slug || '_x_' || v_pair.rhs_slug;
            tgt_slug    := v_pair.lhs_slug;
            morphism_id := v_mid;
            kind        := v_m.kind;
            SELECT count(*)          INTO domain_size FROM jsonb_object_keys(v_m.state_map);
            SELECT count(DISTINCT value) INTO image_size  FROM jsonb_each_text(v_m.state_map);
            RETURN NEXT;
        END IF;

        -- product → rhs
        v_mid := nerode.register_morphism(v_pair.product_id, v_rhs_aid);
        IF v_mid IS NOT NULL THEN
            SELECT * INTO v_m FROM nerode.morphisms WHERE id = v_mid;
            src_slug    := v_pair.lhs_slug || '_x_' || v_pair.rhs_slug;
            tgt_slug    := v_pair.rhs_slug;
            morphism_id := v_mid;
            kind        := v_m.kind;
            SELECT count(*)          INTO domain_size FROM jsonb_object_keys(v_m.state_map);
            SELECT count(DISTINCT value) INTO image_size  FROM jsonb_each_text(v_m.state_map);
            RETURN NEXT;
        END IF;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.build_morphism_corpus() IS
    'Seed nerode.morphisms with all product→factor epimorphisms from the corpus.  '
    'Idempotent.  Returns one row per registered morphism.';

-- ---------------------------------------------------------------------------
-- Bootstrap: seed all morphisms on first schema apply.
-- ---------------------------------------------------------------------------
DO $$ BEGIN PERFORM nerode.build_morphism_corpus(); END $$;
