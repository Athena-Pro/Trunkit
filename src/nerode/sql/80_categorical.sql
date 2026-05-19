-- =============================================================================
-- 80_categorical.sql
-- Categorical structure of the DFA category over nerode.
--
-- Key structures:
--   1. quotient_maps VIEW       -- epimorphisms that reduce state count
--   2. check_triangle_commutes  -- verify g∘f = h for three morphisms
--   3. product_universal_property -- verify the categorical product property
--   4. calx_functor_report      -- state-count functor: |src| ≥ |tgt| for epis
--   5. categorical_profile      -- full categorical view of one automaton
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. View: quotient_maps
--
-- An epimorphism f: A → B is a *quotient map* when |B| < |A|.
-- In DFA theory this is the Myhill-Nerode quotient: B = Min(A).
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW nerode.quotient_maps AS
SELECT
    m.id            AS morphism_id,
    m.src_id,
    m.tgt_id,
    src_a.state_count AS src_states,
    tgt_a.state_count AS tgt_states,
    src_a.state_count - tgt_a.state_count AS states_collapsed,
    m.kind,
    m.cert_claim_id
FROM nerode.morphisms m
JOIN nerode.automata src_a ON src_a.id = m.src_id
JOIN nerode.automata tgt_a ON tgt_a.id = m.tgt_id
WHERE m.kind = 'epimorphism'
  AND tgt_a.state_count < src_a.state_count;


-- ---------------------------------------------------------------------------
-- 2. check_triangle_commutes(f, g, h) → BOOLEAN
--
-- Given morphism IDs:
--   f : A → B   (p_f)
--   g : B → C   (p_g)
--   h : A → C   (p_h, claimed = g∘f)
--
-- Returns TRUE iff the triangle commutes: g(f(a)) = h(a) for all a ∈ Q_A.
-- Returns NULL if any morphism is missing.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.check_triangle_commutes(
    p_f BIGINT,
    p_g BIGINT,
    p_h BIGINT
) RETURNS BOOLEAN
LANGUAGE plpgsql STABLE AS $$
DECLARE
    f_map    JSONB;
    g_map    JSONB;
    h_map    JSONB;
    key      TEXT;
    via_g    TEXT;
    via_h    TEXT;
BEGIN
    SELECT state_map INTO f_map FROM nerode.morphisms WHERE id = p_f;
    SELECT state_map INTO g_map FROM nerode.morphisms WHERE id = p_g;
    SELECT state_map INTO h_map FROM nerode.morphisms WHERE id = p_h;

    IF f_map IS NULL OR g_map IS NULL OR h_map IS NULL THEN
        RETURN NULL;  -- morphism not found
    END IF;

    FOR key IN SELECT jsonb_object_keys(f_map) LOOP
        -- g(f(key))
        via_g := g_map->>(f_map->>key);
        -- h(key) directly
        via_h := h_map->>key;
        IF via_g IS DISTINCT FROM via_h THEN
            RETURN FALSE;
        END IF;
    END LOOP;

    RETURN TRUE;
END;
$$;


-- ---------------------------------------------------------------------------
-- 3. product_universal_property(product, lhs, rhs, witness) → JSONB
--
-- Verifies the universal property of a categorical product:
--
--       W ---------> A
--        \          ^
--         \  h    /  π₁
--          v    /
--          P ------> B
--               π₂
--
-- Given:
--   p_product  : automaton ID of P = A × B
--   p_lhs      : automaton ID of A
--   p_rhs      : automaton ID of B
--   p_witness  : automaton ID of W (with morphisms to A and B already registered)
--
-- Steps:
--   1. Find π₁: P → A and π₂: P → B from nerode.morphisms.
--   2. Find f: W → A and g: W → B from nerode.morphisms.
--   3. Build an inverse index: (a_state, b_state) → product_state
--      using the projection maps (each product state maps uniquely to one pair).
--   4. For each w ∈ W, compute h(w) = p such that π₁(p) = f(w) ∧ π₂(p) = g(w).
--   5. Verify the two triangles commute (by construction, always true when h exists).
--
-- Returns a JSONB object with keys:
--   universal_holds, product_id, lhs_id, rhs_id, witness_id,
--   pi1_morphism_id, pi2_morphism_id, f_morphism_id, g_morphism_id,
--   mediating_map, error (if any)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.product_universal_property(
    p_product BIGINT,
    p_lhs     BIGINT,
    p_rhs     BIGINT,
    p_witness BIGINT
) RETURNS JSONB
LANGUAGE plpgsql STABLE AS $$
DECLARE
    pi1_id   BIGINT;  pi1_map JSONB;
    pi2_id   BIGINT;  pi2_map JSONB;
    f_id     BIGINT;  f_map   JSONB;
    g_id     BIGINT;  g_map   JSONB;
    -- Inverse: "a_state::text || '_' || b_state::text" → product_state::text
    pair_idx JSONB := '{}';
    h_map    JSONB := '{}';
    p_key    TEXT;
    a_val    TEXT;
    b_val    TEXT;
    pair_key TEXT;
    w_key    TEXT;
    p_state  TEXT;
