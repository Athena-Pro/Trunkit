-- =============================================================================
--  nerode — Step 12: Validation Corpus
--
--  Named DFAs with composite state counts. Exercises:
--    • calx arithmetic bridge  — prime factorisation, composite identification
--    • certify_eigenform fast path — from_regex outputs are already minimal
--    • certify_prime_dfa        — is_prime=FALSE for all corpus entries
--
--  Corpus entries (period-n cycle DFAs over alphabet {a}):
--
--    slug      regex           |Q|   factorisation
--    --------  --------------  ----  -------------
--    cycle_4   (aaaa)*            4  2^2
--    cycle_6   (aaaaaa)*          6  2 * 3
--    cycle_9   (aaaaaaaaa)*       9  3^2
--    cycle_10  (aaaaaaaaaa)*     10  2 * 5
--
--  The language of (a^n)* is { w ∈ {a}* : |w| mod n = 0 }.
--  The unique minimal DFA has exactly n states: a length-mod-n counter.
--  All four values of n are composite, so calx factorisation is non-trivial.
--
--  Functions:
--    nerode.build_corpus()
--        Build automata for any corpus entries that lack one.
--        Idempotent: reuses existing automaton_id when already set.
--        Returns TABLE (slug, automaton_id, state_count).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- nerode.corpus — registry of named reference DFAs
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS nerode.corpus (
    id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug                 TEXT  UNIQUE NOT NULL,
    description          TEXT,
    regex                TEXT,
    expected_state_count INTEGER,
    automaton_id         BIGINT REFERENCES nerode.automata(id),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  nerode.corpus IS
    'Registered named automata. build_corpus() populates automaton_id on first call.';
COMMENT ON COLUMN nerode.corpus.slug IS
    'Stable short identifier; used as the automaton name in nerode.automata.';
COMMENT ON COLUMN nerode.corpus.automaton_id IS
    'Set by nerode.build_corpus(); NULL until the first build.';

-- ---------------------------------------------------------------------------
-- Seed corpus metadata (idempotent via ON CONFLICT DO NOTHING)
-- ---------------------------------------------------------------------------

INSERT INTO nerode.corpus (slug, description, regex, expected_state_count) VALUES
    ('cycle_4',
     'Strings over {a} whose length is divisible by 4. |Q|=4=2^2.',
     '(aaaa)*', 4),
    ('cycle_6',
     'Strings over {a} whose length is divisible by 6. |Q|=6=2*3.',
     '(aaaaaa)*', 6),
    ('cycle_9',
     'Strings over {a} whose length is divisible by 9. |Q|=9=3^2.',
     '(aaaaaaaaa)*', 9),
    ('cycle_10',
     'Strings over {a} whose length is divisible by 10. |Q|=10=2*5.',
     '(aaaaaaaaaa)*', 10)
ON CONFLICT (slug) DO NOTHING;

-- ---------------------------------------------------------------------------
-- nerode.build_corpus()
--
-- For each corpus entry without an automaton_id, call nerode.from_regex() and
-- store the result.  For entries that already have an automaton_id, reuse it.
-- Returns one summary row per corpus entry.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION nerode.build_corpus()
RETURNS TABLE (slug TEXT, automaton_id BIGINT, state_count INTEGER)
AS $$
DECLARE
    v_row  nerode.corpus%ROWTYPE;
    v_id   BIGINT;
    v_sc   INTEGER;
BEGIN
    FOR v_row IN
        SELECT * FROM nerode.corpus ORDER BY id
    LOOP
        IF v_row.automaton_id IS NULL THEN
            -- Build the DFA from the registered regex and store back
            SELECT nerode.from_regex(v_row.regex, v_row.slug)
            INTO   v_id;

            UPDATE nerode.corpus
            SET    automaton_id = v_id
            WHERE  id = v_row.id;
        ELSE
            v_id := v_row.automaton_id;
        END IF;

        SELECT a.state_count
        INTO   v_sc
        FROM   nerode.automata a
        WHERE  a.id = v_id;

        -- Assign named output columns then yield row
        slug         := v_row.slug;
        automaton_id := v_id;
        state_count  := v_sc;
        RETURN NEXT;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.build_corpus() IS
    'Build automata for all corpus entries that lack one. '
    'Idempotent: reuses existing automaton_id when already set. '
    'Returns (slug, automaton_id, state_count) per corpus entry.';

-- ---------------------------------------------------------------------------
-- Populate on schema apply (idempotent — build_corpus is a no-op when all
-- automaton_ids are already set)
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    PERFORM nerode.build_corpus();
END;
$$;
