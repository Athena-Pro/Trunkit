-- Unified model, step 52: attest the system-developed unpredictable sequence.
--
-- The capability test: from three calx-rooted Recaman candidates the system
-- deterministically selected 'aliquot' (Recaman with jump sigma(n)-n),
-- registered as Z000001. Formal claim, three laws:
--   1  deterministic synthesis: 'aliquot' wins the unpredictability metric;
--      its first 60 terms hash to a fixed sha256;
--   2  uniqueness: its combined difference+factorial 7-vector
--      [39,26,24,0,0,0,573] differs from every one of the 23 prior corpus
--      sequences -- a genuinely new point in invariant space;
--   3  unpredictability: its difference tower does not collapse
--      (delta^0,1,2 all H1>0) whereas the polynomial control (squares)
--      collapses to [34,0,0].
--
-- Backed by the self-contained hash-pinned checker
-- proofs/developed_sequence.py via tools/cert_formal.py. Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'developed_sequence',
       '{"seq_id":"Z000001","rule":"Recaman with jump sigma(n)-n",
         "selected_from":["radical","bigomega","aliquot"],
         "combined_signature":[39,26,24,0,0,0,573],
         "sha256":"9bc2e547f7c1fdd7b2c3c33fcda89e1fc33031ef4475841605f3abb07699e71f",
         "claims":["deterministic_synthesis","unique_vs_23_corpus",
                   "unpredictable_difference_tower_non_collapse"]}'::jsonb,
       'system-developed Aliquot-Recaman Z000001 is deterministic, unique vs the 23 corpus, and unpredictable (non-collapsing difference tower)',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'system-developed Aliquot-Recaman Z000001 is deterministic, unique vs the 23 corpus, and unpredictable (non-collapsing difference tower)'
);
