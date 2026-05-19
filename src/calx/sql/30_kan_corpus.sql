-- Unified model, step 30: a text corpus inside the `kan` schema.
--
-- The type/category-theory literature cluster lives here as queryable data,
-- with Postgres native full-text search. The corpus is also registered as an
-- *abstract* category ('corpus', db_schema NULL) whose objects are the
-- documents — exercising explicit mode (nullable db_schema, see 20_kan.sql).
--
-- Idempotent: CREATE ... IF NOT EXISTS; the search function is CREATE OR REPLACE.

CREATE TABLE IF NOT EXISTS kan.corpus_document (
    slug          TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    authors       TEXT,
    arxiv         TEXT,
    source_pdf    TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    pages         INTEGER,
    char_count    INTEGER,
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS kan.corpus_chunk (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_slug TEXT    NOT NULL REFERENCES kan.corpus_document(slug) ON DELETE CASCADE,
    ordinal       INTEGER NOT NULL,             -- 0-based position within the document
    char_start    INTEGER NOT NULL,
    char_end      INTEGER NOT NULL,
    body          TEXT    NOT NULL,
    body_tsv      tsvector GENERATED ALWAYS AS (to_tsvector('english', body)) STORED,
    UNIQUE (document_slug, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_corpus_chunk_tsv
    ON kan.corpus_chunk USING GIN (body_tsv);
CREATE INDEX IF NOT EXISTS idx_corpus_chunk_doc
    ON kan.corpus_chunk (document_slug, ordinal);

-- Ranked full-text search across the corpus.
-- Returns the best-matching chunks with their document metadata.
CREATE OR REPLACE FUNCTION kan.corpus_search(p_query TEXT, p_limit INT DEFAULT 10)
RETURNS TABLE (
    document_slug TEXT,
    title         TEXT,
    ordinal       INTEGER,
    rank          REAL,
    snippet       TEXT
)
LANGUAGE sql STABLE AS $$
    SELECT c.document_slug,
           d.title,
           c.ordinal,
           ts_rank(c.body_tsv, websearch_to_tsquery('english', p_query)) AS rank,
           ts_headline('english', c.body,
                       websearch_to_tsquery('english', p_query),
                       'MaxFragments=2, MinWords=8, MaxWords=24, ShortWord=3') AS snippet
      FROM kan.corpus_chunk c
      JOIN kan.corpus_document d ON d.slug = c.document_slug
     WHERE c.body_tsv @@ websearch_to_tsquery('english', p_query)
     ORDER BY rank DESC, c.document_slug, c.ordinal
     LIMIT p_limit;
$$;

-- Per-document corpus summary.
CREATE OR REPLACE VIEW kan.corpus_summary AS
SELECT d.slug,
       d.title,
       d.authors,
       d.arxiv,
       d.pages,
       d.char_count,
       count(c.id)            AS chunks,
       d.ingested_at
  FROM kan.corpus_document d
  LEFT JOIN kan.corpus_chunk c ON c.document_slug = d.slug
 GROUP BY d.slug, d.title, d.authors, d.arxiv, d.pages, d.char_count, d.ingested_at
 ORDER BY d.slug;
