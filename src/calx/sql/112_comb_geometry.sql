-- Unified model, step 112: exact geometry over Q and Q(alpha).
--
-- Fourth step of gap T2 (docs/DESIGN_COMB_FINITE_STRUCTURES.md, Part 2), and
-- the reason that document exists: the planar unit-distance counterexample is
-- built through algebraic number theory, and a store that keeps its
-- coordinates in floating point is not hosting the counterexample, only a
-- picture of it. The whole content of the object is an exact incidence pattern
-- that float8 erases.
--
-- SQUARED DISTANCES, ALWAYS. The unit-distance condition is |p-q|^2 = 1, a
-- polynomial in the coordinates. No square root is ever taken, so the metric
-- introduces no irrationality of its own and the computation stays inside
-- whatever field the coordinates live in. That single choice is what turns
-- exact geometry in SQL from a symbolic-algebra project into four small array
-- functions. Nothing here ever computes a distance, and the names say so.
--
-- THE REPRESENTATION. alpha is a root of a monic minimal polynomial given as
-- integer coefficients in ASCENDING order, so x^2 - 3 is {-3,0,1}. A field
-- element is a rational vector over the power basis 1, alpha, ..., alpha^(d-1):
-- integer numerators and one common positive denominator, in lowest terms.
-- Because the power basis IS a basis, that canonical form is unique, so
-- equality is a comparison of two values rather than a subtraction and a zero
-- test -- and so the Python mirror in calx/numberfield.py can be compared to
-- this one row for row.
--
-- Q IS THE DEGREE-1 CASE. m(x) = x, i.e. {0,1}: d = 1, basis {1}, and the
-- reduction never fires. Rational and algebraic coordinates run the same code
-- path instead of needing a special case, which is one fewer place for the two
-- to drift apart.
--
-- WHERE FLOATS GO. A configuration produced by a numerical optimiser is still
-- worth storing -- AlphaEvolve's constructions arrive that way -- so
-- coord_domain admits 'float_heuristic' alongside 'rational' and 'algebraic'.
-- What it does not get is a verdict: comb.unit_distance_claim stamps the
-- claim's numeric domain from the configuration, and the shield in 94
-- downgrades a float_heuristic claim's valid to unverified at record time. The
-- honest outcome for an approximate construction is "candidate", and that is
-- what the ledger will show without anyone having to remember the rule.
--
-- Idempotent; additive only.

INSERT INTO cert.method (name, claim_kind, checker_kind, description)
VALUES ('comp_sql', 'computational', 'sql', 'in-DB probe returning (ok, evidence)')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 1. Field arithmetic
-- ---------------------------------------------------------------------------

DO $$ BEGIN
    CREATE TYPE comb.nf_elem AS (num NUMERIC[], den NUMERIC);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

COMMENT ON TYPE comb.nf_elem IS
    'An element of Q(alpha): integer numerators over the power basis 1..alpha^(d-1) '
    'plus one common positive denominator, in lowest terms. Canonical, so equal '
    'elements compare equal.';

-- Lowest terms, positive denominator. Zero normalises to 0/1 so that it has one
-- representation rather than one per denominator.
--
-- trim_scale is not cosmetic here. NUMERIC carries its scale, so power(10, 0)
-- is 1.000...0 to 100-odd places and compares equal to 1 while printing and
-- hashing quite differently. Since this canonical form is what the Python
-- mirror is diffed against, and what a bundle would carry, the trailing zeros
-- have to come off at the one point every element passes through.
CREATE OR REPLACE FUNCTION comb.nf_normalize(p_num NUMERIC[], p_den NUMERIC)
RETURNS comb.nf_elem
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE v_num NUMERIC[] := p_num; v_den NUMERIC := p_den; v_g NUMERIC; c NUMERIC;
BEGIN
    IF v_den = 0 THEN
        RAISE EXCEPTION 'comb.nf_normalize: zero denominator';
    END IF;
    IF v_den < 0 THEN
        v_den := -v_den;
        v_num := ARRAY(SELECT -x FROM unnest(v_num) x);
    END IF;
    v_g := v_den;
    FOREACH c IN ARRAY v_num LOOP
        v_g := gcd(v_g, abs(c));
    END LOOP;
    IF v_g > 1 THEN
        v_num := ARRAY(SELECT x / v_g FROM unnest(v_num) x);
        v_den := v_den / v_g;
    END IF;
    RETURN ROW(ARRAY(SELECT trim_scale(x) FROM unnest(v_num) x),
               trim_scale(v_den))::comb.nf_elem;
