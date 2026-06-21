-- Unified model, step 96: holographic / succinct commitments (#1a).
--
-- The minimal, dependency-light half of "proof compression": commit to a
-- (possibly long) trace with a tiny Merkle root, so a consumer verifies ~32
-- bytes instead of carrying the whole execution log, and the producer reveals
-- leaves on demand. Reuses Postgres' built-in `sha256()` (core, no pgcrypto) —
-- the same hash discipline as the append-only ledger.
--
-- This is what makes a recurrence certificate (93) genuinely succinct: a 2–4
-- coefficient cert + a 32-byte commitment stand in for an arbitrarily long term
-- list, and any tamper to a term flips the root. Idempotent.

CREATE OR REPLACE FUNCTION cert.leaf_hash(s TEXT)
RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
    SELECT encode(sha256(convert_to(COALESCE(s,''), 'UTF8')), 'hex');
$$;

-- Binary Merkle root over ordered leaves (odd node duplicated). Empty -> hash("").
CREATE OR REPLACE FUNCTION cert.merkle_root(leaves TEXT[])
RETURNS TEXT LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE level TEXT[]; nxt TEXT[]; i INT; n INT;
BEGIN
    IF leaves IS NULL OR array_length(leaves,1) IS NULL THEN
        RETURN cert.leaf_hash('');
    END IF;
    level := ARRAY(SELECT cert.leaf_hash(x) FROM unnest(leaves) WITH ORDINALITY t(x,o) ORDER BY o);
    WHILE array_length(level,1) > 1 LOOP
        nxt := ARRAY[]::TEXT[];
        i := 1; n := array_length(level,1);
        WHILE i <= n LOOP
            IF i + 1 <= n THEN
                nxt := array_append(nxt, cert.leaf_hash(level[i] || level[i+1]));
            ELSE
                nxt := array_append(nxt, cert.leaf_hash(level[i] || level[i]));  -- duplicate odd tail
            END IF;
            i := i + 2;
        END LOOP;
        level := nxt;
    END LOOP;
    RETURN level[1];
END $$;

-- Compact commitment to a claim's verifiable content: a 64-hex-char root over
-- {claim id, statement, method, latest status, latest evidence}. Stable for a
-- given attested state; changes when a new certificate lands.
CREATE OR REPLACE FUNCTION cert.claim_commitment(p_claim_id BIGINT)
RETURNS TEXT LANGUAGE plpgsql STABLE AS $$
DECLARE s cert.standing%ROWTYPE; leaves TEXT[];
BEGIN
    SELECT * INTO s FROM cert.standing WHERE claim_id = p_claim_id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    leaves := ARRAY[
        'claim:'  || p_claim_id,
        'stmt:'   || COALESCE(s.statement,''),
        'method:' || COALESCE(s.method,''),
        'status:' || COALESCE(s.status,'unchecked'),
        'evid:'   || COALESCE(s.evidence::text,'')
    ];
    RETURN cert.merkle_root(leaves);
END $$;

-- Consumer-side check: does the recomputed commitment match a carried root?
CREATE OR REPLACE FUNCTION cert.verify_commitment(p_claim_id BIGINT, p_root TEXT)
RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
    SELECT cert.claim_commitment(p_claim_id) IS NOT DISTINCT FROM p_root;
$$;
