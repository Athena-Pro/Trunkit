-- =============================================================================
--  nerode — Step A0: Interval algebra for quantitative automata (Phase 4)
--
--  Represents I(D) over D = ℝ≥0 from arXiv:2606.11223 ("Scenario Constraints
--  with Memory"):  ∅,  [a,b],  [a,∞),  and the full domain D.
--
--  Endpoints are NUMERIC (exact rationals) so the extremal DP (Theorem 3) stays
--  sound — that theorem's polynomial-time guarantee is stated for rational
--  constants encoded in binary.
--
--  Pure / IMMUTABLE. No dependency on other Phase-4 files. Idempotent.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Composite type nerode.interval
--   is_empty = TRUE          ⇒ ∅                 (lo/hi ignored)
--   lo = 0,  hi IS NULL      ⇒ D = [0,∞)         (full domain, the default top)
--   lo = a,  hi IS NULL      ⇒ [a,∞)
--   lo = a,  hi = b          ⇒ [a,b]
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE t.typname = 'interval' AND n.nspname = 'nerode'
    ) THEN
        CREATE TYPE nerode.interval AS (
            lo       NUMERIC,
            hi       NUMERIC,
            is_empty BOOLEAN
        );
    END IF;
END
$$;

COMMENT ON TYPE nerode.interval IS
    'Admissible numerical interval I(D) over D = ℝ≥0 (arXiv:2606.11223). '
    'is_empty=TRUE ⇒ ∅; hi IS NULL ⇒ unbounded above [lo,∞); (0,NULL,FALSE) ⇒ full domain D.';

-- ---------------------------------------------------------------------------
-- Constructors
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.iv_empty()
RETURNS nerode.interval LANGUAGE sql IMMUTABLE AS
$$ SELECT ROW(NULL, NULL, TRUE)::nerode.interval; $$;

CREATE OR REPLACE FUNCTION nerode.iv_full()
RETURNS nerode.interval LANGUAGE sql IMMUTABLE AS
$$ SELECT ROW(0, NULL, FALSE)::nerode.interval; $$;

-- Closed bounded interval [a,b]; collapses to ∅ if a > b or either bound < 0.
CREATE OR REPLACE FUNCTION nerode.iv_closed(p_a NUMERIC, p_b NUMERIC)
RETURNS nerode.interval LANGUAGE sql IMMUTABLE AS
$$
    SELECT CASE
        WHEN p_a IS NULL OR p_b IS NULL OR p_a < 0 OR p_b < 0 OR p_a > p_b
            THEN ROW(NULL, NULL, TRUE)::nerode.interval
        ELSE ROW(p_a, p_b, FALSE)::nerode.interval
    END;
$$;

-- Lower-bounded interval [a,∞); ∅ if a < 0.
CREATE OR REPLACE FUNCTION nerode.iv_lower(p_a NUMERIC)
RETURNS nerode.interval LANGUAGE sql IMMUTABLE AS
$$
    SELECT CASE
        WHEN p_a IS NULL OR p_a < 0 THEN ROW(NULL, NULL, TRUE)::nerode.interval
        ELSE ROW(p_a, NULL, FALSE)::nerode.interval
    END;
$$;

-- Build from JSONB: {} or null ⇒ D; {"empty":true} ⇒ ∅;
-- {"lo":a} ⇒ [a,∞); {"lo":a,"hi":b} ⇒ [a,b].
CREATE OR REPLACE FUNCTION nerode.iv_from_json(p JSONB)
RETURNS nerode.interval LANGUAGE sql IMMUTABLE AS
$$
    SELECT CASE
        WHEN p IS NULL OR p = '{}'::jsonb                 THEN nerode.iv_full()
        WHEN COALESCE((p->>'empty')::boolean, FALSE)      THEN nerode.iv_empty()
        WHEN p ? 'hi'                                     THEN nerode.iv_closed((p->>'lo')::numeric, (p->>'hi')::numeric)
        WHEN p ? 'lo'                                     THEN nerode.iv_lower((p->>'lo')::numeric)
        ELSE nerode.iv_full()
    END;
$$;

-- ---------------------------------------------------------------------------
-- Predicates / accessors
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.iv_is_empty(p nerode.interval)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE AS
$$ SELECT COALESCE(p.is_empty, TRUE); $$;

