-- Unified model, step 44: attest the difference-tower homology finding.
--
-- Formal claim: the gap-pattern-homology signature tower of nine OEIS
-- classics has specific measured values, AND Catalan/Bell/Motzkin form a
-- cross-family depth-equivalence class ([0,0,0] at every difference order)
-- that the original prefix/blowup OEIS matcher structurally cannot detect.
--
-- Backed by the self-contained hash-pinned checker
-- proofs/seq_homology_signature.py, driven by tools/cert_formal.py.
-- This attests a real analytical result of the unified model -- the depth
-- generalization of the OEIS-similarity work. Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'sequence_homology',
       '{"sequences":["A000040","A000041","A000045","A000108","A000110",
                      "A000217","A000290","A000578","A001006"],
         "invariant":"H1_difference_tower",
         "depth_class":["A000108","A000110","A001006"],
         "depth_class_signature":[0,0,0],
         "contrast":"invisible to prefix/blowup oeis_match"}'::jsonb,
       'OEIS difference-tower H1 signatures hold; Catalan/Bell/Motzkin are a prefix-invisible depth class',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'OEIS difference-tower H1 signatures hold; Catalan/Bell/Motzkin are a prefix-invisible depth class'
);
