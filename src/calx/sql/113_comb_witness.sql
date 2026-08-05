-- Unified model, step 113: carrying comb objects, and the bridge to the bound tier.
--
-- Last step of gap T2 (docs/DESIGN_COMB_FINITE_STRUCTURES.md, Part 4). 110-112
-- store objects and decide things about them, all of it inside this database.
-- This step is about getting an object OUT: a consumer holding an exported
-- bundle should be able to re-derive the verdict from the object itself rather
-- than trusting that someone once ran a probe.
--
-- WHY A CONSTRUCTION IS A witness_carry CLAIM AND NOT A comp_sql ONE. comp_sql
-- carries a verdict; witness_carry (88) carries a verdict AND the proof term.
-- For a construction the object IS the proof term -- "there is a triangle-free
-- graph on 5 vertices with 5 edges" is proved by exhibiting one, and a bundle
-- that carries only "true" has thrown away the entire content of the result.
-- So the probes here return (ok, evidence, witness) with the serialised object
-- in the third column, and cert.check_with_witness files it in cert.witness.
--
-- The witness body is self-describing and self-contained: kind, ground set,
-- every block, and for a configuration the minimal polynomial and every exact
-- coordinate. A consumer needs nothing from this database to re-run the check
-- -- which is the whole point, and is why calx/numberfield.py exists on the
-- other side of it.
--
-- THE BRIDGE, WHICH IS THE POINT OF THE WHOLE GAP. An extremal result is a
-- conjunction: there is an object of size m (existential, witness-checkable,
-- exactly what comb hosts) and nothing larger exists (a theorem, which stays a
-- formal or attested claim). The bound tier (105) already models that split,
-- and cert.bound.attained_by has been free-form JSONB since it was written --
-- a note to a human, not a pointer at anything.
--
-- comb.attain_bound makes it a pointer. After it, "optimal construction found
-- numerically" -- AlphaEvolve on #650 and its siblings -- is three rows that
-- know how to talk to each other: the construction in comb, a lower bound
-- whose attained_by resolves to it, and an upper bound whose truth is a formal
-- claim.
--
-- WHY THIS DOES NOT EDIT 105. cert.bound_optimal reads attained_by and does not
-- know what a comb object is; teaching it would couple the bound tier to a
-- schema it should not depend on, and every layer here is additive by rule.
-- Instead the attainment is its OWN claim, and cert.derivation (85) composes
-- the two -- which is the mechanism the ledger already has for exactly this,
-- and which buys transitive taint from 102 for free: a refuted or revoked
-- attainment reaches the optimality claim without either layer knowing about
-- the other.
--
-- Idempotent; additive only.

-- ---------------------------------------------------------------------------
-- 1. Serialisation -- the object as a consumer receives it
-- ---------------------------------------------------------------------------

-- Everything needed to rebuild the structure elsewhere. Blocks carry their
-- positions so a digraph survives the round trip.
CREATE OR REPLACE FUNCTION comb.serialize_structure(p_subject TEXT)
RETURNS JSONB
LANGUAGE plpgsql STABLE AS $$
DECLARE s comb.structure%ROWTYPE;
BEGIN
    SELECT * INTO s FROM comb.structure WHERE subject_id = p_subject;
    IF s.id IS NULL THEN
        RAISE EXCEPTION 'comb: no structure %', p_subject;
    END IF;
    RETURN jsonb_build_object(
        'witness_type', 'comb_structure',
        'subject_id', s.subject_id,
        'kind', s.kind,
        'ground_n', s.ground_n,
        'canon_digest', s.canon_digest,
        'canon_tool', s.canon_tool,
        'blocks', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'idx', b.idx,
                       'elements', (SELECT COALESCE(jsonb_agg(i.element_idx
                                                    ORDER BY i.element_idx), '[]'::jsonb)
                                      FROM comb.incidence i
                                     WHERE i.structure_id = s.id AND i.block_idx = b.idx),
                       'positions', (SELECT jsonb_agg(i.position ORDER BY i.element_idx)
                                       FROM comb.incidence i
                                      WHERE i.structure_id = s.id AND i.block_idx = b.idx
                                        AND i.position IS NOT NULL))
                   ORDER BY b.idx)
              FROM comb.block b WHERE b.structure_id = s.id), '[]'::jsonb));
END
$$;