BEGIN
    -- 1. Find projection morphisms P→A, P→B
    SELECT id, state_map INTO pi1_id, pi1_map
    FROM nerode.morphisms
    WHERE src_id = p_product AND tgt_id = p_lhs
    LIMIT 1;

    SELECT id, state_map INTO pi2_id, pi2_map
    FROM nerode.morphisms
    WHERE src_id = p_product AND tgt_id = p_rhs
    LIMIT 1;

    IF pi1_map IS NULL THEN
        RETURN jsonb_build_object('error', format('no π₁ morphism from product(%s) to lhs(%s)', p_product, p_lhs));
    END IF;
    IF pi2_map IS NULL THEN
        RETURN jsonb_build_object('error', format('no π₂ morphism from product(%s) to rhs(%s)', p_product, p_rhs));
    END IF;

    -- 2. Find witness morphisms W→A, W→B
    SELECT id, state_map INTO f_id, f_map
    FROM nerode.morphisms
    WHERE src_id = p_witness AND tgt_id = p_lhs
    LIMIT 1;

    SELECT id, state_map INTO g_id, g_map
    FROM nerode.morphisms
    WHERE src_id = p_witness AND tgt_id = p_rhs
    LIMIT 1;

    IF f_map IS NULL THEN
        RETURN jsonb_build_object('error', format('no morphism from witness(%s) to lhs(%s)', p_witness, p_lhs));
    END IF;
    IF g_map IS NULL THEN
        RETURN jsonb_build_object('error', format('no morphism from witness(%s) to rhs(%s)', p_witness, p_rhs));
    END IF;

    -- 3. Build inverse index from projection maps: pair_key → product_state
    FOR p_key IN SELECT jsonb_object_keys(pi1_map) LOOP
        a_val    := pi1_map->>p_key;
        b_val    := pi2_map->>p_key;
        pair_key := a_val || '_' || b_val;
        pair_idx := pair_idx || jsonb_build_object(pair_key, p_key);
    END LOOP;

    -- 4. Build mediating map h: W → P
    FOR w_key IN SELECT jsonb_object_keys(f_map) LOOP
        a_val    := f_map->>w_key;
        b_val    := g_map->>w_key;
        pair_key := a_val || '_' || b_val;
        p_state  := pair_idx->>pair_key;

        IF p_state IS NULL THEN
            RETURN jsonb_build_object(
                'universal_holds', FALSE,
                'error', format(
                    'no product state for pair (%s, %s) needed by witness state %s',
                    a_val, b_val, w_key
                )
            );
        END IF;

        h_map := h_map || jsonb_build_object(w_key, p_state::BIGINT);
    END LOOP;

    -- 5. Both triangles commute by construction (h defined via inverse of π₁,π₂).
    RETURN jsonb_build_object(
        'universal_holds',   TRUE,
        'product_id',        p_product,
        'lhs_id',            p_lhs,
        'rhs_id',            p_rhs,
        'witness_id',        p_witness,
        'pi1_morphism_id',   pi1_id,
        'pi2_morphism_id',   pi2_id,
        'f_morphism_id',     f_id,
        'g_morphism_id',     g_id,
        'mediating_map_size', (SELECT count(*) FROM jsonb_each(h_map))::INT,
        'mediating_map',     h_map
    );
END;
$$;


