-- Unified model, step 60: attest coreflection + coproduct universal props.
--
-- The strata tower is promoted from "complete orthogonal idempotents" to two
-- universal properties (hash-pinned self-contained checker proofs/grading.py):
--
--   (A) each rung W_k is a COREFLECTOR  i_k -| W_k :
--       A1 idempotent, A2 counit eps:W_k=>Id natural, A3 triangle
--       identities, A4 W_k(S) is the terminal omega=k subobject.
--   (B) the sequence object is the COPRODUCT of its rungs:
--       S|{omega>=1} ~= coproduct_k W_k(S)  with B1 jointly surjective,
--       B2 pairwise disjoint, B3 unique mediating map (the rungs partition
--       the object), B4 the coproduct recovers the object -- a genuine
--       Z>=1-graded decomposition.
--
-- Canonical: naturals(120) omega-decomposition = [40,66,13]. Live, kan
-- carries the adjunctions coreflect_W1..3 (counit/unit NTs) and the tables
-- kan.coreflection / kan.grading_decomposition (views *_summary, *_laws).
-- Driven by tools/cert_formal.py. Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'grading',
       '{"coreflection":"i_k -| W_k (rung subcategory coreflective)",
         "counit":"eps_k : W_k => Id (stratum inclusion)",
         "coproduct":"S|{omega>=1} = coproduct_k W_k(S)",
         "laws":["A1_idempotent","A2_counit_natural","A3_triangle",
                 "A4_terminal_subobject","B1_jointly_surjective",
                 "B2_disjoint","B3_unique_mediating","B4_recovers_object"],
         "canonical":"naturals(120) decomposition=[40,66,13]"}'::jsonb,
       'each strata rung W_k is a coreflector (i_k -| W_k) and the sequence object is the coproduct of its rungs (a Z-graded decomposition)',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'each strata rung W_k is a coreflector (i_k -| W_k) and the sequence object is the coproduct of its rungs (a Z-graded decomposition)'
);
