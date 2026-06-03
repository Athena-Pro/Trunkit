-- Unified model, step 93: cert observability infrastructure.
--
-- Provides the plain-language status board (cert.board / cert.board_summary)
-- and the live-verification backing tables (subject_probe, live_build, crown
-- consensus).  All structural; no project-specific claims are registered here.
--
-- Project workspaces can extend the board CASE mapping by applying a local
-- override of this file via:  trunkit init --local <dir>
--
-- Three-valued throughout: valid / refuted / unverified.  Never a silent green.
-- Idempotent.

-- ---- Tier 1: subject-existence guard ----------------------------------------
CREATE TABLE IF NOT EXISTS cert.subject_probe (
    claim_id    integer PRIMARY KEY,
    path        text,
    exists      boolean,
    fingerprint text,
    build_evidence jsonb,
    checked_at  timestamptz
);

-- ---- Tier 2: live build/test ------------------------------------------------
CREATE TABLE IF NOT EXISTS cert.live_build (
    claim_id   integer PRIMARY KEY,
    tool       text,
    cmd        text,
    status     text,
    detail     text,
    checked_at timestamptz
);

-- ---- Plain-language status board --------------------------------------------
-- Maps subject_kind → human-readable area name.
-- Project workspaces that define additional subject_kinds can override this
-- view from local/sql/ (apply after core via trunkit init --local).

CREATE OR REPLACE VIEW cert.board AS
SELECT cl.id AS claim_id,
    CASE
        WHEN cl.subject_kind IN ('trunkit_method', 'cert_soundness')
             THEN 'Methods & self-checks'
        WHEN cl.subject_kind LIKE 'curry_%'
             THEN 'Curry (provenance)'
        WHEN cl.subject_kind IN (
                'homology_fact', 'sequence_homology',
                'factorial_homology', 'shared_prime_h2')
             THEN 'Math: homology'
        WHEN cl.subject_kind LIKE 'kan_%'
             OR cl.subject_kind IN (
                'lithon', 'shadow', 'moonshine', 'grading', 'bigrading',
                'chromatic', 'equipment', 'strata_tower', 'colimit_closure',
                'identity_decomposition', 'self_shadow', 'self_syzygy',
                'f1_radix', 'prime_members_functor', 'combined_scale',
                'combined_signature', 'developed_sequence',
                'omega_family', 'omega_family_succ')
             THEN 'Math: kan engines'
        ELSE 'Other'
    END AS area,
    s.status,
    CASE s.status
        WHEN 'valid'      THEN '✅ verified'
        WHEN 'refuted'    THEN '❌ failed'
        WHEN 'unverified' THEN '❓ unknown'
        WHEN 'unchecked'  THEN '⬜ not checked'
        WHEN 'error'      THEN '⚠ error'
        WHEN 'pass'       THEN '✅ verified'
        WHEN 'contested'  THEN '⚖ contested'
        ELSE s.status
    END AS plain,
    cl.statement
FROM cert.claim cl
JOIN cert.standing s ON s.claim_id = cl.id;

CREATE OR REPLACE VIEW cert.board_summary AS
SELECT
    area,
    count(*) FILTER (WHERE status IN ('valid', 'pass'))              AS verified,
    count(*) FILTER (WHERE status = 'refuted')                       AS failed,
    count(*) FILTER (WHERE status IN ('unverified', 'unchecked', 'error')) AS unknown,
    count(*)                                                          AS total
FROM cert.board
GROUP BY area
ORDER BY area;

-- ---- Crown consensus (OCTT partial closure) ---------------------------------
CREATE TABLE IF NOT EXISTS cert.evidence_vote (
    claim_id   integer NOT NULL REFERENCES cert.claim(id),
    voter      text    NOT NULL,
    vote       text    NOT NULL CHECK (vote IN ('valid','refuted','unverified')),
    evidence   jsonb,
    voted_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (claim_id, voter)
);

CREATE OR REPLACE VIEW cert.crown_consensus AS
SELECT
    claim_id,
    count(*) FILTER (WHERE vote = 'valid')      AS votes_valid,
    count(*) FILTER (WHERE vote = 'refuted')    AS votes_refuted,
    count(*) FILTER (WHERE vote = 'unverified') AS votes_unverified,
    count(*)                                    AS votes_total,
    CASE
        WHEN count(*) FILTER (WHERE vote = 'valid')    > count(*) FILTER (WHERE vote = 'refuted')
             AND count(*) FILTER (WHERE vote = 'valid') > count(*) FILTER (WHERE vote = 'unverified')
             THEN 'valid'
        WHEN count(*) FILTER (WHERE vote = 'refuted')  > count(*) FILTER (WHERE vote = 'valid')
             AND count(*) FILTER (WHERE vote = 'refuted') > count(*) FILTER (WHERE vote = 'unverified')
             THEN 'refuted'
        ELSE 'contested'
    END AS consensus
FROM cert.evidence_vote
GROUP BY claim_id;
