-- Unified model, step 78: attest Monstrous Moonshine -- F_1 = trivial rep.
--
-- The capstone identification: the field-with-one-element unit that was
-- load-bearing through every step (closer 71 / zeta operator 73 / radix-1
-- cell 75 / W_0 67) IS the trivial representation of the Monster. The
-- McKay "+1" in every graded dimension of V-natural is exactly that F_1
-- point; Ogg's supersingular primes (= primes dividing |M|) are the same
-- genus-zero prime horizon as lithon's 15-prime adelic window. Four laws
-- (hash-pinned self-contained checker proofs/moonshine.py):
--   M1 McKay F_1     dim V_n decomposes EXACTLY into Monster irreps for
--                    n=1..4, every decomposition carrying the trivial rep
--                    at multiplicity >= 1 (the universal "+1");
--   M2 ss horizon    prime powers reconstruct |M| exactly; prime set =
--                    the 15 supersingular primes (lithon overlap 13/15);
--   M3 j syzygy      j-coefficients (exact E4^3/Delta) are self-syzygy
--                    crackable with eventual leading digit 1 (Fibonacci
--                    class; consecutive ratio e^{2pi/sqrt n} -> 1);
--   M4 radix collapse binary F_1-depth O(sqrt n) << magnitude e^{4pi sqrt n}.
-- Moonshine is F_1 glued to the Monster -- canonical sha b3c57a48f65662d0.
--
-- Live: kan carries the 'moonshine' functor + tables kan.moonshine[_term]
-- (views *_summary/_supersingular/_laws). Driven by tools/cert_formal.py.
-- Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'moonshine',
       '{"thesis":"F_1 = the Monster trivial representation; the McKay +1 is the F_1 point",
         "laws":["M1_mckay_f1","M2_supersingular_horizon",
                 "M3_j_self_syzygy_crackable","M4_radix_collapse"],
         "supersingular":"primes(|M|) = 15 supersingular primes; lithon overlap 13/15",
         "j_class":"crackable, Fibonacci class, eventual leading digit 1",
         "canonical":"sha b3c57a48f65662d0"}'::jsonb,
       'Monstrous Moonshine is F_1 glued to the Monster: the McKay +1 in every graded dimension of V-natural is the F_1 point (the trivial representation), the primes dividing |M| are exactly the 15 supersingular genus-zero primes mirroring the lithon horizon, and the j-coefficients are self-syzygy-crackable (Fibonacci class) and radix-collapsible',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'Monstrous Moonshine is F_1 glued to the Monster: the McKay +1 in every graded dimension of V-natural is the F_1 point (the trivial representation), the primes dividing |M| are exactly the 15 supersingular genus-zero primes mirroring the lithon horizon, and the j-coefficients are self-syzygy-crackable (Fibonacci class) and radix-collapsible'
);
