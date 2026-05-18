-- =============================================================================
--  calx — Schema & Indexes
--  Source of truth: tables `integers`, `primes`, `factorizations`
-- =============================================================================


-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 1: SCHEMA
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS integers (
    n             BIGINT   PRIMARY KEY,
    is_prime      BOOLEAN  NOT NULL DEFAULT FALSE,
    omega         INTEGER,            -- ω(n):  number of distinct prime factors
    big_omega     INTEGER,            -- Ω(n):  total prime factors with multiplicity
    is_squarefree BOOLEAN             -- TRUE iff every prime appears with exponent 1
);

COMMENT ON TABLE  integers               IS 'Universe of all integers 1..N';
COMMENT ON COLUMN integers.omega         IS 'ω(n): count of distinct prime divisors';
COMMENT ON COLUMN integers.big_omega     IS 'Ω(n): sum of all prime factor exponents';
COMMENT ON COLUMN integers.is_squarefree IS 'TRUE iff Ω(n) = ω(n)';


CREATE TABLE IF NOT EXISTS primes (
    p                BIGINT  PRIMARY KEY REFERENCES integers(n),
    discovered_order BIGINT  NOT NULL UNIQUE   -- 1-indexed rank (2 → 1, 3 → 2, ...)
);

COMMENT ON TABLE primes IS 'All confirmed primes within the database range';


CREATE TABLE IF NOT EXISTS factorizations (
    n         BIGINT  NOT NULL REFERENCES integers(n),
    prime     BIGINT  NOT NULL REFERENCES primes(p),
    exponent  INTEGER NOT NULL CHECK (exponent >= 1),
    PRIMARY KEY (n, prime)
);

COMMENT ON TABLE  factorizations          IS 'Full prime factorization: n = Π p^e';
COMMENT ON COLUMN factorizations.exponent IS 'ν_p(n): p-adic valuation of n at prime p';


-- ─────────────────────────────────────────────────────────────────────────────
-- SECTION 2: INDEXES
-- ─────────────────────────────────────────────────────────────────────────────

-- Partial index: fast enumeration of all primes
CREATE INDEX IF NOT EXISTS idx_integers_primes
    ON integers(n)
    WHERE is_prime = TRUE;

-- Covering index: "what are all prime factors of n?"
CREATE INDEX IF NOT EXISTS idx_fact_by_n
    ON factorizations(n, prime, exponent);

-- Covering index: "what are all multiples of prime p in range?"
CREATE INDEX IF NOT EXISTS idx_fact_by_prime
    ON factorizations(prime, n, exponent);

-- Lookup: squarefree numbers, k-almost-primes, etc.
CREATE INDEX IF NOT EXISTS idx_integers_omega
    ON integers(omega, big_omega, n);
