-- =============================================================================
--  Tier 3 — sequence composition (compose_index) and OEIS matching
--
--  Multiset semantics: composition_membership is keyed by (composite_id, idx)
--  so duplicate values (e.g. p_1, p_1 from Fibonacci indices) are preserved.
--
--  Single-pass: composition_runs.catalog_seq_ids freezes the Tier-1 catalog.
-- =============================================================================

CREATE TABLE IF NOT EXISTS composition_runs (
    run_id           BIGSERIAL PRIMARY KEY,
    catalog_frozen_at TIMESTAMPTZ DEFAULT now(),
    catalog_seq_ids  TEXT[]    NOT NULL,
    compose_kind     TEXT      NOT NULL DEFAULT 'compose_index',
    notes            TEXT
);

COMMENT ON TABLE composition_runs IS
    'One Tier-3 pass. catalog_seq_ids is the frozen sequence catalog; no closure iteration.';


CREATE TABLE IF NOT EXISTS sequence_compositions (
    composite_id   TEXT    PRIMARY KEY,
    run_id         BIGINT  NOT NULL REFERENCES composition_runs(run_id) ON DELETE CASCADE,
    compose_kind   TEXT    NOT NULL DEFAULT 'compose_index',
    base_seq_id    TEXT    NOT NULL,
    selector_kind  TEXT    NOT NULL,   -- ''sequence'' | ''orbit''
    selector_ref   TEXT    NOT NULL,   -- seq_id or orbit_id::text
    selector_start BIGINT,             -- orbit start_n or NULL for static C
    compose_depth  INTEGER NOT NULL DEFAULT 1,
    formula        TEXT,
    created_at     TIMESTAMPTZ DEFAULT now(),
    CHECK (selector_kind IN ('sequence', 'orbit'))
);

CREATE INDEX IF NOT EXISTS idx_compositions_run ON sequence_compositions(run_id);
CREATE INDEX IF NOT EXISTS idx_compositions_base ON sequence_compositions(base_seq_id);
CREATE INDEX IF NOT EXISTS idx_compositions_selector ON sequence_compositions(selector_kind, selector_ref);

COMMENT ON TABLE sequence_compositions IS
    'compose_index(B,C): a_k = B[C_k]. C values are indices into B (from static seq or orbit trace).';


CREATE TABLE IF NOT EXISTS composition_membership (
    composite_id TEXT    NOT NULL REFERENCES sequence_compositions(composite_id) ON DELETE CASCADE,
    idx          INTEGER NOT NULL,     -- 1-based position in composed stream (multiset)
    n            BIGINT  NOT NULL,     -- a_idx = B[C_idx]
    PRIMARY KEY (composite_id, idx)
);

CREATE INDEX IF NOT EXISTS idx_composition_membership_n
    ON composition_membership(n, composite_id);

COMMENT ON TABLE composition_membership IS
    'Multiset output of a composition. (composite_id, idx) is the discriminator, not n.';


CREATE TABLE IF NOT EXISTS oeis_compose_candidates (
    composite_id  TEXT    NOT NULL REFERENCES sequence_compositions(composite_id) ON DELETE CASCADE,
    candidate_id  INTEGER NOT NULL,
    oeis_id       TEXT,
    oeis_name     TEXT    NOT NULL DEFAULT '',
    prefix_len    INTEGER NOT NULL,
    confidence    DOUBLE PRECISION NOT NULL DEFAULT 0,
    raw_payload   JSONB,
    fetched_at    TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (composite_id, candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_oeis_compose_oeis_id ON oeis_compose_candidates(oeis_id);
CREATE INDEX IF NOT EXISTS idx_oeis_compose_prefix_hash
    ON oeis_compose_candidates ((raw_payload->>'prefix_hash'));

COMMENT ON TABLE oeis_compose_candidates IS
    'OEIS search hits for composed stream prefixes. Reuses Tier-2 scoring in raw_payload.scoring.';
