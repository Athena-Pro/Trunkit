-- =============================================================================
--  nerode — Step B0: Schema-anchored ledger (Porter policy gate)  [Phase 5]
--
--  The Absorb/Render store of LedgerAgent (arXiv:2606.20529): successful tool
--  returns are projected into a compact typed dictionary keyed by canonical
--  paths, so the next model reads current task state by lookup instead of
--  re-scanning the transcript. This is the typed sibling of Porter's
--  sequence_cache; it is keyed per session and per canonical path.
--
--  Depends on: nerode core (00/01). Idempotent.
--  NOTE: starter file — not yet executed against a live database.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- nerode.ledger_state — the schema-anchored typed dictionary
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nerode.ledger_state (
    session_id   TEXT        NOT NULL,
    path         TEXT        NOT NULL,        -- canonical path, e.g. 'reservation.UX789'
    value        JSONB       NOT NULL,        -- typed value projected from a tool return
    schema_type  TEXT,                        -- optional type tag ('reservation','order',…)
    source_event BIGINT,                      -- nerode.session_log.id provenance (optional)
    observed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, path)
);

COMMENT ON TABLE nerode.ledger_state IS
    'LedgerAgent schema-anchored ledger (arXiv:2606.20529): per-session typed '
    'dictionary of observed task state, keyed by canonical path.';

CREATE INDEX IF NOT EXISTS idx_ledger_state_session
    ON nerode.ledger_state (session_id);

-- ---------------------------------------------------------------------------
-- nerode.ledger_absorb(session, path, value, type, source_event)
--   Absorb(L, m): project a successful tool return into typed state. Latest
--   successful read for a path wins (idempotent upsert).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.ledger_absorb(
    p_session_id   TEXT,
    p_path         TEXT,
    p_value        JSONB,
    p_schema_type  TEXT   DEFAULT NULL,
    p_source_event BIGINT DEFAULT NULL)
RETURNS VOID
LANGUAGE sql AS $$
    INSERT INTO nerode.ledger_state (session_id, path, value, schema_type, source_event, observed_at)
    VALUES (p_session_id, p_path, p_value, p_schema_type, p_source_event, now())
    ON CONFLICT (session_id, path) DO UPDATE
        SET value        = EXCLUDED.value,
            schema_type  = COALESCE(EXCLUDED.schema_type, nerode.ledger_state.schema_type),
            source_event = COALESCE(EXCLUDED.source_event, nerode.ledger_state.source_event),
            observed_at  = now();
$$;

COMMENT ON FUNCTION nerode.ledger_absorb(TEXT, TEXT, JSONB, TEXT, BIGINT) IS
    'Absorb a successful tool return into the session ledger (latest wins).';

-- ---------------------------------------------------------------------------
-- nerode.ledger_get(session, path) → JSONB   single lookup (NULL if absent)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.ledger_get(p_session_id TEXT, p_path TEXT)
RETURNS JSONB
LANGUAGE sql STABLE AS $$
    SELECT value FROM nerode.ledger_state
    WHERE session_id = p_session_id AND path = p_path;
$$;

-- ---------------------------------------------------------------------------
-- nerode.ledger_render(session) → JSONB   the compact dict re-injected per turn
--   { path : value, ... }   (Render(L))
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.ledger_render(p_session_id TEXT)
RETURNS JSONB
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(jsonb_object_agg(path, value ORDER BY path), '{}'::jsonb)
    FROM nerode.ledger_state
    WHERE session_id = p_session_id;
$$;

COMMENT ON FUNCTION nerode.ledger_render(TEXT) IS
    'Render(L): the compact typed dictionary {path: value} for re-injection into '
    'the prompt — current task state by lookup, no transcript re-scan.';

-- ---------------------------------------------------------------------------
-- nerode.ledger_hash(session) → TEXT   md5 of the rendered ledger (drift anchor)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.ledger_hash(p_session_id TEXT)
RETURNS TEXT
LANGUAGE sql STABLE AS $$
    SELECT md5(nerode.ledger_render(p_session_id)::text);
$$;
