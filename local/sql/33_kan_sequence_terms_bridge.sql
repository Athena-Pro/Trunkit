-- Unified model, step 33: bridge corpus terms into kan.sequence_terms.
--
-- The homology/shadow pipeline reads kan.sequence_terms, but several registered
-- corpus sequences (A002975, A005384, A006512, A023200, A063990) had terms only in
-- calx.sequence_membership (seeded by seed_sequences.py), never in kan.sequence_terms
-- — so seq_homology/factorial_homology produced degenerate all-zero signatures for
-- them, which then collided spuriously and made the shadow engine's resolves-law fail.
--
-- This bridges the first 60 terms (by idx) from calx.sequence_membership into
-- kan.sequence_terms for any registered sequence that has membership data but no kan
-- terms. Sequences already seeded (A000040/45/90 via step 32) are left untouched.
-- Idempotent.

INSERT INTO kan.sequence_terms (seq_id, idx, term)
SELECT q.seq_id, (q.rn - 1)::int AS idx, q.n
FROM (
    SELECT m.seq_id, m.n,
           row_number() OVER (PARTITION BY m.seq_id ORDER BY m.idx) AS rn
      FROM calx.sequence_membership m
      JOIN calx.sequences s ON s.seq_id = m.seq_id
     WHERE NOT EXISTS (SELECT 1 FROM kan.sequence_terms kt WHERE kt.seq_id = m.seq_id)
) q
WHERE q.rn <= 60
ON CONFLICT (seq_id, idx) DO NOTHING;
