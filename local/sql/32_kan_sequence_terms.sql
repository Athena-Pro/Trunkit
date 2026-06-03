-- Seed kan.sequence_terms for chromatic convergence check — 32_kan_sequence_terms.sql
--
-- The chromatic_convergence view checks top_cum = n_terms, where top_cum is the
-- cumulative chromatic layer count and n_terms is the count of sequence_terms rows.
-- chromatic_layer was pre-populated with 60 terms per sequence for A000040/45/90;
-- sequence_terms must match or chromatic_laws returns NULL (treated as empty engine).
--
-- Idempotent: ON CONFLICT DO NOTHING.

-- Register sequences in calx.sequences (required FK for sequence_terms)
INSERT INTO calx.sequences (seq_id, name, seq_type)
VALUES
    ('A000040', 'The prime numbers',                          'prime'),
    ('A000045', 'Fibonacci numbers: F(n) = F(n-1) + F(n-2)', 'recurrence'),
    ('A000290', 'The squares: a(n) = n^2',                   'polynomial')
ON CONFLICT (seq_id) DO NOTHING;

-- 60 primes (A000040) from calx.primes
INSERT INTO kan.sequence_terms (seq_id, idx, term)
SELECT 'A000040', (row_number() OVER (ORDER BY p))::int - 1, p
FROM calx.primes
ORDER BY p
LIMIT 60
ON CONFLICT (seq_id, idx) DO NOTHING;

-- 60 squares (A000290): 0^2, 1^2, ..., 59^2
INSERT INTO kan.sequence_terms (seq_id, idx, term)
SELECT 'A000290', (g-1)::int, ((g-1)*(g-1))::numeric
FROM generate_series(1, 60) g
ON CONFLICT (seq_id, idx) DO NOTHING;

-- 60 Fibonacci numbers (A000045): F(0)..F(59)
WITH RECURSIVE fib(idx, a, b) AS (
    SELECT 0, 0::numeric, 1::numeric
    UNION ALL
    SELECT idx+1, b, a+b FROM fib WHERE idx < 59
)
INSERT INTO kan.sequence_terms (seq_id, idx, term)
SELECT 'A000045', idx::int, a FROM fib
ON CONFLICT (seq_id, idx) DO NOTHING;
