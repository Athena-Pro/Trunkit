-- Trunkit local overlay, step 95b: account for the shield-order scar in the
-- canonical ledger, as a LIVE claim rather than a comment.
--
-- Two certificates on the canonical ledger (772 and 787, both float_heuristic
-- claims attested 2026-07-02) fail cert.verify_chain()'s content check with
-- "row altered". They were not altered. They were written while the exactness
-- shield fired AFTER cert_certificate_hash (see
-- src/calx/sql/94_cert_exactness.sql for the mechanism and the fix),
-- so their stored row_hash commits to the pre-shield content: status 'valid'
-- and evidence without the {shield, original_status} keys.
--
-- cert.certificate is append-only -- cert_certificate_append_only rejects
-- UPDATE and DELETE -- so these rows can never be repaired. The honest move is
-- not to hide them but to make the ledger explain its own scar, re-checkably.
--
-- The probe below does NOT hardcode 772/787. It recomputes every certificate
-- two ways and asserts that the set of content-mismatching rows is exactly the
-- set explained by the pre-shield reconstruction. So:
--   * the known scar keeps this claim VALID and enumerated in its evidence;
--   * a genuinely altered row -- one that reproduces under neither recipe --
--     REFUTES it. The claim is a live tamper detector that tolerates a known,
--     provably-mechanical defect, instead of a note that rots.
--
-- Idempotent.

DROP TABLE IF EXISTS _ledger_scar_claims;
CREATE TEMP TABLE _ledger_scar_claims (
    subject_kind TEXT, subject_ref JSONB, statement TEXT,
    claim_kind TEXT, method TEXT, domain TEXT, probe_sql TEXT);

INSERT INTO _ledger_scar_claims VALUES
    ('cert_ledger',
     '{"ledger":"canonical","law":"L0_shield_order_scar"}',
     'cert ledger integrity: every certificate whose content hash fails to reproduce is explained by the exactness-shield trigger-order defect (the row was hashed before the shield rewrote status/evidence), NOT by alteration -- proven by reproducing each stored row_hash from the pre-shield content. Any row reproducing under neither recipe refutes this claim. cert.certificate is append-only, so the affected rows can never be repaired, only accounted for; fixed forward by src/calx/sql/94_cert_exactness.sql.',
     'computational', 'comp_sql', 'exact_int',
     $probe$
     WITH c AS (
         SELECT ce.id, ce.claim_id, ce.seq, ce.status, ce.evidence, ce.valid_under,
                ce.prev_hash, ce.row_hash, ce.premise_hashes,
                i.row_hash AS inf_hash,
                lag(ce.row_hash) OVER (ORDER BY ce.id) AS expected_prev
           FROM cert.certificate ce
           LEFT JOIN curry.inferences i
                  ON i.inference_id = ce.checker_inference_id
     ), j AS (
         SELECT id,
                (prev_hash IS NOT DISTINCT FROM expected_prev) AS link_ok,
                (row_hash = cert.certificate_row_hash(
                     claim_id, seq, status, evidence, valid_under,
                     inf_hash, prev_hash, premise_hashes)) AS content_ok,
                (row_hash = cert.certificate_row_hash(
                     claim_id, seq, 'valid',
                     (evidence - 'shield' - 'original_status'), valid_under,
                     inf_hash, prev_hash, premise_hashes)) AS preshield_ok
           FROM c
     )
     SELECT (COUNT(*) FILTER (WHERE NOT content_ok AND NOT preshield_ok) = 0
             AND COUNT(*) FILTER (WHERE NOT link_ok) = 0) AS ok,
            jsonb_build_object(
                'certificates',      COUNT(*),
                'links_ok',          COUNT(*) FILTER (WHERE link_ok),
                'content_ok',        COUNT(*) FILTER (WHERE content_ok),
                'shield_order_scar', COALESCE((SELECT jsonb_agg(id ORDER BY id)
                                                FROM j
                                               WHERE NOT content_ok AND preshield_ok),
                                              '[]'::jsonb),
                'unexplained',       COALESCE((SELECT jsonb_agg(id ORDER BY id)
                                                FROM j
                                               WHERE NOT content_ok AND NOT preshield_ok),
                                              '[]'::jsonb),
                'broken_links',      COALESCE((SELECT jsonb_agg(id ORDER BY id)
                                                FROM j WHERE NOT link_ok),
                                              '[]'::jsonb),
                'reading', 'scar = provably the trigger-order defect; unexplained = genuine alteration'
            ) AS evidence
       FROM j
     $probe$);

INSERT INTO cert.claim (subject_kind, subject_ref, statement,
                        claim_kind, method, domain, probe_sql)
SELECT subject_kind, subject_ref, statement, claim_kind, method, domain, probe_sql
  FROM _ledger_scar_claims
ON CONFLICT (statement)
DO UPDATE SET probe_sql = EXCLUDED.probe_sql,
              domain    = EXCLUDED.domain;

DROP TABLE _ledger_scar_claims;