-- Coordinates travel as num/den over the power basis, with the minimal
-- polynomial alongside them, because a coordinate vector means nothing without
-- the field it is written in.
CREATE OR REPLACE FUNCTION comb.serialize_configuration(p_subject TEXT)
RETURNS JSONB
LANGUAGE plpgsql STABLE AS $$
DECLARE c comb.configuration%ROWTYPE;
BEGIN
    SELECT * INTO c FROM comb.configuration WHERE subject_id = p_subject;
    IF c.id IS NULL THEN
        RAISE EXCEPTION 'comb: no configuration %', p_subject;
    END IF;
    RETURN jsonb_build_object(
        'witness_type', 'comb_configuration',
        'subject_id', c.subject_id,
        'dim', c.dim,
        'coord_domain', c.coord_domain,
        'min_poly', to_jsonb(comb.min_poly(p_subject)),
        'field', (SELECT name FROM comb.number_field WHERE id = c.field_id),
        'points', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                       'idx', p.idx,
                       'coords', (SELECT jsonb_agg(jsonb_build_object(
                                             'axis', co.axis,
                                             'num', to_jsonb(co.num),
                                             'den', co.den)
                                         ORDER BY co.axis)
                                    FROM comb.coordinate co
                                   WHERE co.configuration_id = c.id
                                     AND co.point_idx = p.idx))
                   ORDER BY p.idx)
              FROM comb.point p WHERE p.configuration_id = c.id), '[]'::jsonb));
END
$$;

-- ---------------------------------------------------------------------------
-- 2. Carried property claims
-- ---------------------------------------------------------------------------

-- One dispatcher rather than a witness_carry twin of every 111 probe. The
-- property name is checked against a fixed list before anything happens, so
-- there is no path from it into executable SQL.
CREATE OR REPLACE FUNCTION comb.carried_property(
    p_subject TEXT, p_property TEXT, p_arg INTEGER DEFAULT NULL
) RETURNS TABLE (ok BOOLEAN, evidence JSONB, witness JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE r RECORD;
BEGIN
    CASE p_property
        WHEN 'wellformed' THEN
            SELECT * INTO r FROM comb.wellformed(p_subject);
        WHEN 'primitive_family' THEN
            SELECT * INTO r FROM comb.is_primitive_family(p_subject);
        WHEN 'intersecting_family' THEN
            SELECT * INTO r FROM comb.is_intersecting_family(p_subject);
        WHEN 'triangle_free' THEN
            SELECT * INTO r FROM comb.is_triangle_free(p_subject);
        WHEN 'uniform' THEN
            IF p_arg IS NULL THEN
                RAISE EXCEPTION 'comb.carried_property: uniform needs a k';
            END IF;
            SELECT * INTO r FROM comb.is_uniform(p_subject, p_arg);
        ELSE
            RAISE EXCEPTION 'comb.carried_property: unknown property %', p_property;
    END CASE;

    ok := r.ok;
    evidence := r.evidence;
    -- The object travels whatever the verdict. A refuted construction is
    -- exactly the case where a reader most wants to see what was built.
    witness := comb.serialize_structure(p_subject)
               || jsonb_build_object('property', p_property, 'arg', p_arg);
    RETURN NEXT;
END
$$;

CREATE OR REPLACE FUNCTION comb.construction_claim(
    p_subject TEXT, p_property TEXT, p_arg INTEGER DEFAULT NULL
) RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE s comb.structure%ROWTYPE; v_stmt TEXT; v_probe TEXT; v_id BIGINT;
BEGIN
    SELECT * INTO s FROM comb.structure WHERE subject_id = p_subject;
    IF s.id IS NULL THEN
        RAISE EXCEPTION 'comb: no structure %', p_subject;
    END IF;
    IF p_property NOT IN ('wellformed', 'primitive_family', 'intersecting_family',
                          'triangle_free', 'uniform') THEN
        RAISE EXCEPTION 'comb.construction_claim: unknown property %', p_property;
    END IF;

    v_stmt := format('%L is a %s on %s elements with %s: %s%s [carried]',
                     p_subject, s.kind, s.ground_n,
                     (SELECT COUNT(*) FROM comb.block WHERE structure_id = s.id)
                         || ' blocks',
                     p_property,
                     CASE WHEN p_arg IS NULL THEN '' ELSE ' = ' || p_arg END);
    v_probe := format(
        'SELECT ok, evidence, witness FROM comb.carried_property(%L, %L, %s)',
        p_subject, p_property, COALESCE(p_arg::TEXT, 'NULL'));

    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind,
                            method, probe_sql)
    VALUES ('comb_construction',
            jsonb_build_object('structure', p_subject, 'property', p_property),
            v_stmt, 'computational', 'witness_carry', v_probe)
    ON CONFLICT (statement) DO UPDATE SET probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

