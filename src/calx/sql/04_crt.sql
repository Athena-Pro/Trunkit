-- =============================================================================
--  calx — Chinese Remainder Theorem machinery
--
--  Layers:
--    1. ext_gcd                  — extended Euclidean algorithm
--    2. mod_inverse              — modular inverse via ext_gcd
--    3. crt_combine              — pairwise CRT step
--    4. crt                      — k-way CRT (left fold of crt_combine)
--    5. wheel_spokes             — residues coprime to primorial(k)
--    6. progression_intersect    — general AP intersection (handles gcd > 1)
--    7. crt_decompose            — n → (n mod p^e) coordinates from factorizations
--    8. crt_reconstruct          — coordinates → n
-- =============================================================================


-- ─────────────────────────────────────────────────────────────────────────────
-- 1. EXTENDED EUCLIDEAN
--    Returns (g, s, t) such that a·s + b·t = g = gcd(a, b)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION ext_gcd(a BIGINT, b BIGINT)
RETURNS TABLE (g BIGINT, s BIGINT, t BIGINT)
LANGUAGE plpgsql IMMUTABLE STRICT
AS $$
DECLARE
    old_r BIGINT := a;  r     BIGINT := b;
    old_s BIGINT := 1;  s_cur BIGINT := 0;
    old_t BIGINT := 0;  t_cur BIGINT := 1;
    q     BIGINT;
    tmp   BIGINT;
BEGIN
    WHILE r != 0 LOOP
        q   := old_r / r;

        tmp := r;       r     := old_r - q * r;     old_r := tmp;
        tmp := s_cur;   s_cur := old_s - q * s_cur; old_s := tmp;
        tmp := t_cur;   t_cur := old_t - q * t_cur; old_t := tmp;
    END LOOP;

    RETURN QUERY SELECT old_r, old_s, old_t;
END $$;

COMMENT ON FUNCTION ext_gcd IS
    'Extended Euclidean: returns (g, s, t) with a·s + b·t = gcd(a,b).';


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. MODULAR INVERSE
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION mod_inverse(a BIGINT, m BIGINT)
RETURNS BIGINT
LANGUAGE plpgsql IMMUTABLE STRICT
AS $$
DECLARE
    g BIGINT;
    s BIGINT;
BEGIN
    SELECT eg.g, eg.s INTO g, s FROM ext_gcd(((a % m) + m) % m, m) eg;

    IF g != 1 THEN
        RAISE EXCEPTION 'mod_inverse: gcd(%, %) = % ≠ 1; inverse does not exist',
            a, m, g;
    END IF;

    RETURN ((s % m) + m) % m;
END $$;

COMMENT ON FUNCTION mod_inverse IS
    'Modular inverse of a mod m via extended Euclidean. Requires gcd(a,m)=1.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. PAIRWISE CRT COMBINE
--    x ≡ a (mod m), x ≡ b (mod n)  →  x ≡ ? (mod m·n)
--    Formula: x = a + m · ((b - a) · m⁻¹ mod n)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION crt_combine(a BIGINT, m BIGINT, b BIGINT, n BIGINT)
RETURNS TABLE (x BIGINT, modulus BIGINT)
LANGUAGE plpgsql IMMUTABLE STRICT
AS $$
DECLARE
    inv_m BIGINT;
    x_val BIGINT;
    mn    BIGINT;
BEGIN
    mn    := m * n;
    inv_m := mod_inverse(m, n);

    x_val := (a + m * (((b - a) * inv_m) % n)) % mn;
    x_val := ((x_val % mn) + mn) % mn;

    RETURN QUERY SELECT x_val, mn;
END $$;

COMMENT ON FUNCTION crt_combine IS
    'Pairwise CRT step: merges (a mod m, b mod n) into one congruence mod m·n.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 4. FULL CRT — k-way fold
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION crt(remainders BIGINT[], moduli BIGINT[])
RETURNS BIGINT
LANGUAGE plpgsql IMMUTABLE STRICT
AS $$
DECLARE
    x     BIGINT;
    m     BIGINT;
    new_x BIGINT;
    new_m BIGINT;
    i     INTEGER;
