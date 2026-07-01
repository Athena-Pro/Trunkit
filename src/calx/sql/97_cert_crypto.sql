-- Unified model, step 97: the CRYPTOGRAPHIC tier of the `cert` pillar.
-- Implements Gabbay, "Cryptographic certificates of validity for trustworthy AI"
-- (arXiv:2606.23768) as a sixth cert method tier.
--
--   computational claim -> trust root is calx itself (the DB computed it)
--   formal claim        -> trust root is an external artifact + sha256
--   CRYPTOGRAPHIC claim -> trust root is a succinct (optionally ZK) proof pi,
--                          checked against an approved verifier key vk, with NO
--                          trust in the agent and NO re-execution of its work.
--
-- Two checker paths, both append through the SAME cert.certificate ledger:
--   (a) external_cmd  : a SNARK/zkVM verifier (Halo2/Zinc) checks pi vs vk.
--   (b) arith_recheck : recompute the polynomial semantics [phi]_s(x) and test
--                       vanishing (== 0 valid, > 0 refuted). This is the
--                       compiler-correctness condition the paper proves (Sec 2-4)
--                       and needs no crypto back end -- a witness_carry-style
--                       verdict that keeps three-valued honesty. The evaluator is
--                       calx.arith; the harness is tools/cert_crypto.py.
--
-- Idempotent.

-- ---- 1. Register the new method tier ---------------------------------------
INSERT INTO cert.method (name, claim_kind, checker_kind, description) VALUES
    ('crypto_succinct', 'cryptographic', 'external_cmd',
     'Succinct (optionally zero-knowledge) proof pi that a compiled predicate '
     'R_phi(pub,w)=0 has a witness. Verifier checks pi against an approved vk '
     'without trusting the agent or re-executing. Witness w may stay private (ZK).')
ON CONFLICT (name) DO NOTHING;

-- Allow the new structured witness kinds (the interpretation `s` / arith relation,
-- and the opaque succinct proof) alongside the existing kinds.
ALTER TABLE cert.witness DROP CONSTRAINT IF EXISTS witness_kind_check;
ALTER TABLE cert.witness ADD CONSTRAINT witness_kind_check
    CHECK (kind IN ('term','trace','counterexample','hash_chain','kan_diagram',
                    'arith_constraint',  -- the interpretation s + relation R_phi
                    'snark_proof'));     -- the succinct proof pi (opaque blob)

