-- calx extension: finite integer SETS as first-class objects.
--
-- THE GAP THIS FILLS.  Every one of calx's 24 routines is about a single
-- integer (its factorization, divisors, residues) or about a sequence as a
-- vector of terms.  Searching calx/cert/kan/curry for
-- sidon|golomb|cover|sumset|extremal|ruler|basis returns exactly one hit
-- (calx.progression_intersect).  There is no machinery for a finite SET of
-- integers with an extremal property -- which is what a large, well-defined
-- part of the Erdos corpus is about:
--
--   61 problems tagged sidon sets / additive basis  (35 open, 16 A-numbers)
--   27 problems tagged arithmetic progressions      (15 open, 10 A-numbers)
--
-- and specifically #3 and #142 -- the two highest-fit targets in
-- kan.erdos_index_targets -- whose sequences A003002-A003005 are exactly
-- "largest subset of {1..n} with no k-term AP".
--
-- SEARCH IS UNTRUSTED; VERIFICATION IS EXACT.  The extremal search lives in
-- Python (ErdosIndex/tools/extremal_search.py) because branch-and-bound in
-- plpgsql would be painful and slow.  Nothing here trusts it.  What the search
-- returns is a WITNESS -- an explicit set -- and a witness is checkable in
-- O(k^2) integer comparisons, which is what the functions below do.  So:
--
--   LOWER bound  "a set of size s with this property exists"  -- CERTIFIABLE.
--                The witness proves it; cert.verify re-checks it from scratch.
--   UPPER bound  "no set of size s+1 exists"                  -- NOT certifiable
--                here.  It is the output of an exhaustive search, and the
--                claim records the method and the bound as evidence rather
--                than asserting a proof.  Do not read `is_maximal` as proved.
--
-- That asymmetry is the whole design.  Recording it wrongly would let an
-- exhaustive-search result masquerade as a theorem.

-- ---------------------------------------------------------------------------
-- 1. Exact verifiers.  Pure, no data dependency, cheap.
-- ---------------------------------------------------------------------------

-- A set is k-AP-free iff it contains no k-term arithmetic progression.
-- Implemented for the general k by scanning (first, common difference) pairs;
-- for k=3 this is the O(m^2) pair scan you would write by hand.
CREATE OR REPLACE FUNCTION calx.set_ap_violation(p_xs BIGINT[], p_k INT DEFAULT 3)
RETURNS BIGINT[] LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    s   BIGINT[];
    m   INT;
    i   INT; j INT; t INT;
    a   BIGINT; d BIGINT; x BIGINT;
    hit BIGINT[];
    ok  BOOLEAN;
BEGIN
    IF p_k < 3 THEN
        RAISE EXCEPTION 'k must be >= 3 (k=% is vacuous)', p_k;
    END IF;
    SELECT array_agg(DISTINCT u ORDER BY u) INTO s FROM unnest(p_xs) u;
    m := COALESCE(array_length(s, 1), 0);
    IF m < p_k THEN RETURN NULL; END IF;
    FOR i IN 1 .. m - 1 LOOP
        FOR j IN i + 1 .. m LOOP
            a := s[i]; d := s[j] - s[i];
            ok := TRUE; hit := ARRAY[a, s[j]];
            FOR t IN 2 .. p_k - 1 LOOP
                x := a + t * d;
                IF NOT (x = ANY(s)) THEN ok := FALSE; EXIT; END IF;
                IF t > 1 THEN hit := hit || x; END IF;
            END LOOP;
            IF ok THEN
                RETURN (SELECT array_agg(DISTINCT h ORDER BY h) FROM unnest(hit) h);
            END IF;
        END LOOP;
    END LOOP;
    RETURN NULL;
END $$;

COMMENT ON FUNCTION calx.set_ap_violation(BIGINT[], INT) IS
    'Returns a witnessing k-term arithmetic progression inside the set, or NULL '
    'if none exists. Returning the offending triple rather than a boolean makes '
    'a refutation self-explaining.';

CREATE OR REPLACE FUNCTION calx.set_is_ap_free(p_xs BIGINT[], p_k INT DEFAULT 3)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE AS $$
    SELECT calx.set_ap_violation(p_xs, p_k) IS NULL;
$$;

