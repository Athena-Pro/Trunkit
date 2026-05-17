"""Load the type/category-theory literature cluster into the kan corpus.

Source: the lit_*.txt files + manifest produced by build_lit_corpus.py
Target: kan.corpus_document / kan.corpus_chunk in the live `calx` Postgres,
        plus an abstract 'corpus' category whose objects are the documents.

Idempotent: documents upsert by slug; a document's chunks are replaced wholesale
on re-run (chunk boundaries are deterministic but may change if this script's
parameters change).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg

GRAPHRAG_INPUT = Path("graphrag_input")
MANIFEST = Path("lit_corpus_manifest.json")
PG_DSN = os.environ.get(
    "CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk"
)

CHUNK_CHARS = 2500       # target chunk size
OVERLAP_CHARS = 250      # carry-over between adjacent chunks


def strip_header(text: str) -> str:
    """Drop the leading '# ...' provenance block and following blank lines."""
    lines = text.splitlines()
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    return "\n".join(lines[i:]).strip()


def chunk(body: str):
    """Paragraph-aware chunking with overlap. Yields (ordinal, start, end, text)."""
    paras = [p for p in body.split("\n\n") if p.strip()]
    chunks = []
    buf = ""
    buf_start = 0
    cursor = 0  # char offset into `body` as we consume paragraphs

    def flush(end_offset: int):
        nonlocal buf, buf_start
        if buf.strip():
            chunks.append((buf_start, end_offset, buf.strip()))
        buf = ""

    for p in paras:
        p_start = body.find(p, cursor)
        if p_start < 0:
            p_start = cursor
        p_end = p_start + len(p)
        cursor = p_end

        if not buf:
            buf_start = p_start
        buf = p if not buf else f"{buf}\n\n{p}"

        if len(buf) >= CHUNK_CHARS:
            flush(p_end)
            # Seed the next buffer with an overlap window of the flushed text.
            prev = chunks[-1][2]
            buf = prev[-OVERLAP_CHARS:]
            buf_start = max(0, p_end - len(buf))

    flush(cursor)

    for ordinal, (s, e, t) in enumerate(chunks):
        yield ordinal, s, e, t


def main() -> int:
    if not MANIFEST.is_file():
        print(f"error: manifest not found at {MANIFEST}", file=sys.stderr)
        return 1
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    docs = manifest["documents"]

    with psycopg.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            # Abstract category for the literature (db_schema NULL = explicit mode).
            cur.execute(
                """INSERT INTO kan.category (name, db_schema, description)
                   VALUES ('corpus', NULL,
                           'Type/category-theory literature cluster; objects are documents')
                   ON CONFLICT (name) DO NOTHING"""
            )

            total_chunks = 0
            for d in docs:
                slug = d["slug"]
                txt_path = GRAPHRAG_INPUT / d["output_file"]
                if not txt_path.is_file():
                    print(f"  [MISS] {txt_path} — skipping", file=sys.stderr)
                    continue
                body = strip_header(txt_path.read_text(encoding="utf-8"))

                cur.execute(
                    """INSERT INTO kan.corpus_document
                           (slug, title, authors, arxiv, source_pdf,
                            source_sha256, pages, char_count)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (slug) DO UPDATE SET
                           title=EXCLUDED.title, authors=EXCLUDED.authors,
                           arxiv=EXCLUDED.arxiv, source_pdf=EXCLUDED.source_pdf,
                           source_sha256=EXCLUDED.source_sha256,
                           pages=EXCLUDED.pages, char_count=EXCLUDED.char_count""",
                    (
                        slug, d["title"], d["authors"], d["arxiv"],
                        d["source_pdf"], d["source_sha256"], d["pages"], len(body),
                    ),
                )

                # Replace chunks wholesale for deterministic re-runs.
                cur.execute(
                    "DELETE FROM kan.corpus_chunk WHERE document_slug = %s", (slug,)
                )
                n = 0
                for ordinal, start, end, text in chunk(body):
                    cur.execute(
                        """INSERT INTO kan.corpus_chunk
                               (document_slug, ordinal, char_start, char_end, body)
                           VALUES (%s, %s, %s, %s, %s)""",
                        (slug, ordinal, start, end, text),
                    )
                    n += 1
                total_chunks += n

                # Register the document as an object of the 'corpus' category.
                cur.execute(
                    """INSERT INTO kan.object (category, name, table_name)
                       VALUES ('corpus', %s, NULL)
                       ON CONFLICT (category, name) DO NOTHING""",
                    (slug,),
                )
                print(f"  [OK ] {slug}: {len(body):,} chars -> {n} chunks")

        conn.commit()

    print(f"\nloaded {len(docs)} documents, {total_chunks} chunks into kan.corpus_*")
    return 0


if __name__ == "__main__":
    sys.exit(main())
