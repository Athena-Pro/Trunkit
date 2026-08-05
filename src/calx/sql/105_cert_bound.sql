-- Unified model, step 105: numeric-bound / inequality certificate tier.
--
-- Implements gap T3 of docs/reports/Erdos_Missing_Capabilities_Report.md (the
-- report lands with PR #36; T3 is also item 3 of Trunkit_Erdos_AI_Capability_Fit.md):
-- a tier for claims of the form  q <= v  or  q >= v,  with a partial order
-- over competing bounds on the same quantity.
--
-- WHY A NEW TIER. The existing tiers certify that a statement is TRUE. A
-- bound is different: two true bounds on the same quantity are not equally
-- useful, and progress in the literature IS the replacement of one true
-- bound by a tighter true bound. A ledger that only records truth cannot
-- express "this bound is the best known" or "this bound is optimal", which
-- is most of what a bound is for.
--
-- WHY THE ORDER IS PARTIAL, NOT TOTAL. Numeric values are totally ordered,
-- so if a bound were only a number the "partial order" would collapse to
-- `<`. It does not collapse because a bound is a number TOGETHER WITH the
-- hypotheses it holds under. A tighter bound requiring a stronger hypothesis
-- does not supersede a weaker unconditional one -- they are incomparable,
-- and both belong on the frontier. Domination is therefore the product
-- order: at least as tight in value AND assuming no more than. Worked
-- example, the bend-and-break constants of arXiv:2607.06447 Section 3.1:
--
--   2n   (Shepherd-Barron)      hypotheses {}                    n=3 -> 6
--   2r   (Bogomolov-McQuillan)  hypotheses {}                    r=2 -> 4
--   r+1  (Jovinelly-Lehmann-Riedl / Liu-Sun-Jiang, optimal)      r=2 -> 3
--
-- all unconditional, so here the order IS total and the frontier is {r+1};
-- add one conditional sharper bound and the frontier grows to two
-- incomparable elements. Both behaviours are exercised in the demo.
--
-- WHAT IS AND IS NOT CHECKABLE IN SQL. That a bound HOLDS is a theorem, not
-- an in-DB computation: it enters the ledger through the existing tiers (a
-- formal claim with a Lean artifact, or a tool-attested fact via 104), and
-- this layer only references it. What this layer checks, forever and
-- cheaply, is the RELATIONAL structure over bounds: mutual consistency,
-- domination, frontier membership, enclosure width. Those are exactly the
-- facts that go stale when new bounds land, which is why they are worth
-- re-checking rather than recording once.
--
-- Three-valued honesty is preserved throughout. A lower bound exceeding an
-- upper bound on the same quantity is a REFUTATION -- the pair cannot both
-- hold, and the evidence names both, so a consumer sees which two claims
-- collide rather than a bare false. An optimality claim with no attaining
-- witness is UNVERIFIED, never valid: "nothing tighter is on record" is a
-- statement about the ledger, not about mathematics. Idempotent; additive.

INSERT INTO cert.method (name, claim_kind, checker_kind, description)
VALUES ('comp_sql', 'computational', 'sql', 'in-DB probe returning (ok, evidence)')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 1. Quantities and bounds
-- ---------------------------------------------------------------------------

-- The thing being bounded. Kept separate from the bounds so that competing
-- bounds are competing BY CONSTRUCTION (same quantity_id) rather than by a
-- string match on a description.
CREATE TABLE IF NOT EXISTS cert.quantity (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_id   TEXT NOT NULL UNIQUE,
    description  TEXT NOT NULL,
    -- Optional pointer into another registry (an OEIS A-number, a claim, a
    -- sequence id). Free-form on purpose: the tier must accept quantities
    -- whose home layer does not exist yet.
    subject_ref  JSONB NOT NULL DEFAULT '{}'::jsonb,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- NUMERIC, never DOUBLE PRECISION: a bound compared in binary floating point
-- is a bound that can be reported tighter than it is. NUMERIC comparison is
-- exact decimal, which is what makes `dominates` a defensible relation.
CREATE TABLE IF NOT EXISTS cert.bound (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    quantity_id  BIGINT NOT NULL REFERENCES cert.quantity(id) ON DELETE CASCADE,
    direction    TEXT NOT NULL CHECK (direction IN ('upper', 'lower')),
    value        NUMERIC NOT NULL,
    -- strict = the inequality is <  (resp >) rather than <= (resp >=).
    is_strict    BOOLEAN NOT NULL DEFAULT false,
    -- The assumption set. Empty array = unconditional. Subset containment on
    -- this column is the second coordinate of the domination order, so the
    -- labels must be canonical: two spellings of one hypothesis silently
    -- make two bounds incomparable.
    hypotheses   TEXT[] NOT NULL DEFAULT '{}',
    -- Attribution, and the ledger claim that carries the bound's TRUTH (a
    -- formal/attested claim elsewhere). NULL claim_id = the bound is on
    -- record but its truth is not yet certified anywhere: it can still be
    -- compared and can still participate in a refutation, but optimality
    -- claims about it stay unverified.
    source       TEXT NOT NULL DEFAULT '',
    claim_id     BIGINT REFERENCES cert.claim(id),
    -- An attaining instance, if known: what makes a bound optimal rather
    -- than merely best-known. Free-form (a construction, a parameter set).
    attained_by  JSONB,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (quantity_id, direction, value, is_strict, hypotheses, source)
);

CREATE INDEX IF NOT EXISTS bound_quantity_idx ON cert.bound (quantity_id, direction);

CREATE OR REPLACE FUNCTION cert.register_quantity(
    p_subject_id TEXT, p_description TEXT, p_subject_ref JSONB DEFAULT '{}'::jsonb
) RETURNS cert.quantity
LANGUAGE plpgsql AS $$
DECLARE v_row cert.quantity%ROWTYPE;
BEGIN
    INSERT INTO cert.quantity (subject_id, description, subject_ref)
    VALUES (p_subject_id, p_description, p_subject_ref)
    ON CONFLICT (subject_id) DO UPDATE
        SET description = EXCLUDED.description, subject_ref = EXCLUDED.subject_ref
    RETURNING * INTO v_row;
    RETURN v_row;
END
$$;

CREATE OR REPLACE FUNCTION cert.register_bound(
    p_quantity   TEXT,
    p_direction  TEXT,
    p_value      NUMERIC,
    p_strict     BOOLEAN DEFAULT false,
    p_hypotheses TEXT[] DEFAULT '{}',
    p_source     TEXT DEFAULT '',
    p_claim_id   BIGINT DEFAULT NULL,
    p_attained_by JSONB DEFAULT NULL
) RETURNS cert.bound
LANGUAGE plpgsql AS $$
DECLARE
    v_qid BIGINT;
    v_row cert.bound%ROWTYPE;
BEGIN
    SELECT id INTO v_qid FROM cert.quantity WHERE subject_id = p_quantity;
    IF v_qid IS NULL THEN
        RAISE EXCEPTION 'no quantity %  (register_quantity first)', p_quantity;
    END IF;
    -- Canonicalise the hypothesis set: sorted, de-duplicated. Subset
    -- containment does not care about order, but the UNIQUE key does, and an
    -- un-normalised set would let the same bound in twice.
    INSERT INTO cert.bound (quantity_id, direction, value, is_strict, hypotheses,
                            source, claim_id, attained_by)
    VALUES (v_qid, p_direction, p_value, p_strict,
            COALESCE((SELECT array_agg(DISTINCT h ORDER BY h)
                        FROM unnest(p_hypotheses) h), '{}'),
            p_source, p_claim_id, p_attained_by)
    ON CONFLICT (quantity_id, direction, value, is_strict, hypotheses, source)
        DO UPDATE SET claim_id    = COALESCE(EXCLUDED.claim_id, cert.bound.claim_id),
                      attained_by = COALESCE(EXCLUDED.attained_by, cert.bound.attained_by)
    RETURNING * INTO v_row;
    RETURN v_row;
END
$$;

-- ---------------------------------------------------------------------------
-- 2. The partial order
-- ---------------------------------------------------------------------------

-- Value coordinate: is `a` at least as tight as `b`? For upper bounds
-- smaller is tighter; for lower bounds larger is tighter. At equal values a
-- strict inequality is tighter than a non-strict one.
CREATE OR REPLACE FUNCTION cert.bound_tighter_eq(a cert.bound, b cert.bound)
RETURNS BOOLEAN
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN a.direction <> b.direction THEN false
        WHEN a.direction = 'upper' THEN
            a.value < b.value OR (a.value = b.value AND (a.is_strict OR NOT b.is_strict))
        ELSE
            a.value > b.value OR (a.value = b.value AND (a.is_strict OR NOT b.is_strict))
    END
$$;

-- The order itself. `a` dominates `b` when it is at least as tight AND
-- assumes no more, and is strictly better in at least one of the two
-- coordinates. Reflexive pairs are excluded, so this is a strict partial
-- order: irreflexive, antisymmetric, transitive (transitivity follows from
-- transitivity of <= on NUMERIC and of <@ on TEXT[]).
--
-- Incomparability is the whole point. A tighter bound under a stronger
-- hypothesis dominates nothing, and is dominated by nothing.
CREATE OR REPLACE FUNCTION cert.bound_dominates(p_a BIGINT, p_b BIGINT)
RETURNS BOOLEAN
LANGUAGE plpgsql STABLE AS $$
DECLARE
    a cert.bound%ROWTYPE;
    b cert.bound%ROWTYPE;
    v_tighter    BOOLEAN;
    v_hyp_subset BOOLEAN;
BEGIN
    SELECT * INTO a FROM cert.bound WHERE id = p_a;
    SELECT * INTO b FROM cert.bound WHERE id = p_b;
    IF a.id IS NULL OR b.id IS NULL OR a.id = b.id THEN RETURN false; END IF;
    IF a.quantity_id <> b.quantity_id OR a.direction <> b.direction THEN
        RETURN false;
    END IF;
    v_tighter    := cert.bound_tighter_eq(a, b);
    v_hyp_subset := a.hypotheses <@ b.hypotheses;
    IF NOT (v_tighter AND v_hyp_subset) THEN RETURN false; END IF;
    -- Strictly better somewhere: either a genuinely tighter value/strictness,
    -- or a genuinely weaker hypothesis set.
    RETURN (a.value <> b.value OR a.is_strict <> b.is_strict)
        OR NOT (b.hypotheses <@ a.hypotheses);
END
$$;

-- The frontier: bounds that nothing else dominates. One row per surviving
-- bound, with the count of what it beats -- a bound that dominates many is
-- the story of the quantity's progress; a frontier of size > 1 in a single
-- direction means genuinely incomparable hypotheses.
CREATE OR REPLACE FUNCTION cert.bound_frontier(p_quantity TEXT)
RETURNS TABLE (
    bound_id   BIGINT,
    direction  TEXT,
    value      NUMERIC,
    is_strict  BOOLEAN,
    hypotheses TEXT[],
    source     TEXT,
    dominates_n INTEGER
)
LANGUAGE sql STABLE AS $$
    SELECT b.id, b.direction, b.value, b.is_strict, b.hypotheses, b.source,
           (SELECT COUNT(*)::INTEGER FROM cert.bound o
             WHERE o.quantity_id = b.quantity_id
               AND cert.bound_dominates(b.id, o.id))
      FROM cert.bound b
      JOIN cert.quantity q ON q.id = b.quantity_id
     WHERE q.subject_id = p_quantity
       AND NOT EXISTS (
           SELECT 1 FROM cert.bound o
            WHERE o.quantity_id = b.quantity_id
              AND cert.bound_dominates(o.id, b.id))
     ORDER BY b.direction, b.value;
$$;

-- ---------------------------------------------------------------------------
-- 3. Consistency -- where the tier earns its keep
-- ---------------------------------------------------------------------------

-- A lower bound above an upper bound on the same quantity is a contradiction:
-- under the union of their hypotheses, no value exists. This is the probe
-- that turns a pile of separately-true-looking bounds into something that can
-- FAIL, and it is the reason to keep bounds in the ledger rather than in a
-- spreadsheet.
--
-- Reported as ok=false with both bounds named, so the evidence identifies the
-- colliding pair (and their claim_ids, if their truth was certified) rather
-- than merely asserting inconsistency.
CREATE OR REPLACE FUNCTION cert.bound_consistent(p_quantity TEXT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_qid       BIGINT;
    v_conflicts JSONB;
    v_n         INTEGER;
BEGIN
    SELECT id INTO v_qid FROM cert.quantity WHERE subject_id = p_quantity;
    IF v_qid IS NULL THEN
        ok := false;
        evidence := jsonb_build_object('reason', 'no such quantity',
                                       'quantity', p_quantity);
        RETURN NEXT; RETURN;
    END IF;

    SELECT COALESCE(jsonb_agg(jsonb_build_object(
               'lower_bound_id', lo.id, 'lower_value', lo.value,
               'lower_strict', lo.is_strict, 'lower_source', lo.source,
               'lower_claim_id', lo.claim_id,
               'upper_bound_id', up.id, 'upper_value', up.value,
               'upper_strict', up.is_strict, 'upper_source', up.source,
               'upper_claim_id', up.claim_id,
               'under_hypotheses',
                   (SELECT COALESCE(array_agg(DISTINCT h ORDER BY h), '{}')
                      FROM unnest(lo.hypotheses || up.hypotheses) h)
           )), '[]'::jsonb), COUNT(*)
      INTO v_conflicts, v_n
      FROM cert.bound lo
      JOIN cert.bound up
        ON up.quantity_id = lo.quantity_id AND up.direction = 'upper'
     WHERE lo.quantity_id = v_qid
       AND lo.direction = 'lower'
       -- Empty interval: lower strictly above upper, or the endpoints meet
       -- with at least one side strict (q < v and q >= v is unsatisfiable).
       AND (lo.value > up.value
            OR (lo.value = up.value AND (lo.is_strict OR up.is_strict)));

    IF v_n > 0 THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', 'lower bound exceeds upper bound -- empty interval',
            'quantity', p_quantity, 'conflicts', v_conflicts, 'n', v_n);
        RETURN NEXT; RETURN;
    END IF;

    ok := true;
    evidence := jsonb_build_object(
        'quantity', p_quantity, 'consistent', true,
        'bounds', (SELECT COUNT(*) FROM cert.bound WHERE quantity_id = v_qid));
    RETURN NEXT;
END
$$;

-- Best-known interval under a hypothesis budget: the tightest lower and
-- upper bounds whose hypotheses are contained in p_assume. Empty p_assume
-- gives the unconditional enclosure. width = upper - lower, exact.
CREATE OR REPLACE FUNCTION cert.enclosure(
    p_quantity TEXT, p_assume TEXT[] DEFAULT '{}'
)
RETURNS TABLE (
    lower_value NUMERIC, lower_is_strict BOOLEAN, lower_source TEXT,
    upper_value NUMERIC, upper_is_strict BOOLEAN, upper_source TEXT,
    width NUMERIC
)
LANGUAGE sql STABLE AS $$
    WITH b AS (
        SELECT bd.* FROM cert.bound bd
          JOIN cert.quantity q ON q.id = bd.quantity_id
         WHERE q.subject_id = p_quantity AND bd.hypotheses <@ p_assume
    ),
    lo AS (
        SELECT value, is_strict, source FROM b WHERE direction = 'lower'
         ORDER BY value DESC, is_strict DESC LIMIT 1
    ),
    up AS (
        SELECT value, is_strict, source FROM b WHERE direction = 'upper'
         ORDER BY value ASC, is_strict DESC LIMIT 1
    )
    SELECT lo.value, lo.is_strict, lo.source, up.value, up.is_strict, up.source,
           up.value - lo.value
      FROM lo FULL OUTER JOIN up ON true;
$$;

-- ---------------------------------------------------------------------------
-- 4. Re-checkable claims over the order
-- ---------------------------------------------------------------------------

-- "The bounds on record for q are mutually consistent." Re-checked by
-- cert.check / cert.verify like any comp_sql claim, so it flips to refuted
-- the moment a colliding bound is registered -- which is precisely when
-- someone needs to be told.
CREATE OR REPLACE FUNCTION cert.bound_consistency_claim(p_quantity TEXT)
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE v_stmt TEXT; v_probe TEXT; v_id BIGINT;
BEGIN
    PERFORM 1 FROM cert.quantity WHERE subject_id = p_quantity;
    IF NOT FOUND THEN RAISE EXCEPTION 'no quantity %', p_quantity; END IF;
    v_stmt  := format('all registered bounds on %L are mutually consistent', p_quantity);
    v_probe := format('SELECT ok, evidence FROM cert.bound_consistent(%L)', p_quantity);
    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
    VALUES ('bound', jsonb_build_object('quantity', p_quantity),
            v_stmt, 'computational', 'comp_sql', v_probe)
    ON CONFLICT (statement) DO UPDATE SET probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

-- "Bound #a dominates bound #b." A permanent, re-checkable record of an
-- improvement: this is the edge of the partial order made into a claim, so
-- that "X superseded Y" is itself certified rather than asserted in prose.
CREATE OR REPLACE FUNCTION cert.bound_domination_claim(p_a BIGINT, p_b BIGINT)
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE a cert.bound%ROWTYPE; b cert.bound%ROWTYPE;
        v_stmt TEXT; v_probe TEXT; v_id BIGINT;
BEGIN
    SELECT * INTO a FROM cert.bound WHERE id = p_a;
    SELECT * INTO b FROM cert.bound WHERE id = p_b;
    IF a.id IS NULL OR b.id IS NULL THEN RAISE EXCEPTION 'no such bound'; END IF;
    v_stmt := format('bound #%s (%s %s, %s) dominates #%s (%s %s, %s)',
                     a.id, a.direction, a.value, COALESCE(NULLIF(a.source, ''), 'unattributed'),
                     b.id, b.direction, b.value, COALESCE(NULLIF(b.source, ''), 'unattributed'));
    v_probe := format('SELECT cert.bound_dominates(%s, %s) AS ok,'
                      ' jsonb_build_object(''a'', %s, ''b'', %s) AS evidence',
                      a.id, b.id, a.id, b.id);
    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
    VALUES ('bound', jsonb_build_object('a', a.id, 'b', b.id),
            v_stmt, 'computational', 'comp_sql', v_probe)
    ON CONFLICT (statement) DO UPDATE SET probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

-- Optimality: nothing on record beats it, AND it is attained.
--
-- The two halves are deliberately different in kind. "Nothing on record
-- beats it" is a fact about the ledger and is checked here. "It is attained"
-- is mathematics, and is NOT checked here -- it is carried by attained_by
-- and, properly, by the claim in claim_id. So the probe returns:
--   ok = true   only when the bound is on the frontier AND attained_by is
--               present AND its truth-claim exists;
--   ok = false  when something on record dominates it (a real refutation of
--               optimality);
--   ok = NULL   when it is undominated but has no attaining witness --
--               "best known", which is not the same as optimal, and reads
--               as UNVERIFIED rather than valid.
CREATE OR REPLACE FUNCTION cert.bound_optimal(p_bound_id BIGINT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    b          cert.bound%ROWTYPE;
    v_beaten   JSONB;
    v_n        INTEGER;
    v_standing TEXT;
BEGIN
    SELECT * INTO b FROM cert.bound WHERE id = p_bound_id;
    IF b.id IS NULL THEN
        ok := false;
        evidence := jsonb_build_object('reason', 'no such bound', 'id', p_bound_id);
        RETURN NEXT; RETURN;
    END IF;

    SELECT COALESCE(jsonb_agg(jsonb_build_object(
               'bound_id', o.id, 'value', o.value, 'strict', o.is_strict,
               'hypotheses', o.hypotheses, 'source', o.source)), '[]'::jsonb),
           COUNT(*)
      INTO v_beaten, v_n
      FROM cert.bound o
     WHERE o.quantity_id = b.quantity_id
       AND cert.bound_dominates(o.id, b.id);

    IF v_n > 0 THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', 'a registered bound is strictly better',
            'bound_id', b.id, 'dominated_by', v_beaten);
        RETURN NEXT; RETURN;
    END IF;

    IF b.attained_by IS NULL OR b.claim_id IS NULL THEN
        ok := NULL;   -- three-valued: best known, optimality not established
        evidence := jsonb_build_object(
            'reason', CASE
                WHEN b.attained_by IS NULL AND b.claim_id IS NULL
                    THEN 'undominated, but no attaining witness and no truth-claim'
                WHEN b.attained_by IS NULL
                    THEN 'undominated, but no attaining witness'
                ELSE 'undominated and attained, but the bound''s truth is not certified'
            END,
            'bound_id', b.id, 'status', 'best known on record');
        RETURN NEXT; RETURN;
    END IF;

    -- The bound's own truth must STAND, not merely be referenced. Reading
    -- effective_status (100) means a revoked or expired truth-claim pulls
    -- optimality back to unverified for free, and 102 gives the same for a
    -- revocation anywhere beneath it.
    SELECT effective_status INTO v_standing
      FROM cert.standing WHERE claim_id = b.claim_id;

    IF v_standing IS DISTINCT FROM 'valid' THEN
        ok := NULL;
        evidence := jsonb_build_object(
            'reason', 'undominated and attained, but the bound''s truth-claim '
                      'does not stand',
            'bound_id', b.id, 'truth_claim', b.claim_id,
            'truth_claim_status', COALESCE(v_standing, 'unchecked'),
            'status', 'best known on record');
        RETURN NEXT; RETURN;
    END IF;

    ok := true;
    evidence := jsonb_build_object(
        'bound_id', b.id, 'value', b.value, 'hypotheses', b.hypotheses,
        'attained_by', b.attained_by, 'truth_claim', b.claim_id,
        'truth_claim_status', v_standing,
        'undominated_among', (SELECT COUNT(*) FROM cert.bound o
                               WHERE o.quantity_id = b.quantity_id));
    RETURN NEXT;
END
$$;

CREATE OR REPLACE FUNCTION cert.bound_optimality_claim(p_bound_id BIGINT)
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE b cert.bound%ROWTYPE; q cert.quantity%ROWTYPE;
        v_stmt TEXT; v_probe TEXT; v_id BIGINT;
BEGIN
    SELECT * INTO b FROM cert.bound WHERE id = p_bound_id;
    IF b.id IS NULL THEN RAISE EXCEPTION 'no bound %', p_bound_id; END IF;
    SELECT * INTO q FROM cert.quantity WHERE id = b.quantity_id;
    v_stmt := format('%s bound %s on %s is optimal%s [bound #%s]',
                     b.direction, b.value, q.subject_id,
                     CASE WHEN cardinality(b.hypotheses) > 0
                          THEN ' under ' || array_to_string(b.hypotheses, ', ')
                          ELSE '' END,
                     b.id);
    v_probe := format('SELECT ok, evidence FROM cert.bound_optimal(%s)', b.id);
    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)
    VALUES ('bound', jsonb_build_object('quantity', q.subject_id, 'bound_id', b.id),
            v_stmt, 'computational', 'comp_sql', v_probe)
    ON CONFLICT (statement) DO UPDATE SET probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

COMMENT ON TABLE cert.bound IS
    'Numeric bound tier (T3). A bound is a value plus the hypotheses it holds '
    'under; cert.bound_dominates is the product order over those two '
    'coordinates, so a tighter conditional bound does not supersede a weaker '
    'unconditional one. Truth of a bound lives in claim_id (formal/attested '
    'tiers); this layer certifies the relational structure -- consistency, '
    'domination, frontier, optimality -- which is what goes stale.';
