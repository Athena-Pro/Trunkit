-- =============================================================================
--  nerode — Step 00: Bootstrap
--  Creates the nerode schema and registers cert methods.
--  Safe to run against a fresh DB or a Trunkit-shared DB.
--  Idempotent: every statement uses CREATE ... IF NOT EXISTS or ON CONFLICT DO NOTHING.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS nerode;

-- ---------------------------------------------------------------------------
-- Minimal cert stub (no-op when Trunkit's full cert schema is already present)
-- ---------------------------------------------------------------------------

CREATE SCHEMA IF NOT EXISTS cert;

CREATE TABLE IF NOT EXISTS cert.method (
    name         TEXT PRIMARY KEY,
    claim_kind   TEXT NOT NULL,
    checker_kind TEXT NOT NULL,
    description  TEXT
);

CREATE TABLE IF NOT EXISTS cert.claim (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_kind TEXT NOT NULL,
    subject_ref  JSONB NOT NULL,
    statement    TEXT NOT NULL UNIQUE,
    claim_kind   TEXT NOT NULL,
    method       TEXT NOT NULL REFERENCES cert.method(name),
    probe_sql    TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cert.certificate (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    claim_id     BIGINT NOT NULL REFERENCES cert.claim(id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    status       TEXT NOT NULL,
    evidence     JSONB NOT NULL DEFAULT '{}'::jsonb,
    valid_under  JSONB NOT NULL DEFAULT '{}'::jsonb,
    checked_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (claim_id, seq)
);

CREATE TABLE IF NOT EXISTS cert.witness (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    certificate_id BIGINT NOT NULL REFERENCES cert.certificate(id) ON DELETE CASCADE,
    kind           TEXT   NOT NULL,
    body           JSONB  NOT NULL,
    schema_version JSONB  NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cert_certificate_claim
    ON cert.certificate (claim_id, seq DESC);

CREATE INDEX IF NOT EXISTS cert_witness_certificate_id_idx
    ON cert.witness (certificate_id);

-- Extend kind constraint to cover both Trunkit and Nerode witness kinds.
-- Idempotent: drops any existing kind check, then re-adds the full superset.
-- Safe on a fresh Nerode-only DB (no existing constraint → adds one).
-- Safe on a Trunkit-shared DB (replaces Trunkit-only constraint with superset).
DO $$
BEGIN
    -- Drop under both the explicit name and the PostgreSQL auto-generated name
    -- (Trunkit's 84_cert_witness.sql creates the inline column CHECK whose
    -- auto-generated name is 'witness_kind_check'; we replace it with our
    -- explicitly-named unified constraint so both names are cleaned up.)
    ALTER TABLE cert.witness DROP CONSTRAINT IF EXISTS witness_kind_check;
    ALTER TABLE cert.witness DROP CONSTRAINT IF EXISTS cert_witness_kind_check;
    ALTER TABLE cert.witness
        ADD CONSTRAINT cert_witness_kind_check CHECK (kind IN (
            -- Trunkit kinds
            'term', 'trace', 'counterexample', 'hash_chain', 'kan_diagram',
            -- Nerode kinds
            'construction_record', 'computation_trace',
            'nerode_partition', 'bisimulation'
        ));
EXCEPTION WHEN OTHERS THEN
    NULL;  -- cert.witness may not yet exist; CREATE TABLE above handles that
END;
$$;

-- ---------------------------------------------------------------------------
-- Minimal curry stub (inference provenance) — no-op when Trunkit is present
-- ---------------------------------------------------------------------------

CREATE SCHEMA IF NOT EXISTS curry;

CREATE TABLE IF NOT EXISTS curry.model_versions (
    model_name      TEXT    NOT NULL,
    version         INTEGER NOT NULL,
    checkpoint_hash TEXT    NOT NULL,
    temperature     FLOAT,
    top_p           FLOAT,
    max_tokens      INTEGER,
    PRIMARY KEY (model_name, version)
);

CREATE TABLE IF NOT EXISTS curry.inferences (
    inference_id        TEXT PRIMARY KEY,
    model_name          TEXT NOT NULL,
    model_version       INTEGER NOT NULL,
    input_tokens        BYTEA,
    output_tokens       BYTEA,
    execution_timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    temperature_used    FLOAT,
    max_tokens_used     INTEGER,
    metadata            JSONB
);

-- ---------------------------------------------------------------------------
-- Cert methods for nerode
-- ---------------------------------------------------------------------------

INSERT INTO cert.method (name, claim_kind, checker_kind, description) VALUES
    ('nerode_membership',
     'structural', 'sql',
     'Membership query nerode.run(): DFA simulation with computation-trace witness.'),
    ('nerode_minimization',
     'structural', 'sql',
     'Hopcroft minimization: Myhill-Nerode partition certificate stored as witness.'),
    ('nerode_equivalence',
     'structural', 'sql',
     'Symmetric-difference emptiness check; bisimulation witness or distinguishing string.'),
    ('nerode_construction',
     'structural', 'sql',
     'Certified automaton construction: Thompson+subset for from_regex, product/complement ops.'),
    ('nerode_run',
     'structural', 'sql',
     'Generic certify() wrapper for nerode.run() membership queries.'),
    ('nerode_from_regex',
     'structural', 'sql',
     'Generic certify() wrapper for nerode.from_regex() pipeline.'),
    ('nerode_minimize',
     'structural', 'sql',
     'Generic certify() wrapper for nerode.minimize().'),
    ('nerode_product',
     'structural', 'sql',
     'Generic certify() wrapper for nerode.product().'),
    ('nerode_complement',
     'structural', 'sql',
     'Generic certify() wrapper for nerode.complement().'),
    ('nerode_equivalent',
     'structural', 'sql',
     'Generic certify() wrapper for nerode.equivalent().')
ON CONFLICT (name) DO NOTHING;

-- Model version for nerode checker
INSERT INTO curry.model_versions
    (model_name, version, checkpoint_hash, temperature, top_p, max_tokens)
VALUES ('nerode-checker', 1, 'nerode-v1', 0.0, 1.0, 0)
ON CONFLICT (model_name, version) DO NOTHING;