END
$$;

-- Reduce a polynomial of any degree modulo a monic minimal polynomial, top
-- down: x^k for k >= d is rewritten with x^d = -(m_0 + ... + m_{d-1}x^{d-1}),
-- shifted up by k-d. Monicity is what keeps this division-free and exact.
CREATE OR REPLACE FUNCTION comb.nf_reduce(p_coeffs NUMERIC[], p_min_poly NUMERIC[])
RETURNS NUMERIC[]
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE v_d INTEGER; v_out NUMERIC[]; v_c NUMERIC; k INTEGER; j INTEGER;
BEGIN
    v_d := cardinality(p_min_poly) - 1;
    IF v_d < 1 THEN
        RAISE EXCEPTION 'comb.nf_reduce: a minimal polynomial needs degree >= 1';
    END IF;
    IF p_min_poly[v_d + 1] <> 1 THEN
        RAISE EXCEPTION
            'comb.nf_reduce: minimal polynomial must be monic; leading coefficient is %',
            p_min_poly[v_d + 1];
    END IF;

    v_out := p_coeffs;
    FOR k IN cardinality(p_coeffs) + 1 .. v_d LOOP   -- pad short inputs
        v_out[k] := 0;
    END LOOP;

    FOR k IN REVERSE cardinality(v_out) - 1 .. v_d LOOP   -- k is the 0-based degree
        v_c := v_out[k + 1];
        IF v_c <> 0 THEN
            v_out[k + 1] := 0;
            FOR j IN 0 .. v_d - 1 LOOP
                v_out[k - v_d + j + 1] :=
                    v_out[k - v_d + j + 1] - v_c * p_min_poly[j + 1];
            END LOOP;
        END IF;
    END LOOP;

    RETURN v_out[1:v_d];
END
$$;

CREATE OR REPLACE FUNCTION comb.nf_add(a comb.nf_elem, b comb.nf_elem,
                                       p_min_poly NUMERIC[])
RETURNS comb.nf_elem
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE v_d INTEGER; v_num NUMERIC[] := '{}'; i INTEGER;
BEGIN
    v_d := cardinality(p_min_poly) - 1;
    FOR i IN 1 .. v_d LOOP
        v_num[i] := COALESCE(a.num[i], 0) * b.den + COALESCE(b.num[i], 0) * a.den;
    END LOOP;
    RETURN comb.nf_normalize(v_num, a.den * b.den);
END
$$;

CREATE OR REPLACE FUNCTION comb.nf_sub(a comb.nf_elem, b comb.nf_elem,
                                       p_min_poly NUMERIC[])
RETURNS comb.nf_elem
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE v_d INTEGER; v_num NUMERIC[] := '{}'; i INTEGER;
BEGIN
    v_d := cardinality(p_min_poly) - 1;
    FOR i IN 1 .. v_d LOOP
        v_num[i] := COALESCE(a.num[i], 0) * b.den - COALESCE(b.num[i], 0) * a.den;
    END LOOP;
    RETURN comb.nf_normalize(v_num, a.den * b.den);
END
$$;

-- Convolve, then reduce. The only place the field structure is used at all.
CREATE OR REPLACE FUNCTION comb.nf_mul(a comb.nf_elem, b comb.nf_elem,
                                       p_min_poly NUMERIC[])
RETURNS comb.nf_elem
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE v_d INTEGER; v_conv NUMERIC[] := '{}'; i INTEGER; j INTEGER; x NUMERIC;
BEGIN
    v_d := cardinality(p_min_poly) - 1;
    FOR i IN 1 .. 2 * v_d - 1 LOOP
        v_conv[i] := 0;
    END LOOP;
    FOR i IN 1 .. v_d LOOP
        x := COALESCE(a.num[i], 0);
        IF x <> 0 THEN
            FOR j IN 1 .. v_d LOOP
                v_conv[i + j - 1] := v_conv[i + j - 1] + x * COALESCE(b.num[j], 0);
            END LOOP;
        END IF;
    END LOOP;
    RETURN comb.nf_normalize(comb.nf_reduce(v_conv, p_min_poly), a.den * b.den);
END
$$;

