-- calx extension: the three primitives the PARTIAL cohorts were missing.
--
-- Coverage analysis (2026-07-26) put 148 indexed Erdos problems in "partial":
-- a tagged mathematical object with only generic machinery behind it. Five of
-- those cohorts were each missing exactly one small, exactly-computable thing:
--
--   unit fractions        48 problems -- no Egyptian machinery at all, despite
--                                        being this repository's most-invested
--                                        area (#148, the Egyptian substrate).
--                                        math_gcd and math_lcm are not a splitter.
--   binomial coefficients 22 problems -- p-adic valuation of C(n,k) (Kummer)
--   factorials            21 problems -- v_p(n!) (Legendre)
--   irrationality +
--   diophantine approx.   29 problems -- continued fractions over Q
--
-- Everything here is exact integer or exact rational arithmetic. No floats
-- appear anywhere in this file, deliberately: these feed cert probes in the
-- exact_int domain, and a float would be quarantined by the exactness shield
-- (src/calx/sql/94) the moment it tried to record a valid certificate.
--
-- EACH FUNCTION HAS AN INDEPENDENT CROSS-CHECK, which is what claim N0 attests:
--   Legendre  v_p(n!)      vs a direct count of factors of p in 1..n
--   Kummer    v_p(C(n,k))  vs v_p(n!) - v_p(k!) - v_p((n-k)!)
--   Egyptian  the expansion sums EXACTLY back to a/b
--   CF        the partial quotients reconstruct a/b EXACTLY
-- Two independent routes to the same number is the cheapest real check there
-- is, and none of these needs an external source to be trusted.

-- ---------------------------------------------------------------------------
-- 1. p-adic valuations
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION calx.valuation_p(p_n NUMERIC, p_p BIGINT)
RETURNS INT LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE v INT := 0; m NUMERIC := abs(p_n);
BEGIN
    IF p_p < 2 THEN RAISE EXCEPTION 'p must be >= 2 (got %)', p_p; END IF;
    IF m = 0 THEN RAISE EXCEPTION 'v_p(0) is undefined (conventionally infinite)'; END IF;
    WHILE m % p_p = 0 LOOP m := m / p_p; v := v + 1; END LOOP;
    RETURN v;
END $$;

-- Legendre: v_p(n!) = sum_{i>=1} floor(n / p^i).
CREATE OR REPLACE FUNCTION calx.valuation_factorial(p_n BIGINT, p_p BIGINT)
RETURNS BIGINT LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE v BIGINT := 0; q NUMERIC := p_p;
BEGIN
    IF p_p < 2 THEN RAISE EXCEPTION 'p must be >= 2'; END IF;
    IF p_n < 0 THEN RAISE EXCEPTION 'n must be >= 0'; END IF;
    WHILE q <= p_n LOOP
        v := v + floor(p_n / q)::BIGINT;
        q := q * p_p;
    END LOOP;
    RETURN v;
END $$;

COMMENT ON FUNCTION calx.valuation_factorial(BIGINT, BIGINT) IS
    'Legendre''s formula. Exact for any n a BIGINT can hold -- note this never '
    'forms n!, which is the whole point: v_p(1000000!) is instant.';

-- Kummer: v_p(C(n,k)) is the number of carries when adding k and n-k in base p.
CREATE OR REPLACE FUNCTION calx.valuation_binomial(p_n BIGINT, p_k BIGINT, p_p BIGINT)
RETURNS BIGINT LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE carries BIGINT := 0; carry INT := 0;
        a BIGINT := p_k; b BIGINT := p_n - p_k; da INT; db INT;
BEGIN
    IF p_k < 0 OR p_k > p_n THEN RETURN 0; END IF;   -- C(n,k)=0 or 1 edge cases
    WHILE a > 0 OR b > 0 OR carry > 0 LOOP
        da := (a % p_p)::INT; db := (b % p_p)::INT;
        IF da + db + carry >= p_p THEN
            carries := carries + 1; carry := 1;
        ELSE
            carry := 0;
        END IF;
        a := a / p_p; b := b / p_p;
        EXIT WHEN a = 0 AND b = 0 AND carry = 0;
    END LOOP;
    RETURN carries;
END $$;

COMMENT ON FUNCTION calx.valuation_binomial(BIGINT, BIGINT, BIGINT) IS
    'Kummer''s theorem: v_p(C(n,k)) = number of carries adding k and n-k in '
    'base p. Cross-checked against Legendre in claim N0.';

-- ---------------------------------------------------------------------------
-- 2. Egyptian fractions
-- ---------------------------------------------------------------------------
-- Fibonacci-Sylvester greedy: repeatedly subtract 1/ceil(b/a).  Terminates
-- because the numerator strictly decreases.  Denominators grow doubly
-- exponentially, so both a term cap and a denominator cap are enforced and
-- exceeding either RAISES rather than returning a truncated expansion -- a
-- partial Egyptian expansion that looks complete is a wrong answer.
CREATE OR REPLACE FUNCTION calx.egyptian_greedy(
    p_num BIGINT, p_den BIGINT,
    p_max_terms INT DEFAULT 12, p_max_den NUMERIC DEFAULT 1e30)
RETURNS NUMERIC[] LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE a NUMERIC := p_num; b NUMERIC := p_den; d NUMERIC;
        out_arr NUMERIC[] := ARRAY[]::NUMERIC[]; g NUMERIC; x NUMERIC; y NUMERIC; t NUMERIC;
BEGIN
    IF p_num <= 0 OR p_den <= 0 THEN RAISE EXCEPTION 'need 0 < num/den'; END IF;
    IF p_num >= p_den THEN RAISE EXCEPTION 'need num/den < 1 (got %/%)', p_num, p_den; END IF;
    WHILE a > 0 LOOP
        IF cardinality(out_arr) >= p_max_terms THEN
            RAISE EXCEPTION 'greedy exceeded % terms on %/% -- refusing to '
                            'return a truncated expansion', p_max_terms, p_num, p_den;
        END IF;
        d := ceil(b / a);
        IF d > p_max_den THEN
            RAISE EXCEPTION 'denominator % exceeds cap % -- refusing to truncate', d, p_max_den;
        END IF;
        out_arr := out_arr || d;
        -- a/b - 1/d = (a*d - b) / (b*d), reduced
        x := a * d - b; y := b * d;
        IF x = 0 THEN RETURN out_arr; END IF;
        g := x; t := y;
        WHILE t <> 0 LOOP g := t; t := x % t; x := g; END LOOP;   -- gcd into g
        g := abs(g);
        a := (a * d - b) / g; b := (b * d) / g;
    END LOOP;
    RETURN out_arr;
END $$;

-- Exact check that a list of distinct unit fractions sums to num/den.
CREATE OR REPLACE FUNCTION calx.egyptian_verify(
    p_num BIGINT, p_den BIGINT, p_denoms NUMERIC[])
RETURNS JSONB LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE num NUMERIC := 0; den NUMERIC := 1; d NUMERIC; g NUMERIC; t NUMERIC; x NUMERIC;
BEGIN
    FOREACH d IN ARRAY p_denoms LOOP
        IF d <= 0 THEN RAISE EXCEPTION 'unit-fraction denominator must be positive'; END IF;
        num := num * d + den; den := den * d;              -- exact rational add
        x := num; t := den;
        WHILE t <> 0 LOOP g := t; t := x % t; x := g; END LOOP;
        g := abs(x); num := num / g; den := den / g;
    END LOOP;
    RETURN jsonb_build_object(
        'sums_to',   num::text || '/' || den::text,
        'target',    p_num::text || '/' || p_den::text,
        'exact',     (num * p_den = den * p_num),
        'terms',     cardinality(p_denoms),
        'distinct',  (SELECT count(DISTINCT u) = cardinality(p_denoms) FROM unnest(p_denoms) u),
        'max_denominator', (SELECT max(u) FROM unnest(p_denoms) u));
END $$;

COMMENT ON FUNCTION calx.egyptian_verify(BIGINT, BIGINT, NUMERIC[]) IS
    'Exact rational sum of unit fractions, compared to a target by cross '
    'multiplication -- never by division. Reports distinctness separately '
    'because an Egyptian representation is usually required to have distinct '
    'denominators and a greedy expansion does not guarantee it in general.';

-- The identity behind every Egyptian splitting argument: 1/d = 1/(d+1) + 1/(d(d+1)).
CREATE OR REPLACE FUNCTION calx.egyptian_split(p_d BIGINT)
RETURNS NUMERIC[] LANGUAGE sql IMMUTABLE AS $$
    SELECT ARRAY[(p_d + 1)::NUMERIC, (p_d::NUMERIC * (p_d + 1))];
$$;

-- ---------------------------------------------------------------------------
-- 3. Continued fractions over Q
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION calx.continued_fraction(p_num BIGINT, p_den BIGINT)
RETURNS BIGINT[] LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE a BIGINT := p_num; b BIGINT := p_den; q BIGINT; r BIGINT;
        out_arr BIGINT[] := ARRAY[]::BIGINT[];
BEGIN
    IF p_den = 0 THEN RAISE EXCEPTION 'zero denominator'; END IF;
    IF b < 0 THEN a := -a; b := -b; END IF;
    LOOP
        q := (a - ((a % b) + b) % b) / b;      -- floor division, correct for a<0
        out_arr := out_arr || q;
        r := a - q * b;
        EXIT WHEN r = 0;
        a := b; b := r;
    END LOOP;
    RETURN out_arr;
END $$;

-- Reconstruct the rational from its partial quotients: the round trip that
-- makes the expansion checkable rather than merely plausible.
CREATE OR REPLACE FUNCTION calx.cf_value(p_q BIGINT[])
RETURNS NUMERIC[] LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE i INT; num NUMERIC; den NUMERIC; t NUMERIC;
BEGIN
    IF cardinality(p_q) = 0 THEN RAISE EXCEPTION 'empty quotient list'; END IF;
    num := p_q[cardinality(p_q)]; den := 1;
    FOR i IN REVERSE cardinality(p_q) - 1 .. 1 LOOP
        t := num; num := p_q[i] * num + den; den := t;    -- num/den := q_i + den/num
    END LOOP;
    RETURN ARRAY[num, den];
END $$;

COMMENT ON FUNCTION calx.cf_value(BIGINT[]) IS
    'Fold partial quotients back to [numerator, denominator]. Inverse of '
    'calx.continued_fraction; claim N0 checks the round trip is exact.';
