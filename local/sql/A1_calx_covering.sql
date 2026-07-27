-- calx extension: covering systems of congruences.
--
-- A covering system is a finite set of congruences a_i (mod m_i) such that
-- every integer satisfies at least one.  22 Erdos problems are tagged
-- `covering systems` (10 open), and calx already had the CRT half of the
-- machinery -- crt, crt_combine, crt_decompose, crt_lift_step, mod_inverse,
-- progression_intersect -- but nothing that could answer the actual question:
-- does this set of congruences cover Z?
--
-- WHY THIS IS STRONGER THAN THE EXTREMAL LAYER.  In A0_calx_set_extremal.sql
-- the lower bound is witnessed and the upper bound is only search output, so
-- "maximal" is recorded as method rather than proved.  Covering has no such
-- asymmetry: the system covers Z if and only if it covers every residue class
-- mod L = lcm(m_i), because membership of n in a_i (mod m_i) depends only on
-- n mod L.  That is a FINITE, EXHAUSTIVE check, so `covers` here really is
-- decided, not estimated.  Both directions carry a witness:
--
--   covers = FALSE  ->  an explicit uncovered residue.  Check it by hand in
--                       seconds; it refutes on its own.
--   covers = TRUE   ->  the exhaustive residue sweep, whose cost is stated
--                       (L) so a reader can see it was affordable rather than
--                       sampled.
--
-- REFUSING IS PART OF THE CONTRACT.  L is an lcm and grows viciously -- five
-- moduli around 100 already exceed 10^8 residues.  Every function here refuses
-- above a stated bound and says `checked: false` rather than sweeping a
-- sampled subset and reporting a boolean.  A covering verifier that answers
-- "probably" is worse than one that answers "not within budget".

-- ---------------------------------------------------------------------------
-- 1. lcm over a modulus list, in numeric so it cannot silently overflow
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION calx.covering_lcm(p_mods BIGINT[])
RETURNS NUMERIC LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    acc NUMERIC := 1;
    m   BIGINT;
    g   NUMERIC;
    a   NUMERIC; b NUMERIC; t NUMERIC;
BEGIN
    IF p_mods IS NULL OR array_length(p_mods, 1) IS NULL THEN
        RAISE EXCEPTION 'no moduli given';
    END IF;
    FOREACH m IN ARRAY p_mods LOOP
        IF m < 1 THEN
            RAISE EXCEPTION 'modulus must be >= 1 (got %)', m;
        END IF;
        a := acc; b := m;
        WHILE b <> 0 LOOP t := b; b := a % b; a := t; END LOOP;  -- gcd
        g := a;
        acc := acc / g * m;
        -- Bail long before this becomes a memory problem rather than after.
        IF acc > 1e12 THEN RETURN acc; END IF;
    END LOOP;
    RETURN acc;
END $$;

COMMENT ON FUNCTION calx.covering_lcm(BIGINT[]) IS
    'lcm of the moduli, in NUMERIC. This is the period of the whole system: a '
    'set of congruences covers Z iff it covers every residue mod this value.';

-- ---------------------------------------------------------------------------
-- 2. The verifier
-- ---------------------------------------------------------------------------
-- Residues that NO congruence covers.  Returns at most p_limit of them; empty
-- means the system covers Z.  Built by marking covered classes rather than
-- testing each residue against each congruence: cost is sum(L/m_i), not L*k.
CREATE OR REPLACE FUNCTION calx.covering_uncovered(
    p_res BIGINT[], p_mods BIGINT[], p_limit INT DEFAULT 16,
    p_max_lcm NUMERIC DEFAULT 5e6)
RETURNS BIGINT[] LANGUAGE plpgsql STABLE AS $$
DECLARE
    L NUMERIC := calx.covering_lcm(p_mods);
    out_arr BIGINT[];
BEGIN
    IF array_length(p_res,1) IS DISTINCT FROM array_length(p_mods,1) THEN
        RAISE EXCEPTION 'residue/modulus arrays differ in length (% vs %)',
              array_length(p_res,1), array_length(p_mods,1);
    END IF;
    IF L > p_max_lcm THEN
        RAISE EXCEPTION 'lcm % exceeds budget % -- refusing to sweep. Raise '
                        'p_max_lcm deliberately if you mean to.', L, p_max_lcm;
    END IF;
    SELECT array_agg(r ORDER BY r) INTO out_arr FROM (
        SELECT r FROM generate_series(0, L::BIGINT - 1) r
        EXCEPT
        SELECT g FROM unnest(p_res, p_mods) AS t(ai, mi),
                    LATERAL generate_series(((ai % mi) + mi) % mi, L::BIGINT - 1, mi) g
        ORDER BY 1 LIMIT p_limit
    ) z;
    RETURN COALESCE(out_arr, ARRAY[]::BIGINT[]);
END $$;

CREATE OR REPLACE FUNCTION calx.is_covering_system(
    p_res BIGINT[], p_mods BIGINT[], p_max_lcm NUMERIC DEFAULT 5e6)
RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
    SELECT cardinality(calx.covering_uncovered(p_res, p_mods, 1, p_max_lcm)) = 0;
$$;

-- Full diagnostic.  Reports the properties the Erdos covering-system problems
-- actually turn on -- distinctness of moduli, the minimum modulus, exactness,
-- redundancy -- rather than a bare boolean.
CREATE OR REPLACE FUNCTION calx.covering_report(
    p_res BIGINT[], p_mods BIGINT[], p_max_lcm NUMERIC DEFAULT 5e6)
RETURNS JSONB LANGUAGE plpgsql STABLE AS $$
DECLARE
    L        NUMERIC := calx.covering_lcm(p_mods);
    k        INT := array_length(p_mods, 1);
    unc      BIGINT[];
    multi    BIGINT;
    redundant INT[] := ARRAY[]::INT[];
    i        INT;
    sub_r    BIGINT[]; sub_m BIGINT[];
BEGIN
    IF L > p_max_lcm THEN
        RETURN jsonb_build_object(
            'checked', false, 'reason', 'lcm exceeds budget',
            'lcm', L, 'budget', p_max_lcm, 'congruences', k,
            'reading', 'NOT a negative result -- the system was never swept');
    END IF;

    unc := calx.covering_uncovered(p_res, p_mods, 16, p_max_lcm);

    -- Exactness: is any residue covered by more than one congruence?
    SELECT count(*) INTO multi FROM (
        SELECT g FROM unnest(p_res, p_mods) AS t(ai, mi),
                    LATERAL generate_series(((ai % mi) + mi) % mi, L::BIGINT - 1, mi) g
         GROUP BY g HAVING count(*) > 1) z;

    -- Redundancy: which single congruences can be dropped with the rest still
    -- covering?  Only meaningful when the full system covers in the first place.
    IF cardinality(unc) = 0 AND k <= 24 THEN
        FOR i IN 1 .. k LOOP
            sub_r := p_res[1:i-1] || p_res[i+1:k];
            sub_m := p_mods[1:i-1] || p_mods[i+1:k];
            IF cardinality(sub_m) > 0
               AND calx.is_covering_system(sub_r, sub_m, p_max_lcm) THEN
                redundant := redundant || i;
            END IF;
        END LOOP;
    END IF;

    RETURN jsonb_build_object(
        'checked',          true,
        'congruences',      k,
        'lcm',              L,
        'covers_Z',         cardinality(unc) = 0,
        'uncovered_sample', to_jsonb(unc),
        'exact_cover',      (cardinality(unc) = 0 AND multi = 0),
        'multiply_covered', multi,
        'distinct_moduli',  (SELECT count(DISTINCT m) = k FROM unnest(p_mods) m),
        'min_modulus',      (SELECT min(m) FROM unnest(p_mods) m),
        'max_modulus',      (SELECT max(m) FROM unnest(p_mods) m),
        'redundant_indices', to_jsonb(redundant),
        'reading', 'covers_Z is DECIDED by exhaustive sweep of all residues mod '
                   'lcm, not sampled; a false is witnessed by uncovered_sample');
END $$;

COMMENT ON FUNCTION calx.covering_report(BIGINT[], BIGINT[], NUMERIC) IS
    'Decide whether congruences a_i mod m_i cover Z, with the properties the '
    'Erdos covering problems turn on: distinct moduli, minimum modulus, exact '
    'cover, redundancy. Refuses (checked:false) rather than sampling when the '
    'lcm exceeds budget.';

-- ---------------------------------------------------------------------------
-- 3. Store of named systems, mirroring calx.extremal_witness
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS calx.covering_system (
    name        TEXT PRIMARY KEY,
    residues    BIGINT[] NOT NULL,
    moduli      BIGINT[] NOT NULL,
    claimed_covers BOOLEAN NOT NULL,
    note        TEXT,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (cardinality(residues) = cardinality(moduli)),
    CHECK (cardinality(moduli) > 0)
);

COMMENT ON TABLE calx.covering_system IS
    'Named congruence systems. claimed_covers is what the SOURCE asserts; the '
    'verifier decides independently, and calx.covering_system_wrong lists any '
    'disagreement. Storing the claim separately from the verdict is the point.';

DROP VIEW IF EXISTS calx.covering_system_wrong;
CREATE VIEW calx.covering_system_wrong AS
SELECT c.name, c.claimed_covers,
       calx.is_covering_system(c.residues, c.moduli) AS actually_covers,
       calx.covering_uncovered(c.residues, c.moduli, 8)  AS uncovered_sample
  FROM calx.covering_system c
 WHERE calx.covering_lcm(c.moduli) <= 5e6
   AND calx.is_covering_system(c.residues, c.moduli) IS DISTINCT FROM c.claimed_covers;

COMMENT ON VIEW calx.covering_system_wrong IS
    'Should always be empty: every stored system whose asserted covering status '
    'disagrees with the exhaustive check. Systems too large to sweep are '
    'excluded rather than assumed correct.';