-- The same for a point configuration realising a graph, where the witness is
-- the exact coordinates and the graph they realise.
CREATE OR REPLACE FUNCTION comb.carried_realisation(
    p_config TEXT, p_structure TEXT, p_faithful BOOLEAN DEFAULT true
) RETURNS TABLE (ok BOOLEAN, evidence JSONB, witness JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE r RECORD;
BEGIN
    SELECT * INTO r FROM comb.is_unit_distance(p_config, p_structure, p_faithful);
    ok := r.ok;
    evidence := r.evidence;
    witness := jsonb_build_object(
        'witness_type', 'comb_realisation',
        'faithful', p_faithful,
        'configuration', comb.serialize_configuration(p_config),
        'structure', comb.serialize_structure(p_structure));
    RETURN NEXT;
END
$$;

CREATE OR REPLACE FUNCTION comb.realisation_claim(
    p_config TEXT, p_structure TEXT, p_faithful BOOLEAN DEFAULT true
) RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE v_domain TEXT; v_stmt TEXT; v_probe TEXT; v_id BIGINT;
BEGIN
    SELECT coord_domain INTO v_domain FROM comb.configuration
     WHERE id = comb.configuration_id(p_config);
    PERFORM comb.structure_id(p_structure);

    v_stmt := format('%L realises %L as a%s unit-distance graph [carried]',
                     p_config, p_structure,
                     CASE WHEN p_faithful THEN ' faithful' ELSE '' END);
    v_probe := format(
        'SELECT ok, evidence, witness FROM comb.carried_realisation(%L, %L, %L)',
        p_config, p_structure, p_faithful);

    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind,
                            method, probe_sql)
    VALUES ('comb_construction',
            jsonb_build_object('configuration', p_config, 'structure', p_structure),
            v_stmt, 'computational', 'witness_carry', v_probe)
    ON CONFLICT (statement) DO UPDATE SET probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_id;

    -- Same shield as 112: an approximate configuration cannot go valid.
    PERFORM cert.set_domain(v_id, v_domain);
    RETURN v_id;
END
$$;

-- ---------------------------------------------------------------------------
-- 3. The bound bridge
-- ---------------------------------------------------------------------------

-- Point a bound's attained_by at a comb object. Refuses a subject that does not
-- exist, because the entire value of doing this instead of writing a note is
-- that the pointer resolves.
CREATE OR REPLACE FUNCTION comb.attain_bound(
    p_bound_id BIGINT, p_subject TEXT, p_object_kind TEXT DEFAULT 'structure'
) RETURNS cert.bound
LANGUAGE plpgsql AS $$
DECLARE v_row cert.bound%ROWTYPE; v_key TEXT;
BEGIN
    IF p_object_kind = 'structure' THEN
        PERFORM comb.structure_id(p_subject);
        v_key := 'comb_structure';
    ELSIF p_object_kind = 'configuration' THEN
        PERFORM comb.configuration_id(p_subject);
        v_key := 'comb_configuration';
    ELSE
        RAISE EXCEPTION 'comb.attain_bound: object kind must be structure or configuration';
    END IF;

    UPDATE cert.bound
       SET attained_by = COALESCE(attained_by, '{}'::jsonb)
                         || jsonb_build_object(v_key, p_subject)
     WHERE id = p_bound_id
    RETURNING * INTO v_row;
    IF v_row.id IS NULL THEN
        RAISE EXCEPTION 'comb.attain_bound: no bound %', p_bound_id;
    END IF;
    RETURN v_row;
END
$$;