-- ---------------------------------------------------------------------------
-- 4. calx_functor_report() → TABLE
--
-- The state-count map  |·|: DFA → ℕ  is a functor: morphisms preserve the
-- ordering and divisibility structure on state counts.
--
-- For each registered morphism we report:
--   - src/tgt state counts
--   - state_ratio  = |src| / |tgt|  (should be ≥ 1 for epimorphisms)
--   - tgt_divides_src = (|src| mod |tgt| = 0)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.calx_functor_report()
RETURNS TABLE (
    morphism_id      BIGINT,
    kind             TEXT,
    src_id           BIGINT,
    src_states       INTEGER,
    tgt_id           BIGINT,
    tgt_states       INTEGER,
    state_ratio      NUMERIC,
    tgt_divides_src  BOOLEAN,
    cert_claim_id    BIGINT
)
LANGUAGE sql STABLE AS $$
    SELECT
        m.id,
        m.kind,
        m.src_id,
        src_a.state_count,
        m.tgt_id,
        tgt_a.state_count,
        ROUND(src_a.state_count::NUMERIC / NULLIF(tgt_a.state_count, 0), 4),
        src_a.state_count % NULLIF(tgt_a.state_count, 0) = 0,
        m.cert_claim_id
    FROM nerode.morphisms m
    JOIN nerode.automata src_a ON src_a.id = m.src_id
    JOIN nerode.automata tgt_a ON tgt_a.id = m.tgt_id
    ORDER BY m.id;
$$;


-- ---------------------------------------------------------------------------
-- 5. categorical_profile(automaton_id) → JSONB
--
-- Full categorical view of one automaton:
--   - state count + calx arithmetic facts
--   - eigenform status (is_minimal = is fixed point of Min functor)
--   - all outgoing morphisms (what this automaton maps into)
--   - all incoming morphisms (what maps into this automaton)
--
-- The eigenform certificate witnesses that Min(A) ≅ A — the automaton is
-- its own fixed point under the minimization endofunctor.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.categorical_profile(p_automaton_id BIGINT)
RETURNS JSONB
LANGUAGE plpgsql STABLE AS $$
DECLARE
    a_row       nerode.automata%ROWTYPE;
    eigen_min   BOOLEAN;
    eigen_claim BIGINT;
    morph_out   JSONB;
    morph_in    JSONB;
    calx_j      JSONB;
BEGIN
    SELECT * INTO a_row FROM nerode.automata WHERE id = p_automaton_id;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('error', 'automaton not found', 'id', p_automaton_id);
    END IF;

    -- Eigenform: is this automaton already minimal?
    SELECT ef.is_minimal, ef.claim_id
    INTO   eigen_min, eigen_claim
    FROM   nerode.certify_eigenform(p_automaton_id) ef
    LIMIT  1;

    -- Outgoing morphisms: what can this automaton map into?
    SELECT jsonb_agg(
        jsonb_build_object(
            'morphism_id', m.id,
            'tgt_id',      m.tgt_id,
            'tgt_states',  tgt_a.state_count,
            'kind',        m.kind,
            'cert_claim',  m.cert_claim_id
        ) ORDER BY m.id
    )
    INTO morph_out
    FROM nerode.morphisms m
    JOIN nerode.automata tgt_a ON tgt_a.id = m.tgt_id
    WHERE m.src_id = p_automaton_id;

    -- Incoming morphisms: what maps into this automaton?
    SELECT jsonb_agg(
        jsonb_build_object(
            'morphism_id', m.id,
            'src_id',      m.src_id,
            'src_states',  src_a.state_count,
            'kind',        m.kind,
            'cert_claim',  m.cert_claim_id
        ) ORDER BY m.id
    )
    INTO morph_in
    FROM nerode.morphisms m
    JOIN nerode.automata src_a ON src_a.id = m.src_id
    WHERE m.tgt_id = p_automaton_id;

    -- Calx arithmetic annotation
    SELECT nerode.calx_state_facts(p_automaton_id)
    INTO calx_j;

    RETURN jsonb_build_object(
        'automaton_id',          p_automaton_id,
        'state_count',           a_row.state_count,
        'is_eigenform',          COALESCE(eigen_min, FALSE),
        'eigenform_cert_claim',  eigen_claim,
        'calx',                  calx_j,
        'morphisms_out',         COALESCE(morph_out, '[]'::JSONB),
        'morphisms_out_count',   COALESCE(jsonb_array_length(morph_out), 0),
        'morphisms_in',          COALESCE(morph_in,  '[]'::JSONB),
        'morphisms_in_count',    COALESCE(jsonb_array_length(morph_in),  0),
        -- Categorical interpretation summary
        'categorical_role',      CASE
            WHEN COALESCE(eigen_min, FALSE)
                 AND COALESCE(jsonb_array_length(morph_in), 0) > 0
                THEN 'terminal_object_candidate'
            WHEN COALESCE(eigen_min, FALSE)
                THEN 'eigenform'
            WHEN COALESCE(jsonb_array_length(morph_in), 0) > 0
                THEN 'quotient_target'
            WHEN COALESCE(jsonb_array_length(morph_out), 0) > 0
                THEN 'source_object'
            ELSE 'isolated'
        END
    );
END;
$$;
