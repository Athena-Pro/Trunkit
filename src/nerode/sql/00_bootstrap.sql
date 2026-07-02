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

-- THE canonical witness-kind vocabulary — the Nerode-side mirror of the
-- identical block in Trunkit's 84_cert_witness.sql (tests/test_witness_kinds.py
-- asserts the two lists never drift). Covers BOTH stacks so the constraint is
-- correct on a Nerode-only DB and a no-op-equivalent on a co-located one.
-- Do NOT drop/re-add this constraint in any later schema file.
-- Idempotent superset replace; drops the legacy auto-generated name too.
DO $$
BEGIN
    ALTER TABLE cert.witness DROP CONSTRAINT IF EXISTS witness_kind_check;
    ALTER TABLE cert.witness DROP CONSTRAINT IF EXISTS cert_witness_kind_check;
    ALTER TABLE cert.witness
        ADD CONSTRAINT cert_witness_kind_check CHECK (kind IN (
            -- cert core
            'term', 'trace', 'counterexample', 'hash_chain', 'kan_diagram',
            -- crypto tier (calx 97_cert_crypto.sql)
            'arith_constraint', 'snark_proof',
            -- Nerode automata bridge
            'construction_record', 'computation_trace',
            'nerode_partition', 'bisimulation', 'state_map',
            -- topological bridge (98_topological_signature.sql)
            'betti'
        ));
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