-- ---------------------------------------------------------------------------
-- 2. The store
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS comb.number_field (
    id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,          -- 'Q(sqrt3)', 'Q(zeta5)'
    min_poly NUMERIC[] NOT NULL,            -- ascending, monic, integer
    degree   INTEGER NOT NULL CHECK (degree >= 1)
);

CREATE TABLE IF NOT EXISTS comb.configuration (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_id   TEXT NOT NULL UNIQUE,
    dim          INTEGER NOT NULL CHECK (dim >= 1),
    coord_domain TEXT NOT NULL CHECK (coord_domain IN
                     ('rational', 'algebraic', 'float_heuristic')),
    field_id     BIGINT REFERENCES comb.number_field(id),
    source       TEXT NOT NULL DEFAULT '',
    provenance   JSONB NOT NULL DEFAULT '{}'::jsonb,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((coord_domain = 'algebraic') = (field_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS comb.point (
    configuration_id BIGINT NOT NULL REFERENCES comb.configuration(id) ON DELETE CASCADE,
    idx              INTEGER NOT NULL CHECK (idx >= 0),
    label            TEXT,
    PRIMARY KEY (configuration_id, idx)
);

-- NUMERIC and integral, never float8: num holds coefficients over the power
-- basis and den their common denominator.
CREATE TABLE IF NOT EXISTS comb.coordinate (
    configuration_id BIGINT   NOT NULL,
    point_idx        INTEGER  NOT NULL,
    axis             SMALLINT NOT NULL CHECK (axis >= 0),
    num              NUMERIC[] NOT NULL,
    den              NUMERIC   NOT NULL CHECK (den > 0),
    PRIMARY KEY (configuration_id, point_idx, axis),
    FOREIGN KEY (configuration_id, point_idx)
        REFERENCES comb.point(configuration_id, idx) ON DELETE CASCADE
);

CREATE OR REPLACE FUNCTION comb.register_field(p_name TEXT, p_min_poly NUMERIC[])
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE v_d INTEGER; v_id BIGINT; c NUMERIC;
BEGIN
    v_d := cardinality(p_min_poly) - 1;
    IF v_d < 1 THEN
        RAISE EXCEPTION 'comb.register_field: degree must be >= 1';
    END IF;
    IF p_min_poly[v_d + 1] <> 1 THEN
        RAISE EXCEPTION 'comb.register_field: minimal polynomial must be monic';
    END IF;
    FOREACH c IN ARRAY p_min_poly LOOP
        IF c <> trunc(c) THEN
            RAISE EXCEPTION
                'comb.register_field: coefficients must be integers, got %', c;
        END IF;
    END LOOP;

    INSERT INTO comb.number_field (name, min_poly, degree)
    VALUES (p_name, p_min_poly, v_d)
    ON CONFLICT (name) DO UPDATE
        SET min_poly = EXCLUDED.min_poly, degree = EXCLUDED.degree
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

CREATE OR REPLACE FUNCTION comb.register_configuration(
    p_subject TEXT, p_dim INTEGER, p_coord_domain TEXT,
    p_field TEXT DEFAULT NULL, p_source TEXT DEFAULT ''
) RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE v_fid BIGINT; v_id BIGINT;
BEGIN
    IF p_field IS NOT NULL THEN
        SELECT id INTO v_fid FROM comb.number_field WHERE name = p_field;
        IF v_fid IS NULL THEN
            RAISE EXCEPTION 'comb: no number field % (register_field first)', p_field;
        END IF;
    END IF;
    INSERT INTO comb.configuration (subject_id, dim, coord_domain, field_id, source)
    VALUES (p_subject, p_dim, p_coord_domain, v_fid, p_source)
    ON CONFLICT (subject_id) DO UPDATE
        SET dim = EXCLUDED.dim, coord_domain = EXCLUDED.coord_domain,
            field_id = EXCLUDED.field_id, source = EXCLUDED.source
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

CREATE OR REPLACE FUNCTION comb.configuration_id(p_subject TEXT)
RETURNS BIGINT
LANGUAGE plpgsql STABLE AS $$
DECLARE v_id BIGINT;
BEGIN
    SELECT id INTO v_id FROM comb.configuration WHERE subject_id = p_subject;
    IF v_id IS NULL THEN
        RAISE EXCEPTION 'comb: no configuration % (register_configuration first)',
                        p_subject;
    END IF;
    RETURN v_id;
END
$$;

-- The field a configuration computes in. Rational and float_heuristic
-- configurations get m(x) = x, so callers never branch on the domain.
CREATE OR REPLACE FUNCTION comb.min_poly(p_config TEXT)
RETURNS NUMERIC[]
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(f.min_poly, ARRAY[0, 1]::NUMERIC[])
      FROM comb.configuration c
      LEFT JOIN comb.number_field f ON f.id = c.field_id
     WHERE c.id = comb.configuration_id(p_config);
$$;

-- General primitive: one coordinate as an explicit power-basis vector.
CREATE OR REPLACE FUNCTION comb.set_coordinate(
    p_config TEXT, p_point INTEGER, p_axis INTEGER,
    p_num NUMERIC[], p_den NUMERIC DEFAULT 1
) RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE v_cid BIGINT; v_dim INTEGER; v_elem comb.nf_elem;
BEGIN
    v_cid := comb.configuration_id(p_config);
    SELECT dim INTO v_dim FROM comb.configuration WHERE id = v_cid;
    IF p_axis < 0 OR p_axis >= v_dim THEN
        RAISE EXCEPTION 'comb.set_coordinate: axis % outside [0, %)', p_axis, v_dim;
    END IF;
    v_elem := comb.nf_normalize(p_num, p_den);

    INSERT INTO comb.point (configuration_id, idx) VALUES (v_cid, p_point)
    ON CONFLICT (configuration_id, idx) DO NOTHING;
    INSERT INTO comb.coordinate (configuration_id, point_idx, axis, num, den)
    VALUES (v_cid, p_point, p_axis, v_elem.num, v_elem.den)
    ON CONFLICT (configuration_id, point_idx, axis)
        DO UPDATE SET num = EXCLUDED.num, den = EXCLUDED.den;
END
$$;

-- Convenience for the degree-1 case: one decimal per axis, converted to an
-- exact fraction by its own scale, so 0.5 becomes 1/2 rather than a float.
CREATE OR REPLACE FUNCTION comb.add_rational_point(
    p_config TEXT, p_point INTEGER, p_values NUMERIC[]
) RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE v_d INTEGER; i INTEGER; v NUMERIC; v_den NUMERIC; v_num NUMERIC[];
BEGIN
    v_d := cardinality(comb.min_poly(p_config)) - 1;
    FOR i IN 1 .. cardinality(p_values) LOOP
        v := p_values[i];
        v_den := power(10::NUMERIC, scale(v));
        v_num := array_fill(0::NUMERIC, ARRAY[v_d]);
        v_num[1] := v * v_den;
        PERFORM comb.set_coordinate(p_config, p_point, i - 1, v_num, v_den);
    END LOOP;
END
$$;

-- ---------------------------------------------------------------------------
-- 3. Squared distance
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION comb.coord(p_config TEXT, p_point INTEGER, p_axis INTEGER)
RETURNS comb.nf_elem
LANGUAGE plpgsql STABLE AS $$
DECLARE v_row comb.coordinate%ROWTYPE;
BEGIN
    SELECT * INTO v_row FROM comb.coordinate
     WHERE configuration_id = comb.configuration_id(p_config)
       AND point_idx = p_point AND axis = p_axis;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'comb.coord: % has no coordinate (point %, axis %)',
                        p_config, p_point, p_axis;
    END IF;
    RETURN ROW(v_row.num, v_row.den)::comb.nf_elem;
END
$$;

-- |p_i - p_j|^2, exact. Never a distance: no root is taken anywhere.
CREATE OR REPLACE FUNCTION comb.sq_distance(p_config TEXT, p_i INTEGER, p_j INTEGER)
RETURNS comb.nf_elem
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_mp   NUMERIC[];
    v_dim  INTEGER;
    v_tot  comb.nf_elem;
    v_diff comb.nf_elem;
    a      INTEGER;
BEGIN
    v_mp := comb.min_poly(p_config);
    SELECT dim INTO v_dim FROM comb.configuration
     WHERE id = comb.configuration_id(p_config);
    v_tot := comb.nf_normalize(array_fill(0::NUMERIC,
                                          ARRAY[cardinality(v_mp) - 1]), 1);
    FOR a IN 0 .. v_dim - 1 LOOP
        v_diff := comb.nf_sub(comb.coord(p_config, p_i, a),
                              comb.coord(p_config, p_j, a), v_mp);
        v_tot := comb.nf_add(v_tot, comb.nf_mul(v_diff, v_diff, v_mp), v_mp);
    END LOOP;
    RETURN v_tot;
END
$$;

-- Is an element exactly the rational p_target? True iff its non-constant
-- coefficients all vanish and the constant one matches after cross-multiplying
-- -- both exact, both in NUMERIC.
CREATE OR REPLACE FUNCTION comb.nf_equals_rational(a comb.nf_elem, p_target NUMERIC)
RETURNS BOOLEAN
LANGUAGE sql IMMUTABLE AS $$
    SELECT COALESCE(a.num[1], 0) = p_target * a.den
       AND NOT EXISTS (SELECT 1 FROM generate_subscripts(a.num, 1) i
                        WHERE i > 1 AND a.num[i] <> 0);
$$;

CREATE OR REPLACE FUNCTION comb.nf_text(a comb.nf_elem)
RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
    SELECT '(' || array_to_string(a.num, ', ') || ')/' || a.den::TEXT;
$$;

CREATE OR REPLACE FUNCTION comb.sq_distance_is(
    p_config TEXT, p_i INTEGER, p_j INTEGER, p_target NUMERIC
) RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE v_d comb.nf_elem;
BEGIN
    v_d := comb.sq_distance(p_config, p_i, p_j);
    ok := comb.nf_equals_rational(v_d, p_target);
    evidence := jsonb_build_object(
        'configuration', p_config, 'points', jsonb_build_array(p_i, p_j),
        'target', p_target, 'squared_distance', comb.nf_text(v_d),
        'exact', true);
    RETURN NEXT;
END
$$;

-- ---------------------------------------------------------------------------
-- 4. Unit-distance realisation
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION comb.points_are_distinct(p_config TEXT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE v_cid BIGINT; v_dupes JSONB; v_n INTEGER;
BEGIN
    v_cid := comb.configuration_id(p_config);
    SELECT COALESCE(jsonb_agg(jsonb_build_array(x.a, x.b)), '[]'::jsonb), COUNT(*)
      INTO v_dupes, v_n
      FROM (SELECT p.idx AS a, q.idx AS b
              FROM comb.point p JOIN comb.point q
                ON q.configuration_id = p.configuration_id AND p.idx < q.idx
             WHERE p.configuration_id = v_cid
               AND comb.nf_equals_rational(comb.sq_distance(p_config, p.idx, q.idx), 0)
             LIMIT 16) x;
    IF v_n > 0 THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', 'two points coincide', 'configuration', p_config,
            'coincident', v_dupes);
        RETURN NEXT; RETURN;
    END IF;
    ok := true;
    evidence := jsonb_build_object('configuration', p_config,
        'points', (SELECT COUNT(*) FROM comb.point WHERE configuration_id = v_cid));
    RETURN NEXT;
END
$$;

-- Does this configuration realise this graph with every edge at distance
-- exactly 1?
--
-- p_faithful controls what is being claimed, and the difference matters. With
-- it false, only edges are constrained -- a unit-distance DRAWING, where a
-- non-edge may land at distance 1 by accident. With it true (the default), the
-- correspondence is exact in both directions, which is what a counterexample
-- to a unit-distance statement has to deposit. Both counts are reported either
-- way, so a reader can see which was checked.
CREATE OR REPLACE FUNCTION comb.is_unit_distance(
    p_config TEXT, p_structure TEXT, p_faithful BOOLEAN DEFAULT true
) RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_cid    BIGINT;
    v_sid    BIGINT;
    v_kind   TEXT;
    v_n      INTEGER;
    v_pts    INTEGER;
    v_bad_e  JSONB;
    v_bad_n  JSONB;
    v_ne     INTEGER;
    v_nn     INTEGER;
BEGIN
    v_cid := comb.configuration_id(p_config);
    SELECT s.id, s.kind, s.ground_n INTO v_sid, v_kind, v_n
      FROM comb.structure s WHERE s.subject_id = p_structure;
    IF v_sid IS NULL THEN
        ok := false;
        evidence := jsonb_build_object('reason', 'no such structure',
                                       'structure', p_structure);
        RETURN NEXT; RETURN;
    END IF;
    IF v_kind <> 'graph' THEN
        ok := NULL;
        evidence := jsonb_build_object(
            'reason', 'unit-distance realisation is defined here for kind=graph',
            'structure', p_structure, 'kind', v_kind);
        RETURN NEXT; RETURN;
    END IF;

    SELECT COUNT(*) INTO v_pts FROM comb.point WHERE configuration_id = v_cid;
    IF v_pts <> v_n THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', 'the configuration and the graph differ in size',
            'points', v_pts, 'vertices', v_n);
        RETURN NEXT; RETURN;
    END IF;

    -- Edges that are not at squared distance 1.
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
               'u', x.u, 'v', x.v, 'squared_distance', x.sq)), '[]'::jsonb), COUNT(*)
      INTO v_bad_e, v_ne
      FROM (SELECT e.u, e.v, comb.nf_text(comb.sq_distance(p_config, e.u, e.v)) AS sq
              FROM comb.edge e
             WHERE e.structure_id = v_sid
               AND NOT comb.nf_equals_rational(
                       comb.sq_distance(p_config, e.u, e.v), 1)
             LIMIT 16) x;

    -- Non-edges that ARE at squared distance 1 (only a fault when faithful).
    SELECT COALESCE(jsonb_agg(jsonb_build_object('u', y.a, 'v', y.b)), '[]'::jsonb),
           COUNT(*)
      INTO v_bad_n, v_nn
      FROM (SELECT p.idx AS a, q.idx AS b
              FROM comb.point p JOIN comb.point q
                ON q.configuration_id = p.configuration_id AND p.idx < q.idx
             WHERE p.configuration_id = v_cid
               AND NOT EXISTS (SELECT 1 FROM comb.edge e
                                WHERE e.structure_id = v_sid
                                  AND e.u = p.idx AND e.v = q.idx)
               AND comb.nf_equals_rational(comb.sq_distance(p_config, p.idx, q.idx), 1)
             LIMIT 16) y;

    IF v_ne > 0 OR (p_faithful AND v_nn > 0) THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', CASE WHEN v_ne > 0
                           THEN 'an edge is not at distance 1'
                           ELSE 'a non-edge is at distance 1 -- realisation is not faithful'
                      END,
            'configuration', p_config, 'structure', p_structure,
            'faithful_required', p_faithful,
            'bad_edges', v_bad_e, 'unit_non_edges', v_bad_n,
            'note', 'at most 16 of each reported');
        RETURN NEXT; RETURN;
    END IF;

    ok := true;
    evidence := jsonb_build_object(
        'configuration', p_config, 'structure', p_structure,
        'faithful_required', p_faithful,
        'vertices', v_n,
        'edges', (SELECT COUNT(*) FROM comb.edge WHERE structure_id = v_sid),
        'unit_non_edges', v_nn,
        'coord_domain', (SELECT coord_domain FROM comb.configuration WHERE id = v_cid));
    RETURN NEXT;