-- Does this bound's attaining witness resolve to an object that is actually
-- here?
--
--   true   attained_by names a comb object and it exists
--   false  it names one that does not -- a dangling witness, which is worse
--          than none because it reads as evidence
--   NULL   attained_by is absent or is free-form JSON naming no comb object.
--          Nothing for this layer to say; not a failure of anything.
CREATE OR REPLACE FUNCTION comb.bound_attainment(p_bound_id BIGINT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE b cert.bound%ROWTYPE; v_s TEXT; v_c TEXT; v_found BOOLEAN;
BEGIN
    SELECT * INTO b FROM cert.bound WHERE id = p_bound_id;
    IF b.id IS NULL THEN
        ok := false;
        evidence := jsonb_build_object('reason', 'no such bound', 'bound_id', p_bound_id);
        RETURN NEXT; RETURN;
    END IF;

    v_s := b.attained_by ->> 'comb_structure';
    v_c := b.attained_by ->> 'comb_configuration';

    IF v_s IS NULL AND v_c IS NULL THEN
        ok := NULL;
        evidence := jsonb_build_object(
            'reason', 'the bound names no comb object as its attaining witness',
            'bound_id', p_bound_id, 'attained_by', b.attained_by,
            'status', 'nothing for this layer to check');
        RETURN NEXT; RETURN;
    END IF;

    IF v_s IS NOT NULL THEN
        SELECT EXISTS (SELECT 1 FROM comb.structure WHERE subject_id = v_s)
          INTO v_found;
    ELSE
        SELECT EXISTS (SELECT 1 FROM comb.configuration WHERE subject_id = v_c)
          INTO v_found;
    END IF;

    IF NOT v_found THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', 'the attaining witness names an object that is not here',
            'bound_id', p_bound_id, 'names', COALESCE(v_s, v_c));
        RETURN NEXT; RETURN;
    END IF;

    ok := true;
    evidence := jsonb_build_object(
        'bound_id', p_bound_id,
        'direction', b.direction, 'value', b.value,
        'attaining_object', COALESCE(v_s, v_c),
        'object_kind', CASE WHEN v_s IS NOT NULL THEN 'structure' ELSE 'configuration' END,
        'witness', CASE WHEN v_s IS NOT NULL
                        THEN comb.serialize_structure(v_s)
                        ELSE comb.serialize_configuration(v_c) END);
    RETURN NEXT;
END
$$;

CREATE OR REPLACE FUNCTION comb.attainment_claim(p_bound_id BIGINT)
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE b cert.bound%ROWTYPE; q TEXT; v_stmt TEXT; v_probe TEXT; v_id BIGINT;
BEGIN
    SELECT * INTO b FROM cert.bound WHERE id = p_bound_id;
    IF b.id IS NULL THEN
        RAISE EXCEPTION 'comb.attainment_claim: no bound %', p_bound_id;
    END IF;
    SELECT subject_id INTO q FROM cert.quantity WHERE id = b.quantity_id;

    v_stmt := format('the %s bound %s on %L is attained by a deposited object [bound #%s]',
                     b.direction, b.value, q, b.id);
    v_probe := format('SELECT ok, evidence FROM comb.bound_attainment(%s)', b.id);
    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind,
                            method, probe_sql)
    VALUES ('comb_attainment',
            jsonb_build_object('bound_id', b.id, 'quantity', q),
            v_stmt, 'computational', 'comp_sql', v_probe)
    ON CONFLICT (statement) DO UPDATE SET probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

-- Compose the two: the optimality claim of 105 now rests on the attainment
-- claim above, recorded as a derivation edge rather than as a change to either
-- layer. cert.derivation_valid_deep (102) walks it, so a refuted or revoked
-- attainment taints optimality without 105 knowing comb exists.
--
-- Returns the optimality claim. Idempotent: re-running does not stack edges.
CREATE OR REPLACE FUNCTION comb.attest_attainment(p_bound_id BIGINT)
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE v_attain BIGINT; v_optimal BIGINT; v_existing BIGINT;
BEGIN
    v_attain  := comb.attainment_claim(p_bound_id);
    v_optimal := cert.bound_optimality_claim(p_bound_id);

    SELECT id INTO v_existing FROM cert.derivation
     WHERE conclusion_id = v_optimal
       AND premise_ids @> ARRAY[v_attain]
       AND rule = 'comb_attainment';
    IF v_existing IS NULL THEN
        INSERT INTO cert.derivation (conclusion_id, premise_ids, rule)
        VALUES (v_optimal, ARRAY[v_attain], 'comb_attainment');
    END IF;

    RETURN v_optimal;
END
$$;

COMMENT ON FUNCTION comb.attest_attainment(BIGINT) IS
    'Bridges T2 to T3: mints the attainment claim for a bound whose attained_by '
    'points at a comb object, mints the 105 optimality claim, and records a '
    'derivation edge from the second to the first. Composition rather than '
    'coupling -- 105 never learns what a comb object is, and 102 gives the '
    'transitive taint for free.';
