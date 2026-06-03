-- Seed the Kolomatskaia–Shulman paper into the kan corpus.
--
-- Claim 1 (empirical_corpus) checks that this slug is present.
-- The PDF is stored at corpus/2311.18781.pdf relative to the repo root.
-- SHA256 computed from the arXiv v2 PDF (2024-02-01), 106 pages.
--
-- Idempotent: ON CONFLICT DO NOTHING.

INSERT INTO kan.corpus_document
    (slug, title, authors, arxiv, source_pdf, source_sha256, pages)
VALUES (
    'lit_displayed_type_theory',
    'Displayed Type Theory and Semi-Simplicial Types',
    'Astra Kolomatskaia, Michael Shulman',
    '2311.18781',
    'corpus/2311.18781.pdf',
    '78f46329b7a6ae78f2ecda5a73c395226e37047a7975ae045359b5a4f9f6e2c0',
    106
)
ON CONFLICT (slug) DO NOTHING;