END
$$;

-- ---------------------------------------------------------------------------
-- 5. Claims
-- ---------------------------------------------------------------------------

-- The claim carries the configuration's numeric domain, so an approximate
-- construction is shielded by 94 rather than relying on anyone remembering that
-- its coordinates were only nearly right.
CREATE OR REPLACE FUNCTION comb.unit_distance_claim(
    p_config TEXT, p_structure TEXT, p_faithful BOOLEAN DEFAULT true
) RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE v_domain TEXT; v_stmt TEXT; v_probe TEXT; v_id BIGINT;
BEGIN
    SELECT coord_domain INTO v_domain FROM comb.configuration
     WHERE id = comb.configuration_id(p_config);
    PERFORM comb.structure_id(p_structure);

    v_stmt := format('configuration %L realises %L as a%s unit-distance graph',
                     p_config, p_structure,
                     CASE WHEN p_faithful THEN ' faithful' ELSE '' END);
    v_probe := format('SELECT ok, evidence FROM comb.is_unit_distance(%L, %L, %L)',
                      p_config, p_structure, p_faithful);
    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind,
                            method, probe_sql)
    VALUES ('comb_geometry',
            jsonb_build_object('configuration', p_config, 'structure', p_structure,
                               'faithful', p_faithful),
            v_stmt, 'computational', 'comp_sql', v_probe)
    ON CONFLICT (statement) DO UPDATE SET probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_id;

    PERFORM cert.set_domain(v_id, v_domain);
    RETURN v_id;
END
$$;

COMMENT ON TABLE comb.configuration IS
    'A finite point configuration with EXACT coordinates over Q or Q(alpha). '
    'Distances are only ever squared, so no root is taken and the arithmetic '
    'stays in the field. coord_domain=float_heuristic is storable but its '
    'claims are shielded by 94 and can never record a valid certificate.';
