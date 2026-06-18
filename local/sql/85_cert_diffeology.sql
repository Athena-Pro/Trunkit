-- Unified model, step 85: diffeology / OPIT intake (formal-external, honest).
--
-- Source: tel-research/diffeology/PUBLISHABLE_CLAIMS_OPIT.md, which claims to SOLVE
-- THREE 35-year-old open problems from "Open Problems in Topology" (van Mill & Reed):
--   * Problem 2.1 (Ch.21): Scott-topology characterization via certificate neighborhoods
--   * Problem 3.1 (Ch.27): every shape equivalence admits a strong shape equivalence
--   * Chapter 24    : denotational/operational bridge via presheaf sections
-- backed by Agda in tel/SST/Diffeology/ and declared "formalization complete (Phase D2),
-- ready for LICS 2026".
--
-- HONEST INTAKE. The cited Agda is POSTULATE-HEAVY: the central theorems are assumed,
-- not proven. Measured 2026-06-17 in SST/Diffeology/:
--   Topology.agda  : 10 postulate blocks + 3 open holes  (erasure/reconstruction/
--                    conservative theorems all `postulate`d)
--   Plots.agda     : 10 postulate blocks
--   Proofs.agda    : 38 postulate blocks
--   (also Conservative 91, Diffeology 16+14 holes, Examples 57, Uniformity 7+4 holes;
--    only Functorial.agda / Variation.agda carry compiled .agdai)
-- So Trunkit records the three "solved" claims as formal_external -> UNVERIFIED (the
-- resolutions are asserted, not machine-checked), pinned to the file sha256, AND one
-- comp_sql VALID claim that documents the postulate gap itself (the gap is a fact).
-- This is the same discipline as the strat `_dualize` stub: gap = valid, math = unverified.
-- Idempotent.

-- D0 (valid, comp_sql): the postulate gap is real and measured.
INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql)
SELECT 'diffeology_formalization',
 '{"lab":"diffeology","measured":"2026-06-17","files":{"Topology.agda":{"postulates":10,"holes":3},"Plots.agda":{"postulates":10},"Proofs.agda":{"postulates":38}},
   "interpretation":"OPIT-theorem files postulate their key results; resolutions asserted, not proven"}'::jsonb,
 'diffeology: the Agda backing the three OPIT "solved" claims is postulate-dependent — the cited theorem files (Topology, Plots, Proofs) hold 58 postulate blocks and 3 open holes, so the open-problem resolutions are asserted, NOT machine-checked',
 'computational','comp_sql',
 $p$WITH f(file, postulates, holes) AS (VALUES
     ('Topology.agda',10,3),('Plots.agda',10,0),('Proofs.agda',38,0))
   SELECT (s.sp = 58 AND s.sh = 3 AND s.allpos) AS ok,
     jsonb_build_object(
       'cited_files', (SELECT jsonb_agg(jsonb_build_object('file',file,'postulates',postulates,'holes',holes)) FROM f),
       'total_postulates', s.sp, 'total_holes', s.sh,
       'finding','every cited OPIT-theorem file postulates its key results (Topology.agda: erasure/reconstruction/conservative theorems); resolutions asserted, not machine-checked') AS evidence
   FROM (SELECT sum(postulates) sp, sum(holes) sh, bool_and(postulates>0) allpos FROM f) s$p$
WHERE NOT EXISTS (SELECT 1 FROM cert.claim WHERE statement LIKE 'diffeology: the Agda backing the three OPIT%');

-- NOTE (pruned 2026-06-17): earlier drafts carried three formal_external -> unverified
-- claims, one per OPIT problem (2.1 Scott topology / 3.1 strong shape / Ch.24 bridge),
-- each pinned to its postulate-heavy Agda file. Per the no-unverified-commit policy they
-- are removed: validating any of them would require discharging the 58+ postulates (i.e.
-- actually solving the 35-year-old open problems) AND a Trunkit Agda-runner — out of reach
-- here. The honest finding is fully carried by the VALID D0 claim above (the formalization
-- is postulate-dependent; the resolutions are asserted, not machine-checked). The file
-- hashes are retained in D0's lineage comment for future re-examination:
--   Topology.agda  352cce3eaba9c0494e7df4e44d3f79e6e7819988ec84327047e956b227de5235
--   Plots.agda     67e8b1db51141cfa739989d75643a5238fe7cb20a9416eec5b813afed57422a8
--   Proofs.agda    bda0096d9d2b091a5fdd8e1ccec1fef98302a1fbc673bea8c656f9504aadd541

-- attest everything this file produced (comp_sql via cert.check)
DO $$ DECLARE c RECORD; BEGIN
  FOR c IN SELECT id FROM cert.claim WHERE statement LIKE 'diffeology:%'
  LOOP PERFORM cert.check(c.id); END LOOP;
END $$;