-- Membership d ∈ I  (the interval guard predicate of the paper).
CREATE OR REPLACE FUNCTION nerode.iv_contains(p nerode.interval, p_d NUMERIC)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE AS
$$
    SELECT CASE
        WHEN p_d IS NULL OR COALESCE(p.is_empty, TRUE) THEN FALSE
        ELSE p_d >= p.lo AND (p.hi IS NULL OR p_d <= p.hi)
    END;
$$;

-- ---------------------------------------------------------------------------
-- Subset (containment): p ⊆ q ?  ∅ ⊆ anything; anything ⊆ D.
-- Used by the cross-scenario monotonicity probe (A6) to decide L(H₁) ⊆ L(H₂).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.iv_subset(p nerode.interval, q nerode.interval)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE AS
$$
    SELECT CASE
        WHEN COALESCE(p.is_empty, TRUE) THEN TRUE          -- ∅ ⊆ anything
        WHEN COALESCE(q.is_empty, TRUE) THEN FALSE         -- nonempty ⊄ ∅
        ELSE q.lo <= p.lo
             AND (q.hi IS NULL OR (p.hi IS NOT NULL AND p.hi <= q.hi))
    END;
$$;

COMMENT ON FUNCTION nerode.iv_subset(nerode.interval, nerode.interval) IS
    'Interval containment p ⊆ q. ∅ ⊆ anything; q unbounded above (hi NULL) ⊇ any '
    'lower-or-equal-bounded p. Used by the A6 monotonicity probe.';

-- ---------------------------------------------------------------------------
-- Meet (intersection) — the intersection resolver of Example 4.
-- ∅ is absorbing; D is the identity; [a,b] ∩ [c,d] = [max(a,c), min(b,d)].
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.iv_meet(p nerode.interval, q nerode.interval)
RETURNS nerode.interval LANGUAGE sql IMMUTABLE AS
$$
    SELECT CASE
        WHEN COALESCE(p.is_empty, TRUE) OR COALESCE(q.is_empty, TRUE)
            THEN nerode.iv_empty()
        ELSE (
            SELECT CASE
                -- both unbounded above ⇒ [max(lo), ∞)
                WHEN p.hi IS NULL AND q.hi IS NULL THEN nerode.iv_lower(GREATEST(p.lo, q.lo))
                ELSE nerode.iv_closed(
                        GREATEST(p.lo, q.lo),
                        LEAST(COALESCE(p.hi, 'Infinity'::numeric),
                              COALESCE(q.hi, 'Infinity'::numeric))
                     )
            END
        )
    END;
$$;

COMMENT ON FUNCTION nerode.iv_meet(nerode.interval, nerode.interval) IS
    'Interval intersection (intersection resolver, Example 4 of arXiv:2606.11223). '
    '∅ absorbing, D identity. Inconsistent overlaps collapse to ∅.';

-- ---------------------------------------------------------------------------
-- Guard rendering: emit the local-payoff-expression AST (used by A2/WFFA) that
-- evaluates to the semiring 1 (=0 in max-plus) on d ∈ I and to −∞ otherwise.
-- This is the "interval-complete" witness required by Theorem 2; for Flin every
-- interval has such a guard. Returned as the A2 payoff-AST `guard` node.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION nerode.iv_to_json(p nerode.interval)
RETURNS JSONB LANGUAGE sql IMMUTABLE AS
$$
    SELECT CASE
        WHEN COALESCE(p.is_empty, TRUE) THEN jsonb_build_object('empty', TRUE)
        WHEN p.hi IS NULL               THEN jsonb_build_object('lo', p.lo)
        ELSE jsonb_build_object('lo', p.lo, 'hi', p.hi)
    END;
$$;

CREATE OR REPLACE FUNCTION nerode.iv_guard(p nerode.interval)
RETURNS JSONB LANGUAGE sql IMMUTABLE AS
$$
    -- {"guard": {"iv": <interval-json>, "then": {"const": 0}}}  (0 = max-plus one)
    SELECT jsonb_build_object(
        'guard', jsonb_build_object(
            'iv',   nerode.iv_to_json(p),
            'then', jsonb_build_object('const', 0)
        )
    );
$$;

COMMENT ON FUNCTION nerode.iv_guard(nerode.interval) IS
    'Render an interval as a max-plus payoff-expression guard (Theorem 2 '
    'interval-completeness witness). Consumed by the A2 WFFA evaluator.';
