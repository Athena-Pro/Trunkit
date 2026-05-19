-- =============================================================================
--  nerode — Step 01: Core Schema
--  Tables: alphabets, automata, states, transitions, construction_log
--  Idempotent — all CREATE ... IF NOT EXISTS.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- nerode.alphabets — named symbol sets (Σ)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS nerode.alphabets (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name       TEXT UNIQUE NOT NULL,
    symbols    TEXT[]      NOT NULL,   -- ordered array of single-character symbols
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  nerode.alphabets         IS 'Named finite alphabets Σ used by automata.';
COMMENT ON COLUMN nerode.alphabets.symbols IS 'Ordered array of distinct symbols; each element is a single character.';

-- ---------------------------------------------------------------------------
-- nerode.automata — automaton metadata
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS nerode.automata (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name         TEXT,
    type         TEXT        NOT NULL CHECK (type IN ('DFA', 'NFA', 'NFA_E', 'PDA')),
    alphabet_id  BIGINT      NOT NULL REFERENCES nerode.alphabets(id),
    state_count  INTEGER     NOT NULL DEFAULT 0,
    certified    BOOLEAN     NOT NULL DEFAULT FALSE,
    cert_claim_id BIGINT,                    -- REFERENCES cert.claim(id) after schema is linked
    source_regex TEXT,                        -- if built from a regex
    provenance   JSONB       NOT NULL DEFAULT '{}',   -- construction record
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  nerode.automata             IS 'Automaton registry. type=DFA/NFA/NFA_E (ε-NFA)/PDA.';
COMMENT ON COLUMN nerode.automata.certified   IS 'TRUE once a cert.certificate has been issued for this automaton.';
COMMENT ON COLUMN nerode.automata.provenance  IS 'Construction record: operation, inputs, parameters.';

CREATE INDEX IF NOT EXISTS idx_nerode_automata_alphabet
    ON nerode.automata (alphabet_id);

-- ---------------------------------------------------------------------------
-- nerode.states — states of each automaton
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS nerode.states (
    automaton_id BIGINT  NOT NULL REFERENCES nerode.automata(id) ON DELETE CASCADE,
    state_id     INTEGER NOT NULL,
    label        TEXT,
    is_initial   BOOLEAN NOT NULL DEFAULT FALSE,
    is_accepting BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (automaton_id, state_id)
);

COMMENT ON TABLE  nerode.states             IS 'States belonging to an automaton.';
COMMENT ON COLUMN nerode.states.state_id    IS 'Local integer identifier for the state within this automaton.';
COMMENT ON COLUMN nerode.states.is_initial  IS 'Exactly one state per automaton should have is_initial=TRUE.';

CREATE INDEX IF NOT EXISTS idx_nerode_states_accepting
    ON nerode.states (automaton_id, is_accepting);

-- ---------------------------------------------------------------------------
-- nerode.transitions — transition function δ
-- For DFA/NFA: symbol TEXT NOT NULL.
-- For NFA_E:   symbol IS NULL encodes an ε-transition.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS nerode.transitions (
    id           BIGINT  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    automaton_id BIGINT  NOT NULL REFERENCES nerode.automata(id) ON DELETE CASCADE,
    from_state   INTEGER NOT NULL,
    symbol       TEXT,                -- NULL ⇒ ε-transition
    to_state     INTEGER NOT NULL,
    FOREIGN KEY (automaton_id, from_state) REFERENCES nerode.states(automaton_id, state_id),
    FOREIGN KEY (automaton_id, to_state)   REFERENCES nerode.states(automaton_id, state_id)
);

COMMENT ON TABLE  nerode.transitions        IS 'Transition relation δ ⊆ Q × (Σ ∪ {ε}) × Q.';
COMMENT ON COLUMN nerode.transitions.symbol IS 'Transition label. NULL encodes an ε-transition (NFA_E only).';

-- Unique non-epsilon transitions (DFA enforces at most one per (from, symbol))
CREATE UNIQUE INDEX IF NOT EXISTS uniq_symbol_transition
    ON nerode.transitions (automaton_id, from_state, symbol, to_state)
    WHERE symbol IS NOT NULL;

-- Unique epsilon transitions
CREATE UNIQUE INDEX IF NOT EXISTS uniq_epsilon_transition
    ON nerode.transitions (automaton_id, from_state, to_state)
    WHERE symbol IS NULL;

-- Fast lookup: all transitions out of a state on a given symbol
CREATE INDEX IF NOT EXISTS idx_nerode_trans_from
    ON nerode.transitions (automaton_id, from_state, symbol);

-- Fast lookup: reverse (used in Hopcroft)
CREATE INDEX IF NOT EXISTS idx_nerode_trans_to
    ON nerode.transitions (automaton_id, to_state, symbol);

-- ---------------------------------------------------------------------------
-- nerode.construction_log — append-only operation log
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS nerode.construction_log (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    automaton_id BIGINT      NOT NULL REFERENCES nerode.automata(id),
    operation    TEXT        NOT NULL,  -- from_regex|minimize|product|complement|equivalent|run
    inputs       JSONB       NOT NULL DEFAULT '{}',
    result       JSONB       NOT NULL DEFAULT '{}',
    cert_cert_id BIGINT,               -- REFERENCES cert.certificate(id) once issued
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_nerode_clog_automaton
    ON nerode.construction_log (automaton_id);

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------

-- Return the alphabet symbols array for an automaton.
CREATE OR REPLACE FUNCTION nerode.alphabet_of(p_automaton_id BIGINT)
RETURNS TEXT[] AS $$
    SELECT a.symbols
    FROM nerode.automata au
    JOIN nerode.alphabets a ON a.id = au.alphabet_id
    WHERE au.id = p_automaton_id;
$$ LANGUAGE sql STABLE;

-- Complete a DFA: for every (state, symbol) pair without a transition, add one
-- pointing to a new sink state (dead state). Returns the sink state_id, or -1
-- if the automaton was already complete.
CREATE OR REPLACE FUNCTION nerode.complete_dfa(p_automaton_id BIGINT)
RETURNS INTEGER AS $$
DECLARE
    v_sink    INTEGER;
    v_symbols TEXT[];
    v_sym     TEXT;
    v_missing INTEGER;
BEGIN
    SELECT symbols INTO v_symbols
    FROM nerode.alphabets a
    JOIN nerode.automata au ON au.alphabet_id = a.id
    WHERE au.id = p_automaton_id;

    -- Count missing transitions
    SELECT count(*) INTO v_missing
    FROM nerode.states s
    CROSS JOIN unnest(v_symbols) AS sym(symbol)
    WHERE s.automaton_id = p_automaton_id
      AND NOT EXISTS (
          SELECT 1 FROM nerode.transitions t
          WHERE t.automaton_id = p_automaton_id
            AND t.from_state = s.state_id
            AND t.symbol = sym.symbol
      );

    IF v_missing = 0 THEN
        RETURN -1;  -- already complete
    END IF;

    -- Assign sink state_id = max existing + 1
    SELECT COALESCE(max(state_id), -1) + 1 INTO v_sink
    FROM nerode.states WHERE automaton_id = p_automaton_id;

    INSERT INTO nerode.states (automaton_id, state_id, label, is_initial, is_accepting)
    VALUES (p_automaton_id, v_sink, 'sink', FALSE, FALSE);

    -- Self-loops on sink for all symbols
    FOREACH v_sym IN ARRAY v_symbols LOOP
        INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
        VALUES (p_automaton_id, v_sink, v_sym, v_sink)
        ON CONFLICT DO NOTHING;
    END LOOP;

    -- Fill missing transitions → sink
    INSERT INTO nerode.transitions (automaton_id, from_state, symbol, to_state)
    SELECT p_automaton_id, s.state_id, sym.symbol, v_sink
    FROM nerode.states s
    CROSS JOIN unnest(v_symbols) AS sym(symbol)
    WHERE s.automaton_id = p_automaton_id
      AND s.state_id != v_sink
      AND NOT EXISTS (
          SELECT 1 FROM nerode.transitions t
          WHERE t.automaton_id = p_automaton_id
            AND t.from_state = s.state_id
            AND t.symbol = sym.symbol
      );

    -- Update state count
    UPDATE nerode.automata
    SET state_count = state_count + 1
    WHERE id = p_automaton_id;

    RETURN v_sink;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION nerode.complete_dfa(BIGINT) IS
    'Make a DFA total by adding a sink/dead state and filling missing transitions. '
    'Returns the sink state_id, or -1 if already complete. Idempotent.';

-- Export a stored automaton as a self-contained JSONB document (CLI/tool use).
CREATE OR REPLACE FUNCTION nerode.export_json(p_automaton_id BIGINT)
RETURNS JSONB AS $$
SELECT jsonb_build_object(
    'nerode_version', 1,
    'id',         au.id,
    'name',       au.name,
    'type',       au.type,
    'alphabet',   a.symbols,
    'certified',  au.certified,
    'source_regex', au.source_regex,
    'states', (
        SELECT jsonb_agg(jsonb_build_object(
            'id',          s.state_id,
            'label',       s.label,
            'is_initial',  s.is_initial,
            'is_accepting',s.is_accepting
        ) ORDER BY s.state_id)
        FROM nerode.states s WHERE s.automaton_id = au.id
    ),
    'transitions', (
        SELECT jsonb_agg(jsonb_build_object(
            'from',   t.from_state,
            'symbol', t.symbol,
            'to',     t.to_state
        ) ORDER BY t.from_state, t.symbol, t.to_state)
        FROM nerode.transitions t WHERE t.automaton_id = au.id
    ),
    'provenance', au.provenance
)
FROM nerode.automata au
JOIN nerode.alphabets a ON a.id = au.alphabet_id
WHERE au.id = p_automaton_id;
$$ LANGUAGE sql STABLE;

COMMENT ON FUNCTION nerode.export_json(BIGINT) IS
    'Export a stored automaton as self-contained JSONB. Compatible with the CLI JSON format.';