-- A Sidon set (B_2 set) has all pairwise sums distinct -- equivalently all
-- positive pairwise differences distinct.  Differences are used here because
-- the duplicate difference is the more legible witness.
CREATE OR REPLACE FUNCTION calx.set_sidon_violation(p_xs BIGINT[])
RETURNS JSONB LANGUAGE sql IMMUTABLE AS $$
    WITH s AS (SELECT DISTINCT u FROM unnest(p_xs) u),
    d AS (SELECT b.u - a.u AS diff, a.u AS lo, b.u AS hi
            FROM s a JOIN s b ON b.u > a.u)
    SELECT jsonb_build_object(
               'difference', diff,
               'pairs', jsonb_agg(jsonb_build_array(lo, hi) ORDER BY lo))
      FROM d GROUP BY diff HAVING count(*) > 1
     ORDER BY diff LIMIT 1;
$$;

CREATE OR REPLACE FUNCTION calx.set_is_sidon(p_xs BIGINT[])
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE AS $$
    SELECT calx.set_sidon_violation(p_xs) IS NULL;
$$;

COMMENT ON FUNCTION calx.set_is_sidon(BIGINT[]) IS
    'TRUE iff all positive pairwise differences are distinct (equivalently all '
    'pairwise sums). Duplicates are returned by calx.set_sidon_violation.';

-- ---------------------------------------------------------------------------
-- 2. Witness store
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS calx.extremal_witness (
    kind        TEXT   NOT NULL,
    n           INT    NOT NULL CHECK (n > 0),
    size        INT    NOT NULL CHECK (size >= 0),
    witness     BIGINT[] NOT NULL,
    is_maximal  BOOLEAN NOT NULL DEFAULT FALSE,
    method      TEXT   NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, n)
);

-- The kind vocabulary is a CHECK added by ALTER, not inlined above, because
-- CREATE TABLE IF NOT EXISTS silently skips EVERYTHING on an existing table --
-- constraints included, exactly as it skips column additions. Inlining it means
-- the vocabulary can never be widened on a live database, which bit immediately:
-- A003005 turned out to be the 6-term case and the original CHECK stopped at 5.
-- Parameterised on k so the next order needs no schema change at all.
ALTER TABLE calx.extremal_witness DROP CONSTRAINT IF EXISTS extremal_witness_kind_check;
ALTER TABLE calx.extremal_witness
    ADD CONSTRAINT extremal_witness_kind_check
    CHECK (kind = 'sidon' OR kind ~ '^ap_free_([3-9]|[1-9][0-9])$');

COMMENT ON TABLE calx.extremal_witness IS
    'Explicit extremal sets found by ErdosIndex/tools/extremal_search.py. The '
    'witness column is the evidence: cert re-verifies the property and the size '
    'from it, so a wrong search is caught. is_maximal records that the search '
    'was exhaustive for this n -- it is a method claim, NOT a proof of the upper '
    'bound, and no claim in this layer asserts otherwise.';

COMMENT ON COLUMN calx.extremal_witness.is_maximal IS
    'The search enumerated the whole space for this n. Trust it exactly as much '
    'as you trust the search code -- unlike `witness`, it is not re-checkable.';

-- Every stored witness must live inside {1..n}, be the size it claims, and
-- actually have the property.  Enforced as a view rather than a CHECK so that
-- a bad row is visible and refutable rather than un-insertable.
-- k is parsed out of the kind rather than switched on, so a new order is
-- covered by this check the moment it is inserted.  A CASE ladder would have
-- returned NULL for an unlisted kind, and NULL is not TRUE, so an unrecognised
-- witness would have been silently treated as valid -- the worst behaviour for
-- a view whose entire job is to be empty.
DROP VIEW IF EXISTS calx.extremal_witness_invalid;
CREATE VIEW calx.extremal_witness_invalid AS
WITH chk AS (
    SELECT w.*,
           CASE WHEN w.kind = 'sidon'
                THEN NOT calx.set_is_sidon(w.witness)
                ELSE NOT calx.set_is_ap_free(
                         w.witness,
                         substring(w.kind FROM 'ap_free_([0-9]+)')::INT)
           END AS property_fails
      FROM calx.extremal_witness w
)
SELECT kind, n, size,
       array_length(witness, 1) AS actual_size,
       (SELECT count(*) FROM unnest(witness) u WHERE u < 1 OR u > n) AS out_of_range,
       property_fails
  FROM chk
 WHERE COALESCE(array_length(witness, 1), 0) <> size
    OR EXISTS (SELECT 1 FROM unnest(witness) u WHERE u < 1 OR u > n)
    OR property_fails IS NOT FALSE;   -- NULL counts as invalid, deliberately

COMMENT ON VIEW calx.extremal_witness_invalid IS
    'Should always be empty. Any row here is a witness whose stored size, range '
    'or defining property does not survive re-checking -- i.e. the search lied.';