BEGIN
    IF array_length(remainders, 1) != array_length(moduli, 1) THEN
        RAISE EXCEPTION 'crt: remainders and moduli arrays must be the same length';
    END IF;

    x := remainders[1];
    m := moduli[1];

    FOR i IN 2 .. array_length(moduli, 1) LOOP
        SELECT c.x, c.modulus INTO new_x, new_m
        FROM crt_combine(x, m, remainders[i], moduli[i]) c;

        x := new_x;
        m := new_m;
    END LOOP;

    RETURN x;
END $$;

COMMENT ON FUNCTION crt IS
    'Full CRT over k congruences. Returns unique x in [0, Π mᵢ).';


-- ─────────────────────────────────────────────────────────────────────────────
-- 5. WHEEL SIEVE SPOKES
--    Residues mod primorial(k) coprime to the first k primes.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION wheel_spokes(k INTEGER)
RETURNS TABLE (spoke BIGINT, wheel_modulus BIGINT)
LANGUAGE plpgsql STABLE
AS $$
DECLARE
    primorial BIGINT;
BEGIN
    SELECT EXP(SUM(LN(p)))::BIGINT INTO primorial
    FROM (SELECT p FROM primes ORDER BY discovered_order LIMIT k) sub;

    RETURN QUERY
    SELECT gs::BIGINT, primorial
    FROM generate_series(1, primorial - 1) gs
    WHERE NOT EXISTS (
        SELECT 1 FROM primes pr
        WHERE pr.discovered_order <= k
          AND gs % pr.p = 0
    );
END $$;

COMMENT ON FUNCTION wheel_spokes IS
    'Returns the φ(primorial(k)) arithmetic-progression spokes surviving the
     first k sieve rounds. Requires the primes table to be populated.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 6. PROGRESSION INTERSECTION
--    General AP intersection — handles gcd(m1, m2) > 1.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION progression_intersect(
    r1 BIGINT, m1 BIGINT,
    r2 BIGINT, m2 BIGINT
)
RETURNS TABLE (remainder BIGINT, modulus BIGINT, intersects BOOLEAN)
LANGUAGE plpgsql IMMUTABLE STRICT
AS $$
DECLARE
    g    BIGINT;
    s    BIGINT;
    lcm  BIGINT;
    diff BIGINT;
    x    BIGINT;
BEGIN
    SELECT eg.g, eg.s INTO g, s FROM ext_gcd(m1, m2) eg;
    lcm  := (m1 / g) * m2;
    diff := r2 - r1;

    IF diff % g != 0 THEN
        RETURN QUERY SELECT 0::BIGINT, 0::BIGINT, FALSE;
        RETURN;
    END IF;

    x := (r1 + m1 * ((diff / g * s) % (m2 / g))) % lcm;
    x := ((x % lcm) + lcm) % lcm;

    RETURN QUERY SELECT x, lcm, TRUE;
END $$;

COMMENT ON FUNCTION progression_intersect IS
    'CRT-style intersection of two arithmetic progressions.
     Returns (remainder, lcm, intersects). intersects=FALSE when no solution.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 7. RESIDUE DECOMPOSITION  (n → CRT coordinates)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION crt_decompose(target BIGINT)
RETURNS TABLE (prime BIGINT, prime_power BIGINT, residue BIGINT)
LANGUAGE sql STABLE
AS $$
    SELECT
        f.prime,
        ROUND(POWER(f.prime, f.exponent))::BIGINT          AS prime_power,
        target % ROUND(POWER(f.prime, f.exponent))::BIGINT AS residue
    FROM factorizations f
    WHERE f.n = target
    ORDER BY f.prime;
$$;

COMMENT ON FUNCTION crt_decompose IS
    'Decomposes n into its CRT coordinate vector (residue mod each prime power).
     Implements ℤ/nℤ ≅ Π ℤ/pᵢ^eᵢℤ in the forward direction.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 8. RESIDUE RECONSTRUCTION  (coordinates → n mod M)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION crt_reconstruct(
    residues     BIGINT[],
    prime_powers BIGINT[]
)
RETURNS BIGINT
LANGUAGE sql IMMUTABLE STRICT
AS $$
    SELECT crt(residues, prime_powers);
$$;

COMMENT ON FUNCTION crt_reconstruct IS
    'Inverse of crt_decompose: residue coordinates → unique x in [0, Π pᵢ^eᵢ).';
