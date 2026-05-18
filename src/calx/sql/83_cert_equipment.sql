-- Unified model, step 83: attest the proarrow-equipment structure.
--
-- Steps 21-28 built the kan double-category data (functors = tight,
-- profunctors = loose, NTs = 2-cells, adjunctions); steps 57-64 certified
-- the strata posets. This step certifies that the kan layer is a PROARROW
-- EQUIPMENT (Wood) = FRAMED BICATEGORY (Shulman) = fibrant double category:
-- every tight arrow has a companion and conjoint, so all of formal category
-- theory (restriction, base change, adjoints, Kan extensions) is internal.
-- Four laws (hash-pinned self-contained checker proofs/equipment.py over
-- the certified strata posets A=chain3, B=chain4, P=2x2 bigrading poset;
-- E3 EXHAUSTIVE over all 35 bimodules A-|->B):
--   E1 companion + zig-zag identities;
--   E2 conjoint  + adjunction f_! -| f^*;
--   E3 fibrant: restriction M(p,q) = p_! (.) M (.) q^* (cartesian filler);
--   E4 coherence: (g o f)_! = f_!(.)g_!, (id)_! = (id)^* = U.
-- Canonical signature sha 59dfa3eec3623301; the live engine
-- (build_equipment.py) produces the identical sha and its boolean
-- kan.equipment_laws view is auto-corroborated by the step-79 bridge.
--
-- Live: kan carries the 'equipment' functor + tables kan.equipment[_arrow]
-- (views *_summary/_arrow_witness/_laws). Driven by tools/cert_formal.py.
-- Idempotent.

INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'equipment',
       '{"structure":"proarrow equipment / framed bicategory on strata posets",
         "laws":["E1_companion_zigzag","E2_conjoint_adjunction",
                 "E3_fibrant_base_change","E4_coherence"],
         "model":"A=chain3,B=chain4,P=2x2 bigrading poset; E3 exhaustive over 35 bimodules",
         "companion":"f_! = {(a,b): f(a) <= b}",
         "conjoint":"f^* = {(b,a): b <= f(a)}",
         "canonical":"sha 59dfa3eec3623301"}'::jsonb,
       'the kan layer is a proarrow equipment: every tight arrow has a companion f_! and conjoint f^* satisfying the zig-zag identities, the loose double category is fibrant (restriction = base change via companion/conjoint), and companions/conjoints are pseudofunctorial',
       'formal', 'formal_external', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = 'the kan layer is a proarrow equipment: every tight arrow has a companion f_! and conjoint f^* satisfying the zig-zag identities, the loose double category is fibrant (restriction = base change via companion/conjoint), and companions/conjoints are pseudofunctorial'
);
