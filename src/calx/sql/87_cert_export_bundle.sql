-- cert.export_bundle: portable proof bundle.
-- Packages one or more claims with their latest certificates, witnesses,
-- and derivation chains into self-contained JSONB. The output travels with
-- code to a consumer who calls cert.verify() on each embedded claim.

CREATE OR REPLACE FUNCTION cert.export_bundle(p_claim_ids BIGINT[])
RETURNS JSONB AS $$
WITH
-- Latest certificate per claim
latest_cert AS (
    SELECT DISTINCT ON (claim_id)
           claim_id, id AS cert_id, seq, status, evidence, valid_under, checked_at
      FROM cert.certificate
     WHERE claim_id = ANY(p_claim_ids)
     ORDER BY claim_id, seq DESC
),
-- Latest witness per certificate
latest_witness AS (
    SELECT DISTINCT ON (w.certificate_id)
           w.certificate_id, w.kind AS witness_kind, w.body AS witness_body
      FROM cert.witness w
      JOIN latest_cert lc ON lc.cert_id = w.certificate_id
     ORDER BY w.certificate_id, w.id DESC
),
-- Derivation for each claim (one per conclusion, if exists)
derivations AS (
    SELECT d.conclusion_id, d.premise_ids, d.rule, d.asserted_at
      FROM cert.derivation d
     WHERE d.conclusion_id = ANY(p_claim_ids)
),
-- Artifact specs for formal claims
artifacts AS (
    SELECT a.claim_id, a.kind AS artifact_kind, a.path,
           a.sha256, a.checker_cmd, a.registered_at
      FROM cert.artifact a
     WHERE a.claim_id = ANY(p_claim_ids)
)
SELECT jsonb_build_object(
    'trunk_bundle_version', 1,
    'exported_at', now(),
    'claims', jsonb_agg(
        jsonb_build_object(
            'claim',       to_jsonb(cl),
            'certificate', jsonb_build_object(
                               'seq',        lc.seq,
                               'status',     lc.status,
                               'evidence',   lc.evidence,
                               'valid_under', lc.valid_under,
                               'checked_at', lc.checked_at
                           ),
            'witness',     CASE WHEN lw.witness_kind IS NOT NULL
                           THEN jsonb_build_object('kind', lw.witness_kind, 'body', lw.witness_body)
                           ELSE NULL END,
            'derivation',  CASE WHEN d.conclusion_id IS NOT NULL
                           THEN jsonb_build_object(
                                    'premise_ids', to_jsonb(d.premise_ids),
                                    'rule',        d.rule,
                                    'asserted_at', d.asserted_at
                                )
                           ELSE NULL END,
            'artifact',    CASE WHEN a.claim_id IS NOT NULL
                           THEN jsonb_build_object(
                                    'kind',         a.artifact_kind,
                                    'path',         a.path,
                                    'sha256',       a.sha256,
                                    'checker_cmd',  a.checker_cmd,
                                    'registered_at', a.registered_at
                                )
                           ELSE NULL END
        )
    )
)
FROM cert.claim cl
JOIN latest_cert  lc ON lc.claim_id = cl.id
LEFT JOIN latest_witness lw ON lw.certificate_id = lc.cert_id
LEFT JOIN derivations     d  ON d.conclusion_id   = cl.id
LEFT JOIN artifacts       a  ON a.claim_id        = cl.id
WHERE cl.id = ANY(p_claim_ids);
$$ LANGUAGE sql STABLE;

COMMENT ON FUNCTION cert.export_bundle(BIGINT[]) IS
    'Portable proof bundle. Returns self-contained JSONB with claims, latest certificates, '
    'witnesses, derivation chains, and artifact specs. '
    'Consumer calls cert.verify() on each embedded claim_id to re-check without trusting producer. '
    'trunk_bundle_version=1 identifies this schema for forward compatibility.';