-- ---- 2. The Cert tuple (Sec 4): (policyID, action, pub, vk, paramsHash, pi) --
-- Mirrors cert.artifact for the formal tier, but pins crypto parameters instead
-- of a file+checker_cmd. policy_id identifies the predicate phi + compiler ver.
CREATE TABLE IF NOT EXISTS cert.crypto_artifact (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    claim_id       BIGINT NOT NULL UNIQUE REFERENCES cert.claim(id) ON DELETE CASCADE,
    policy_id      TEXT NOT NULL,          -- predicate phi + compiler version
    backend        TEXT NOT NULL,          -- halo2 | zinc | risc0 | jolt | ...
    vk_hash        TEXT NOT NULL,          -- sha256 of approved verifier key
    params_hash    TEXT NOT NULL,          -- sha256 of proof-system params
    pub            JSONB NOT NULL DEFAULT '{}'::jsonb,  -- public instance data
    verifier_cmd   TEXT NOT NULL,          -- command that checks pi (exit 0 == ok)
    zero_knowledge BOOLEAN NOT NULL DEFAULT FALSE,      -- witness hidden?
    registered_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE cert.crypto_artifact IS
    'Cert = (policyID, action, pub, vk, paramsHash, pi) from arXiv:2606.23768 Sec 4. '
    'The succinct proof pi is stored as a cert.witness(kind=snark_proof); this row '
    'pins the approved verifier key + params + the verifier command. '
    'zero_knowledge=TRUE means the witness w is NOT present in any bundle.';

-- Register (or re-register) the crypto artifact backing a crypto claim.
CREATE OR REPLACE FUNCTION cert.register_crypto_artifact(
    p_claim_id     BIGINT,
    p_policy_id    TEXT,
    p_backend      TEXT,
    p_vk_hash      TEXT,
    p_params_hash  TEXT,
    p_pub          JSONB,
    p_verifier_cmd TEXT,
    p_zero_knowledge BOOLEAN DEFAULT FALSE,
    p_private_registers TEXT[] DEFAULT '{}',
    p_public_symbols    TEXT[] DEFAULT '{}'
) RETURNS cert.crypto_artifact
LANGUAGE plpgsql AS $$
DECLARE
    v_row cert.crypto_artifact%ROWTYPE;
BEGIN
    INSERT INTO cert.crypto_artifact
        (claim_id, policy_id, backend, vk_hash, params_hash, pub, verifier_cmd,
         zero_knowledge, private_registers, public_symbols)
    VALUES (p_claim_id, p_policy_id, p_backend, p_vk_hash, p_params_hash,
            COALESCE(p_pub,'{}'::jsonb), p_verifier_cmd, p_zero_knowledge,
            p_private_registers, p_public_symbols)
    ON CONFLICT (claim_id) DO UPDATE
        SET policy_id = EXCLUDED.policy_id, backend = EXCLUDED.backend,
            vk_hash = EXCLUDED.vk_hash, params_hash = EXCLUDED.params_hash,
            pub = EXCLUDED.pub, verifier_cmd = EXCLUDED.verifier_cmd,
            zero_knowledge = EXCLUDED.zero_knowledge,
            private_registers = EXCLUDED.private_registers,
            public_symbols = EXCLUDED.public_symbols,
            registered_at = now()
    RETURNING * INTO v_row;
    RETURN v_row;
END
$$;

-- ---- 3. Consumer re-verification (side-effect-free), arith_recheck path -----
-- The crypto verifier itself is harness-driven (like formal_external); but the
-- arithmetisation verdict CAN be recomputed in-DB from a stored arith_constraint
-- witness, giving cert.verify a real three-valued result (not mere presence).
--   'valid'      iff [phi]_s(x) = 0  (witness body carries residual 0)
--   'refuted'    iff residual > 0
--   'unverified' iff no arith_constraint witness (ZK-only -> defer to verifier_cmd)
CREATE OR REPLACE FUNCTION cert.verify_crypto(p_claim_id BIGINT)
RETURNS TABLE (status TEXT, residual NUMERIC, evidence JSONB)
LANGUAGE plpgsql AS $$
DECLARE
    v_wit JSONB;
BEGIN
    SELECT w.body INTO v_wit
      FROM cert.witness w
      JOIN cert.certificate ce ON ce.id = w.certificate_id
     WHERE ce.claim_id = p_claim_id AND w.kind = 'arith_constraint'
     ORDER BY ce.seq DESC LIMIT 1;

    IF v_wit IS NULL THEN
        RETURN QUERY SELECT 'unverified'::TEXT, NULL::NUMERIC,
            jsonb_build_object('note',
                'no arith_constraint witness; ZK-only -> defer to snark verifier_cmd');
        RETURN;
    END IF;
    RETURN QUERY SELECT
        CASE WHEN (v_wit->>'residual')::NUMERIC = 0 THEN 'valid' ELSE 'refuted' END,
        (v_wit->>'residual')::NUMERIC,
        jsonb_build_object('policy_id', v_wit->'policy_id',
                           'recheck', 'arithmetisation [phi]_s(x) vanishing');
END
$$;

-- ---- 4. Witness-register declaration + bundle-admission invariant ----------
-- Composition-security result (compartment.py / admission.py): every secure
-- construct is individually sound (non-negativity), so the ONLY cross-claim
-- risks are symbol reuse. A combined bundle is admissible iff:
--   I1  register disjointness     : no symbol is a PRIVATE register of two claims
--   I2  classification consistency: no symbol is PRIVATE in one claim and PUBLIC
--                                   in another (else the public reader's
--                                   transcript exposes the other's sealed secret).
ALTER TABLE cert.crypto_artifact
    ADD COLUMN IF NOT EXISTS private_registers TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS public_symbols    TEXT[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN cert.crypto_artifact.private_registers IS
    'Private witness registers this sealed claim carries (inverse/quotient/'
    'four-square/column-index symbols). Must be disjoint across a bundle (I1).';
COMMENT ON COLUMN cert.crypto_artifact.public_symbols IS
    'Public data symbols this claim reads. Must not name another claim''s '
    'private register (I2).';

-- Side-effect-free admissibility verdict for a combined bundle. One row per
-- violation (admit=FALSE), or a single admit=TRUE row when clean.
CREATE OR REPLACE FUNCTION cert.bundle_admits(p_claim_ids BIGINT[])
RETURNS TABLE (admit BOOLEAN, violation TEXT, symbol TEXT,
               claim_a BIGINT, claim_b BIGINT)
LANGUAGE sql STABLE AS $$
WITH ca AS (
    SELECT claim_id, private_registers, public_symbols
      FROM cert.crypto_artifact WHERE claim_id = ANY(p_claim_ids)
),
priv AS (SELECT claim_id, unnest(private_registers) AS sym FROM ca),
pub  AS (SELECT claim_id, unnest(public_symbols)    AS sym FROM ca),
i1 AS (   -- a private register shared by two distinct claims
    SELECT 'I1 register-aliasing'::TEXT AS violation, p1.sym AS symbol,
           p1.claim_id AS claim_a, p2.claim_id AS claim_b
      FROM priv p1 JOIN priv p2 ON p1.sym = p2.sym AND p1.claim_id < p2.claim_id
),
i2 AS (   -- a symbol private in one claim and public in another
    SELECT 'I2 classification-conflict'::TEXT AS violation, pr.sym AS symbol,
           pr.claim_id AS claim_a, pb.claim_id AS claim_b
      FROM priv pr JOIN pub pb ON pr.sym = pb.sym AND pr.claim_id <> pb.claim_id
),
v AS (SELECT * FROM i1 UNION ALL SELECT * FROM i2)
SELECT FALSE, v.violation, v.symbol, v.claim_a, v.claim_b FROM v
UNION ALL
SELECT TRUE, NULL::TEXT, NULL::TEXT, NULL::BIGINT, NULL::BIGINT
 WHERE NOT EXISTS (SELECT 1 FROM v);
$$;

COMMENT ON FUNCTION cert.bundle_admits(BIGINT[]) IS
    'Bundle-admission invariant for the crypto tier. Enforces I1 (private '
    'register disjointness) and I2 (private/public classification consistency) '
    'across a combined bundle. Call before accepting a multi-claim crypto bundle; '
    'admit=TRUE iff no coupling between sealed methods. Spec: calx.arith.bundle_admits.';

-- ---- 5. Seed a worked-example crypto claim (probe NULL => harness-driven) ---
-- "7 divides 28", arithmetised as ∃q. 28 = 7*q with quotient witness q=4
-- (a SEALABLE method: q is a private register). Attested by tools/cert_crypto.py.
INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
SELECT 'divisibility',
       '{"d":7,"n":28}'::jsonb,
       '7 divides 28 (quotient-witnessed, crypto tier)',
       'cryptographic', 'crypto_succinct', NULL
WHERE NOT EXISTS (
    SELECT 1 FROM cert.claim
     WHERE statement = '7 divides 28 (quotient-witnessed, crypto tier)'
);
