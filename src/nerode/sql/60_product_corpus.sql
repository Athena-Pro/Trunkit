-- =============================================================================
--  nerode — Step 13: Product Corpus
--
--  For every pair of corpus DFAs, compute their intersection product DFA
--  and record:
--
--    state_bound  = |Q(M1)| × |Q(M2)|    (naive BFS worst-case)
--    actual_count = reachable states built by nerode.product()  (BFS actual)
--
--  For cycle languages (a^m)* over {a}:
--
--    actual_count = lcm(|Q(M1)|, |Q(M2)|)
--    state_bound  = |Q(M1)| × |Q(M2)|
--    bound is tight  ⟺  gcd(|Q(M1)|, |Q(M2)|) = 1  (coprime pair)
--
--  Pairs registered:
--
--    lhs_slug    rhs_slug     |Q1|  |Q2|  bound  actual  gcd=1?
--    ----------  ----------    ----  ----  -----  ------  ------
--    cycle_4     cycle_6          4     6     24      12    No
--    cycle_4     cycle_9          4     9     36      36   Yes  (coprime → tight)
--    cycle_4     cycle_10         4    10     40      20    No
--    cycle_6     cycle_9          6     9     54      18    No
--    cycle_6     cycle_10         6    10     60      30    No
--
--  Functions:
--    nerode.build_product_corpus()
--        Compute product DFA for any pair that has not yet been built.
--        Idempotent: reuses existing product_id when already set.
--        Returns TABLE (lhs_slug, rhs_slug, product_id, state_bound, actual_count).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- nerode.product_pairs — registry of corpus × corpus intersection results
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS nerode.product_pairs (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lhs_slug      TEXT NOT NULL,
    rhs_slug      TEXT NOT NULL,
    product_id    BIGINT REFERENCES nerode.automata(id),
    state_bound   INTEGER,   -- |Q(M1)| * |Q(M2)|  (filled on first build)
    actual_count  INTEGER,   -- reachable states in product DFA
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (lhs_slug, rhs_slug)
);

COMMENT ON TABLE  nerode.product_pairs IS
    'Pairwise intersection products of corpus DFAs. build_product_corpus() populates product_id.';
COMMENT ON COLUMN nerode.product_pairs.state_bound IS
    '|Q(M1)| * |Q(M2)|: the naive upper bound on product-DFA size.';
COMMENT ON COLUMN nerode.product_pairs.actual_count IS
    'Reachable states actually built by nerode.product() (BFS from the initial pair-state).';

-- ---------------------------------------------------------------------------
-- Seed pairs (idempotent via ON CONFLICT DO NOTHING)
-- ---------------------------------------------------------------------------

INSERT INTO nerode.product_pairs (lhs_slug, rhs_slug) VALUES
    ('cycle_4',  'cycle_6'),
    ('cycle_4',  'cycle_9'),
    ('cycle_4',  'cycle_10'),
    ('cycle_6',  'cycle_9'),
    ('cycle_6',  'cycle_10')
ON CONFLICT (lhs_slug, rhs_slug) DO NOTHING;

-- ---------------------------------------------------------------------------
-- nerode.build_product_corpus()
--
-- For each registered pair that lacks a product_id:
--   1. Look up automaton IDs from nerode.corpus.
--   2. Call nerode.product(lhs_id, rhs_id, 'intersection').
--   3. Record state_bound and actual_count in nerode.product_pairs.
--
-- For pairs that already have a product_id, the existing values are returned
-- unchanged (idempotent).
--
-- Returns one summary row per registered pair.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.build_product_corpus()
RETURNS TABLE (
    lhs_slug     TEXT,
    rhs_slug     TEXT,
    product_id   BIGINT,
    state_bound  INTEGER,
    actual_count INTEGER
) AS $$
DECLARE
    v_row     nerode.product_pairs%ROWTYPE;
    v_lhs_id  BIGINT;
    v_rhs_id  BIGINT;
    v_lhs_sc  INTEGER;
    v_rhs_sc  INTEGER;
    v_pid     BIGINT;
    v_ac      INTEGER;
BEGIN
    FOR v_row IN
        SELECT * FROM nerode.product_pairs ORDER BY id
    LOOP
        -- Resolve corpus automaton IDs and their current state counts
        SELECT c.automaton_id, a.state_count
        INTO   v_lhs_id, v_lhs_sc
        FROM   nerode.corpus c
        JOIN   nerode.automata a ON a.id = c.automaton_id
        WHERE  c.slug = v_row.lhs_slug;

        IF NOT FOUND OR v_lhs_id IS NULL THEN
            RAISE EXCEPTION
                'nerode.build_product_corpus: corpus entry ''%'' has no automaton_id — '
                'run nerode.build_corpus() first', v_row.lhs_slug;
        END IF;

        SELECT c.automaton_id, a.state_count
        INTO   v_rhs_id, v_rhs_sc
        FROM   nerode.corpus c
        JOIN   nerode.automata a ON a.id = c.automaton_id
        WHERE  c.slug = v_row.rhs_slug;

        IF NOT FOUND OR v_rhs_id IS NULL THEN
            RAISE EXCEPTION
                'nerode.build_product_corpus: corpus entry ''%'' has no automaton_id — '
                'run nerode.build_corpus() first', v_row.rhs_slug;
        END IF;

        IF v_row.product_id IS NULL THEN
            -- Build the intersection product DFA (BFS over reachable pair-states)
            SELECT nerode.product(v_lhs_id, v_rhs_id, 'intersection')
            INTO   v_pid;

            -- Record actual reachable state count
            SELECT a.state_count INTO v_ac
            FROM   nerode.automata a
            WHERE  a.id = v_pid;

            UPDATE nerode.product_pairs
            SET    product_id   = v_pid,
                   state_bound  = v_lhs_sc * v_rhs_sc,
                   actual_count = v_ac
            WHERE  id = v_row.id;
        ELSE
            -- Already built — reuse stored values
            v_pid := v_row.product_id;
            v_ac  := v_row.actual_count;
        END IF;

        -- Yield summary row
        lhs_slug     := v_row.lhs_slug;
        rhs_slug     := v_row.rhs_slug;
        product_id   := v_pid;
        state_bound  := v_lhs_sc * v_rhs_sc;   -- always fresh from corpus
        actual_count := v_ac;
        RETURN NEXT;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.build_product_corpus() IS
    'Build intersection product DFAs for all registered corpus pairs. '
    'Idempotent: skips pairs that already have a product_id. '
    'Returns (lhs_slug, rhs_slug, product_id, state_bound, actual_count) per pair.';

-- ---------------------------------------------------------------------------
-- Populate on schema apply (idempotent — build_product_corpus is a no-op
-- when all pairs already have a product_id set)
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    PERFORM nerode.build_product_corpus();
END
$$;
