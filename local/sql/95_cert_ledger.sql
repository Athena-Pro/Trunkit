-- Unified model, step 95: immutability + entanglement (the hash-chained ledger).
--
-- Turns the append-only-BY-CONVENTION cert ledger into a cryptographically
-- tamper-evident one, and entangles records into a Merkle DAG that spans the
-- cert and curry layers (and, by value, the Nerode/Porter handoff envelopes).
--
-- IMMUTABILITY
--   * Every cert.certificate / cert.witness / curry.inferences row gets a
--     content hash; cert rows also carry prev_hash (a linear chain) so any
--     insertion/removal/reordering/edit breaks the chain.
--   * BEFORE UPDATE/DELETE triggers make append-only a LAW, not a convention.
--   * cert.verify_chain() recomputes the whole chain and reports the first break.
--
-- ENTANGLEMENT (what each certificate's hash commits to)
--   content  = claim_id, seq, status, evidence, valid_under
--   ⊗ curry  = the row_hash of its checker_inference (provenance)
--   ⊗ time   = prev_hash (the previous ledger record)
--   ⊗ proof  = premise_hashes[] (snapshot of its derivation premises' hashes)
--   witness  ⊗ its parent certificate's row_hash
--   external ⊗ cert.anchor_external() folds a foreign hash (a Nerode handoff
--             envelope, a TEL root, a git tag) into the ledger by value.
--
-- Hashing is SHA-256 (built-in since PG11, no pgcrypto) over a canonical-JSON
-- preimage defined by cert.canonical_json so an off-DB consumer (calx.ledger)
-- reproduces it byte-for-byte. Separator: US (chr(31)).
--
-- Idempotent. Backfill runs once (existing rows), BEFORE the append-only
-- triggers exist, so re-apply is a no-op.

-- ---------------------------------------------------------------------------
-- 0. canonical JSON + hash primitives (shared spec with calx.ledger.py)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION cert.hash_text(p TEXT)
RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
    SELECT encode(sha256(convert_to(coalesce(p, ''), 'UTF8')), 'hex')
$$;

-- Deterministic JSON serialization. Object keys ordered by (utf8 byte length,
-- utf8 bytes); arrays keep order; compact (no spaces). Reproduced exactly in
-- calx.ledger._canonical_json. NOTE: integers/booleans/strings are byte-stable
-- across SQL/Python; non-integer numbers are canonicalization-sensitive.
CREATE OR REPLACE FUNCTION cert.canonical_json(j JSONB)
RETURNS TEXT LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE t TEXT; res TEXT;
BEGIN
    IF j IS NULL THEN RETURN 'null'; END IF;
    t := jsonb_typeof(j);
    IF t = 'object' THEN
        SELECT coalesce('{' || string_agg(
                   to_json(k)::text || ':' || cert.canonical_json(j -> k),
                   ',' ORDER BY octet_length(k), convert_to(k, 'UTF8')
               ) || '}', '{}')
          INTO res FROM jsonb_object_keys(j) AS k;
        RETURN res;
    ELSIF t = 'array' THEN
        SELECT coalesce('[' || string_agg(cert.canonical_json(e.value), ','
                   ORDER BY e.ord) || ']', '[]')
          INTO res FROM jsonb_array_elements(j) WITH ORDINALITY AS e(value, ord);
        RETURN res;
    ELSIF t = 'string' THEN
        RETURN to_json(j #>> '{}')::text;
    ELSE
        RETURN j::text;   -- number / boolean / null
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 1. ledger columns
-- ---------------------------------------------------------------------------

ALTER TABLE curry.inferences  ADD COLUMN IF NOT EXISTS row_hash TEXT;
ALTER TABLE cert.certificate  ADD COLUMN IF NOT EXISTS row_hash       TEXT;
ALTER TABLE cert.certificate  ADD COLUMN IF NOT EXISTS prev_hash      TEXT;
ALTER TABLE cert.certificate  ADD COLUMN IF NOT EXISTS premise_hashes TEXT[];
ALTER TABLE cert.witness      ADD COLUMN IF NOT EXISTS row_hash TEXT;

-- ---------------------------------------------------------------------------
-- 2. pure hash functions (used by both the insert triggers and verify_chain)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION cert.inference_row_hash(
    p_inference_id TEXT, p_model_name TEXT, p_model_version INT, p_metadata JSONB)
RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
    SELECT cert.hash_text(
        'trunkit-inf-v1' || chr(31) || coalesce(p_inference_id, '')
        || chr(31) || coalesce(p_model_name, '')
        || chr(31) || coalesce(p_model_version::text, '')
        || chr(31) || cert.canonical_json(coalesce(p_metadata, '{}'::jsonb)))
$$;

CREATE OR REPLACE FUNCTION cert.certificate_row_hash(
    p_claim_id BIGINT, p_seq INT, p_status TEXT, p_evidence JSONB,
    p_valid_under JSONB, p_inf_hash TEXT, p_prev TEXT, p_premises TEXT[])
RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
    SELECT cert.hash_text(
        'trunkit-cert-v1' || chr(31) || p_claim_id || chr(31) || p_seq
        || chr(31) || p_status
        || chr(31) || cert.canonical_json(coalesce(p_evidence, '{}'::jsonb))
        || chr(31) || cert.canonical_json(coalesce(p_valid_under, '{}'::jsonb))
        || chr(31) || coalesce(p_inf_hash, '')
        || chr(31) || coalesce(p_prev, '')
        || chr(31) || coalesce(array_to_string(p_premises, ','), ''))
$$;

CREATE OR REPLACE FUNCTION cert.witness_row_hash(
    p_cert_id BIGINT, p_cert_hash TEXT, p_kind TEXT, p_body JSONB)
RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
    SELECT cert.hash_text(
        'trunkit-witness-v1' || chr(31) || p_cert_id
        || chr(31) || coalesce(p_cert_hash, '')
        || chr(31) || coalesce(p_kind, '')
        || chr(31) || cert.canonical_json(coalesce(p_body, '{}'::jsonb)))
$$;

-- Snapshot of a conclusion's derivation-premise hashes (latest cert per premise).
CREATE OR REPLACE FUNCTION cert.premise_hashes(p_conclusion BIGINT)
RETURNS TEXT[] LANGUAGE sql STABLE AS $$
    SELECT coalesce(array_agg(ph ORDER BY pid), '{}')
    FROM (
        SELECT u.pid,
               (SELECT c.row_hash FROM cert.certificate c
                 WHERE c.claim_id = u.pid ORDER BY c.seq DESC LIMIT 1) AS ph
          FROM cert.derivation d, unnest(d.premise_ids) AS u(pid)
         WHERE d.conclusion_id = p_conclusion
    ) s
    WHERE ph IS NOT NULL
$$;

-- ---------------------------------------------------------------------------
-- 3. insert triggers (compute the chain as rows are appended)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION cert.tg_inference_hash() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.row_hash := cert.inference_row_hash(
        NEW.inference_id, NEW.model_name, NEW.model_version, NEW.metadata);
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION cert.tg_certificate_hash() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_prev TEXT; v_inf TEXT;
BEGIN
    -- serialize ledger appends so prev_hash is well-defined under concurrency
    PERFORM pg_advisory_xact_lock(hashtext('cert.ledger'));
    SELECT row_hash INTO v_prev FROM cert.certificate ORDER BY id DESC LIMIT 1;
    SELECT row_hash INTO v_inf  FROM curry.inferences
        WHERE inference_id = NEW.checker_inference_id;
    NEW.premise_hashes := cert.premise_hashes(NEW.claim_id);
    NEW.prev_hash := v_prev;
    NEW.row_hash  := cert.certificate_row_hash(
        NEW.claim_id, NEW.seq, NEW.status, NEW.evidence, NEW.valid_under,
        v_inf, v_prev, NEW.premise_hashes);
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION cert.tg_witness_hash() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE v_cert_hash TEXT;
BEGIN
    SELECT row_hash INTO v_cert_hash FROM cert.certificate WHERE id = NEW.certificate_id;
    NEW.row_hash := cert.witness_row_hash(NEW.certificate_id, v_cert_hash, NEW.kind, NEW.body);
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS curry_inference_hash ON curry.inferences;
CREATE TRIGGER curry_inference_hash BEFORE INSERT ON curry.inferences
    FOR EACH ROW EXECUTE FUNCTION cert.tg_inference_hash();

DROP TRIGGER IF EXISTS cert_certificate_hash ON cert.certificate;
CREATE TRIGGER cert_certificate_hash BEFORE INSERT ON cert.certificate
    FOR EACH ROW EXECUTE FUNCTION cert.tg_certificate_hash();

DROP TRIGGER IF EXISTS cert_witness_hash ON cert.witness;
CREATE TRIGGER cert_witness_hash BEFORE INSERT ON cert.witness
    FOR EACH ROW EXECUTE FUNCTION cert.tg_witness_hash();

-- ---------------------------------------------------------------------------
-- 4. one-time backfill (runs before the append-only triggers are created)
-- ---------------------------------------------------------------------------

UPDATE curry.inferences
   SET row_hash = cert.inference_row_hash(inference_id, model_name, model_version, metadata)
 WHERE row_hash IS NULL;

DO $$
DECLARE r RECORD; v_prev TEXT := NULL; v_inf TEXT; v_prem TEXT[]; v_hash TEXT;
BEGIN
    FOR r IN SELECT * FROM cert.certificate ORDER BY id LOOP
        IF r.row_hash IS NOT NULL THEN
            v_prev := r.row_hash;          -- already hashed; carry as predecessor
            CONTINUE;
        END IF;
        SELECT row_hash INTO v_inf FROM curry.inferences
            WHERE inference_id = r.checker_inference_id;
        v_prem := cert.premise_hashes(r.claim_id);
        v_hash := cert.certificate_row_hash(
            r.claim_id, r.seq, r.status, r.evidence, r.valid_under, v_inf, v_prev, v_prem);
        UPDATE cert.certificate
           SET row_hash = v_hash, prev_hash = v_prev, premise_hashes = v_prem
         WHERE id = r.id;
        v_prev := v_hash;
    END LOOP;
END $$;

UPDATE cert.witness w
   SET row_hash = cert.witness_row_hash(
        w.certificate_id,
        (SELECT c.row_hash FROM cert.certificate c WHERE c.id = w.certificate_id),
        w.kind, w.body)
 WHERE w.row_hash IS NULL;

-- ---------------------------------------------------------------------------
-- 5. append-only enforcement (immutability is now a LAW)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION cert.reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        '% forbidden: %.% is an append-only ledger (cert immutability law)',
        TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
        USING HINT = 'Append a new certificate (re-check); never mutate history.';
END $$;

DROP TRIGGER IF EXISTS cert_certificate_append_only ON cert.certificate;
CREATE TRIGGER cert_certificate_append_only BEFORE UPDATE OR DELETE ON cert.certificate
    FOR EACH ROW EXECUTE FUNCTION cert.reject_mutation();

DROP TRIGGER IF EXISTS cert_witness_append_only ON cert.witness;
CREATE TRIGGER cert_witness_append_only BEFORE UPDATE OR DELETE ON cert.witness
    FOR EACH ROW EXECUTE FUNCTION cert.reject_mutation();

DROP TRIGGER IF EXISTS curry_inferences_append_only ON curry.inferences;
CREATE TRIGGER curry_inferences_append_only BEFORE UPDATE OR DELETE ON curry.inferences
    FOR EACH ROW EXECUTE FUNCTION cert.reject_mutation();

-- ---------------------------------------------------------------------------
-- 6. ledger introspection + integrity check
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION cert.ledger_root() RETURNS TEXT
LANGUAGE sql STABLE AS $$
    SELECT row_hash FROM cert.certificate ORDER BY id DESC LIMIT 1
$$;

CREATE OR REPLACE FUNCTION cert.ledger_height() RETURNS BIGINT
LANGUAGE sql STABLE AS $$
    SELECT count(*)::bigint FROM cert.certificate
$$;

-- Recompute the entire chain; return the first break (or ok=true).
CREATE OR REPLACE FUNCTION cert.verify_chain()
RETURNS TABLE (ok BOOLEAN, checked BIGINT, broken_at BIGINT, reason TEXT)
LANGUAGE plpgsql STABLE AS $$
DECLARE r RECORD; v_prev TEXT := NULL; v_inf TEXT; v_calc TEXT; n BIGINT := 0;
BEGIN
    FOR r IN SELECT * FROM cert.certificate ORDER BY id LOOP
        n := n + 1;
        IF r.prev_hash IS DISTINCT FROM v_prev THEN
            RETURN QUERY SELECT false, n, r.id,
                format('prev_hash link broken at certificate id %s', r.id);
            RETURN;
        END IF;
        SELECT row_hash INTO v_inf FROM curry.inferences
            WHERE inference_id = r.checker_inference_id;
        v_calc := cert.certificate_row_hash(
            r.claim_id, r.seq, r.status, r.evidence, r.valid_under,
            v_inf, v_prev, r.premise_hashes);
        IF v_calc IS DISTINCT FROM r.row_hash THEN
            RETURN QUERY SELECT false, n, r.id,
                format('content hash mismatch at certificate id %s (row altered)', r.id);
            RETURN;
        END IF;
        v_prev := r.row_hash;
    END LOOP;
    RETURN QUERY SELECT true, n, NULL::bigint, 'chain intact'::text;
END $$;

COMMENT ON FUNCTION cert.verify_chain() IS
    'Recompute the cert hash chain from primitives and report the first broken '
    'link or altered row. ok=true means the ledger is internally consistent.';

-- ---------------------------------------------------------------------------
-- 7. cross-layer entanglement by value (Nerode envelopes / TEL roots / git)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS cert.external_anchor (
    id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    anchor_kind           TEXT NOT NULL,   -- handoff_envelope | tel_root | git | ...
    anchor_hash           TEXT NOT NULL,
    ledger_root_at_anchor TEXT,            -- cert head when this anchor was folded in
    note                  TEXT,
    anchored_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Fold a foreign hash into the ledger's timeline. The Nerode handoff envelope
-- embeds cert.ledger_root() when packed (see nerode.precache); calling this with
-- the envelope's own hash closes the loop — proofs and handoffs entangled by value.
CREATE OR REPLACE FUNCTION cert.anchor_external(p_kind TEXT, p_hash TEXT, p_note TEXT DEFAULT NULL)
RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE v_id BIGINT;
BEGIN
    INSERT INTO cert.external_anchor (anchor_kind, anchor_hash, ledger_root_at_anchor, note)
    VALUES (p_kind, p_hash, cert.ledger_root(), p_note)
    RETURNING id INTO v_id;
    RETURN v_id;
END $$;

DROP TRIGGER IF EXISTS cert_external_anchor_append_only ON cert.external_anchor;
CREATE TRIGGER cert_external_anchor_append_only BEFORE UPDATE OR DELETE ON cert.external_anchor
    FOR EACH ROW EXECUTE FUNCTION cert.reject_mutation();

-- ---------------------------------------------------------------------------
-- 8. export_bundle v2 — carry the chain so a consumer verifies it offline.
--    (Replaces step 87's v1; lives here because it needs the hash columns.)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION cert.export_bundle(p_claim_ids BIGINT[])
RETURNS JSONB LANGUAGE sql STABLE AS $$
WITH
latest_cert AS (
    SELECT DISTINCT ON (claim_id)
           claim_id, id AS cert_id, seq, status, evidence, valid_under, checked_at,
           row_hash, prev_hash, premise_hashes, checker_inference_id
      FROM cert.certificate
     WHERE claim_id = ANY(p_claim_ids)
     ORDER BY claim_id, seq DESC
),
latest_witness AS (
    SELECT DISTINCT ON (w.certificate_id)
           w.certificate_id, w.kind AS witness_kind, w.body AS witness_body,
           w.row_hash AS witness_row_hash
      FROM cert.witness w
      JOIN latest_cert lc ON lc.cert_id = w.certificate_id
     ORDER BY w.certificate_id, w.id DESC
),
derivations AS (
    SELECT d.conclusion_id, d.premise_ids, d.rule, d.asserted_at
      FROM cert.derivation d
     WHERE d.conclusion_id = ANY(p_claim_ids)
),
artifacts AS (
    SELECT a.claim_id, a.kind AS artifact_kind, a.path,
           a.sha256, a.checker_cmd, a.registered_at
      FROM cert.artifact a
     WHERE a.claim_id = ANY(p_claim_ids)
)
SELECT jsonb_build_object(
    'trunk_bundle_version', 2,
    'exported_at', now(),
    'ledger_root', cert.ledger_root(),
    'ledger_height', cert.ledger_height(),
    'claims', jsonb_agg(
        jsonb_build_object(
            'claim',       to_jsonb(cl),
            'certificate', jsonb_build_object(
                               'cert_id',        lc.cert_id,
                               'seq',            lc.seq,
                               'status',         lc.status,
                               'evidence',       lc.evidence,
                               'valid_under',    lc.valid_under,
                               'checked_at',     lc.checked_at,
                               'row_hash',       lc.row_hash,
                               'prev_hash',      lc.prev_hash,
                               'premise_hashes', to_jsonb(lc.premise_hashes),
                               'inference_hash', (SELECT i.row_hash FROM curry.inferences i
                                                   WHERE i.inference_id = lc.checker_inference_id)
                           ),
            'witness',     CASE WHEN lw.witness_kind IS NOT NULL
                           THEN jsonb_build_object('kind', lw.witness_kind, 'body', lw.witness_body,
                                                   'row_hash', lw.witness_row_hash)
                           ELSE NULL END,
            'derivation',  CASE WHEN d.conclusion_id IS NOT NULL
                           THEN jsonb_build_object('premise_ids', to_jsonb(d.premise_ids),
                                                   'rule', d.rule, 'asserted_at', d.asserted_at)
                           ELSE NULL END,
            'artifact',    CASE WHEN a.claim_id IS NOT NULL
                           THEN jsonb_build_object('kind', a.artifact_kind, 'path', a.path,
                                                   'sha256', a.sha256, 'checker_cmd', a.checker_cmd,
                                                   'registered_at', a.registered_at)
                           ELSE NULL END
        )
    )
)
FROM cert.claim cl
JOIN latest_cert  lc ON lc.claim_id = cl.id
LEFT JOIN latest_witness lw ON lw.certificate_id = lc.cert_id
LEFT JOIN derivations    d  ON d.conclusion_id   = cl.id
LEFT JOIN artifacts      a  ON a.claim_id        = cl.id
WHERE cl.id = ANY(p_claim_ids);
$$;

COMMENT ON FUNCTION cert.export_bundle(BIGINT[]) IS
    'Portable proof bundle v2. Carries per-certificate row_hash/prev_hash/'
    'premise_hashes/inference_hash + witness row_hash + the ledger_root, so a '
    'consumer (calx.ledger.verify_chain) re-checks content integrity and proof '
    'entanglement offline. cert.verify() still re-checks the claims themselves.';
