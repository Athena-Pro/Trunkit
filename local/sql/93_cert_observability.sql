-- Unified model, step 93: cert observability + consensus (2026-05-29 session).
--
-- Durable schema for the verification methods added this session. Data (claims,
-- votes, probe rewrites) lives in the ledger / attestation scripts; this file is
-- the reproducible STRUCTURE. Idempotent.
--
--   * subject-existence guard  (Tier 1) -- cert.subject_probe   (host-fed by tools/tel_subject_guard.py)
--   * live build/test          (Tier 2) -- cert.live_build      (host-fed by tools/tel_build_check.py)
--   * plain-language board      (#3)     -- cert.board / cert.board_summary
--   * crown consensus (OCTT)             -- cert.evidence_vote + cert.crown_consensus
--
-- All obey the three-valued rule hardened in steps 40 & 79: a subject that has
-- moved -> unverified (not silent green); a build the checker can't invoke ->
-- unverified (not false refuted); competing models that disagree -> contested
-- (the OCTT partial-closure window), never a fake green.

-- ---- Tier 1: subject-existence guard ---------------------------------------
CREATE TABLE IF NOT EXISTS cert.subject_probe (
  claim_id int PRIMARY KEY, path text, exists boolean,
  fingerprint text, build_evidence jsonb, checked_at timestamptz);

-- ---- Tier 2: live build/test -----------------------------------------------
CREATE TABLE IF NOT EXISTS cert.live_build (
  claim_id int PRIMARY KEY, tool text, cmd text,
  status text, detail text, checked_at timestamptz);

-- ---- #3: plain-language status board ---------------------------------------
CREATE OR REPLACE VIEW cert.board AS
SELECT cl.id AS claim_id,
  CASE
    WHEN cl.subject_kind='tel_project' THEN 'TEL builds'
    WHEN cl.subject_kind='tel_behavior' THEN 'TEL behavior'
    WHEN cl.subject_kind='tel_graphics'  THEN 'TEL graphics'
    WHEN cl.subject_kind='tel_calx_live'    THEN 'TEL graphics'
    WHEN cl.subject_kind='tel_visuals_live' THEN 'TEL graphics'
    WHEN cl.subject_kind='tel_constants'    THEN 'TEL constants'
    WHEN cl.subject_kind IN ('tel_claim','number_fact','repo_layout') THEN 'TEL results & hygiene'
    WHEN cl.subject_kind IN ('duality_depth','operator_graph','frontier_residual') THEN 'Cross-lab structures'
    WHEN cl.subject_kind IN ('trunkit_method','cert_soundness') THEN 'Methods & self-checks'
    WHEN cl.subject_kind LIKE 'curry_%' THEN 'Curry (provenance)'
    WHEN cl.subject_kind IN ('homology_fact','sequence_homology','factorial_homology','shared_prime_h2') THEN 'Math: homology'
    WHEN cl.subject_kind LIKE 'kan_%' OR cl.subject_kind IN ('lithon','shadow','moonshine','grading','bigrading','chromatic','equipment','strata_tower','colimit_closure','identity_decomposition','self_shadow','self_syzygy','f1_radix','prime_members_functor','combined_scale','combined_signature','developed_sequence','omega_family','omega_family_succ','loom_frame','loom_lift') THEN 'Math: kan engines'
    ELSE 'Other' END AS area,
  s.status,
  CASE s.status WHEN 'valid' THEN '✅ verified' WHEN 'refuted' THEN '❌ failed'
                WHEN 'unverified' THEN '❓ unknown' WHEN 'unchecked' THEN '⬜ not checked'
                WHEN 'error' THEN '⚠ error' WHEN 'pass' THEN '✅ verified'
                WHEN 'contested' THEN '⚖ contested' ELSE s.status END AS plain,
  cl.statement
FROM cert.claim cl JOIN cert.standing s ON s.claim_id=cl.id;

CREATE OR REPLACE VIEW cert.board_summary AS
SELECT area,
  count(*) FILTER (WHERE status IN ('valid','pass'))  AS verified,
  count(*) FILTER (WHERE status='refuted')            AS failed,
  count(*) FILTER (WHERE status IN ('unverified','unchecked','error')) AS unknown,
  count(*) AS total
FROM cert.board GROUP BY area ORDER BY area;

-- ---- Crown consensus (Open Crown Type Theory) ------------------------------
-- Competing evidence from different models for one claim; each model = a crown
-- party / horn. Adjudicated by topology (dissent algebra: veto=max, parallel=min,
-- series=sum). The partial-closure window is the principled 'contested' verdict.
CREATE TABLE IF NOT EXISTS cert.evidence_vote (
  claim_id int, model_name text, agrees boolean,
  cost numeric DEFAULT 1,            -- dissent cost: budget to make this party yield (0 if it agrees)
  evidence jsonb, voted_at timestamptz DEFAULT now(),
  PRIMARY KEY (claim_id, model_name));

CREATE OR REPLACE FUNCTION cert.crown_consensus(p_claim int, p_topology text DEFAULT 'veto', p_k int DEFAULT NULL)
RETURNS TABLE(verdict text, k_star numeric, agree_n int, total_n int, evidence jsonb)
LANGUAGE plpgsql AS $$
DECLARE v_total int; v_agree int; v_dissent int; v_min numeric; v_max numeric; v_sum numeric;
        v_kstar numeric; v_verdict text;
BEGIN
  SELECT count(*), count(*) FILTER (WHERE agrees), count(*) FILTER (WHERE NOT agrees)
    INTO v_total, v_agree, v_dissent FROM cert.evidence_vote WHERE claim_id=p_claim;
  IF v_total = 0 THEN
    RETURN QUERY SELECT 'unverified', NULL::numeric, 0, 0, '{"note":"no model evidence"}'::jsonb; RETURN;
  END IF;
  SELECT COALESCE(min(CASE WHEN agrees THEN 0 ELSE cost END),0),
         COALESCE(max(CASE WHEN agrees THEN 0 ELSE cost END),0),
         COALESCE(sum(CASE WHEN agrees THEN 0 ELSE cost END),0)
    INTO v_min, v_max, v_sum FROM cert.evidence_vote WHERE claim_id=p_claim;
  v_kstar := CASE p_topology WHEN 'series' THEN v_sum WHEN 'parallel' THEN v_min ELSE v_max END;
  v_verdict := CASE
    WHEN p_topology='parallel'  AND v_agree >= 1 THEN 'valid'
    WHEN p_topology='threshold' AND v_agree >= COALESCE(p_k, ceil(v_total/2.0)) THEN 'valid'
    WHEN p_topology IN ('veto','series') AND v_dissent = 0 THEN 'valid'
    WHEN v_agree = 0 THEN 'refuted'
    ELSE 'contested' END;
  RETURN QUERY SELECT v_verdict, v_kstar, v_agree, v_total,
    jsonb_build_object('topology',p_topology,'agree',v_agree,'dissent',v_dissent,'k_star',v_kstar,
      'partial_window',(v_verdict='contested'),'dissent_cost_min_max_sum',jsonb_build_array(v_min,v_max,v_sum));
END $$;
