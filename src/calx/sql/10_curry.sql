-- Unified model, step 1: the `curry` schema.
--
-- A faithful Postgres port of Curry's SQLite store. Semantics preserved: immutable, version-locked constants /
-- functions / models; full inference provenance; deterministic execution cache.
--
-- Type mapping vs. the SQLite original:
--   TEXT                          -> TEXT
--   INTEGER                       -> INTEGER / BIGINT
--   BLOB                          -> BYTEA
--   BOOLEAN                       -> BOOLEAN
--   TIMESTAMP DEFAULT now         -> TIMESTAMPTZ DEFAULT now()
--   JSON-in-TEXT (bindings, etc.) -> JSONB
--
-- All CREATE statements use IF NOT EXISTS so this file is idempotent.

CREATE TABLE IF NOT EXISTS curry.retirement_tags (
    tag_id      TEXT PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason      TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS curry.constants (
    id                TEXT    NOT NULL,
    version           INTEGER NOT NULL,
    value             BYTEA   NOT NULL,
    type_signature    TEXT    NOT NULL,
    declared_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    retired_at        TIMESTAMPTZ,
    retirement_tag_id TEXT REFERENCES curry.retirement_tags(tag_id),
    description       TEXT,
    PRIMARY KEY (id, version)
);

CREATE TABLE IF NOT EXISTS curry.type_compatibility (
    constant_id         TEXT    NOT NULL,
    from_version        INTEGER NOT NULL,
    to_version          INTEGER NOT NULL,
    is_compatible       BOOLEAN DEFAULT TRUE,
    conversion_function TEXT,
    PRIMARY KEY (constant_id, from_version, to_version),
    FOREIGN KEY (constant_id, from_version) REFERENCES curry.constants(id, version),
    FOREIGN KEY (constant_id, to_version)   REFERENCES curry.constants(id, version)
);

CREATE TABLE IF NOT EXISTS curry.functions (
    name              TEXT    NOT NULL,
    version           INTEGER NOT NULL,
    body              TEXT    NOT NULL,
    constant_bindings JSONB   NOT NULL DEFAULT '{}'::jsonb,
    function_bindings JSONB            DEFAULT '{}'::jsonb,
    is_pure           BOOLEAN DEFAULT FALSE,
    declared_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    retired_at        TIMESTAMPTZ,
    retirement_tag_id TEXT REFERENCES curry.retirement_tags(tag_id),
    expected_args     JSONB,
    description       TEXT,
    arg_descriptions  JSONB,
    PRIMARY KEY (name, version)
);

CREATE TABLE IF NOT EXISTS curry.function_dependencies (
    id                          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    function_name               TEXT    NOT NULL,
    function_version            INTEGER NOT NULL,
    depends_on_constant_id      TEXT,
    depends_on_constant_version INTEGER,
    depends_on_function_name    TEXT,
    depends_on_function_version INTEGER,
    FOREIGN KEY (function_name, function_version)
        REFERENCES curry.functions(name, version)
);

CREATE TABLE IF NOT EXISTS curry.model_versions (
    model_name              TEXT    NOT NULL,
    version                 INTEGER NOT NULL,
    checkpoint_hash         TEXT    NOT NULL,
    model_type              TEXT,
    base_model_name         TEXT,
    base_model_version      INTEGER,
    temperature             REAL,
    top_p                   REAL,
    max_tokens              INTEGER,
    system_prompt_id        TEXT,
    system_prompt_version   INTEGER,
    trained_on_data_id      TEXT,
    trained_on_data_version INTEGER,
    declared_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    retired_at              TIMESTAMPTZ,
    retirement_tag_id       TEXT REFERENCES curry.retirement_tags(tag_id),
    PRIMARY KEY (model_name, version),
    FOREIGN KEY (system_prompt_id, system_prompt_version)
        REFERENCES curry.constants(id, version)
);

CREATE TABLE IF NOT EXISTS curry.prompts (
    prompt_id             TEXT    NOT NULL,
    version               INTEGER NOT NULL,
    name                  TEXT,
    description           TEXT,
    system_prompt_id      TEXT,
    system_prompt_version INTEGER,
    instruction_template  TEXT    NOT NULL,
    input_schema          JSONB,
    output_schema         JSONB,
    declared_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    retired_at            TIMESTAMPTZ,
    retirement_tag_id     TEXT REFERENCES curry.retirement_tags(tag_id),
    PRIMARY KEY (prompt_id, version),
    FOREIGN KEY (system_prompt_id, system_prompt_version)
        REFERENCES curry.constants(id, version)
);

CREATE TABLE IF NOT EXISTS curry.inferences (
    inference_id           TEXT PRIMARY KEY,
    model_name             TEXT    NOT NULL,
    model_version          INTEGER NOT NULL,
    input_tokens           TEXT,
    output_tokens          BYTEA   NOT NULL,
    temperature_used       REAL,
    top_p_used             REAL,
    seed                   BIGINT,
    execution_timestamp    TIMESTAMPTZ NOT NULL DEFAULT now(),
    execution_duration_ms  INTEGER,
    metadata               JSONB,
    FOREIGN KEY (model_name, model_version)
        REFERENCES curry.model_versions(model_name, version)
);

CREATE TABLE IF NOT EXISTS curry.execution_cache (
    function_name    TEXT    NOT NULL,
    function_version INTEGER NOT NULL,
    input_hash       TEXT    NOT NULL,
    output_hash      TEXT    NOT NULL,
    cached_result    BYTEA   NOT NULL,
    cached_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    hit_count        INTEGER DEFAULT 1,
    PRIMARY KEY (function_name, function_version, input_hash),
    FOREIGN KEY (function_name, function_version)
        REFERENCES curry.functions(name, version)
);

CREATE INDEX IF NOT EXISTS idx_curry_constants_active
    ON curry.constants (id, version) WHERE retired_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_curry_functions_active
    ON curry.functions (name, version) WHERE retired_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_curry_inferences_model
    ON curry.inferences (model_name, model_version, execution_timestamp);
