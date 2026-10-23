-- Finite certificates: portable instances, snapshot-bound claims, supplied
-- witnesses, structure transformations, and typed extremal objectives.
-- No search is performed and no canonical-labelling tool is trusted here.

CREATE OR REPLACE FUNCTION comb.instance_digest(p_instance JSONB)
RETURNS TEXT LANGUAGE sql IMMUTABLE AS $$
    SELECT encode(sha256(convert_to('trunkit-comb-instance-v1' || chr(31) ||
        cert.claim_canonical_json(p_instance), 'UTF8')), 'hex')
$$;

CREATE OR REPLACE FUNCTION comb.structure_instance(p_subject TEXT)
RETURNS JSONB LANGUAGE plpgsql STABLE AS $$
DECLARE s comb.structure%ROWTYPE;
BEGIN
    SELECT * INTO s FROM comb.structure WHERE id = comb.structure_id(p_subject);
    RETURN jsonb_build_object('kind',s.kind,'ground_n',s.ground_n,
        'elements',COALESCE((SELECT jsonb_agg(e.idx ORDER BY e.idx)
            FROM comb.element e WHERE e.structure_id=s.id),'[]'::jsonb),
        'blocks',COALESCE((SELECT jsonb_agg(jsonb_build_object(
            'idx',b.idx,
            'elements',COALESCE((SELECT jsonb_agg(i.element_idx ORDER BY i.element_idx)
                FROM comb.incidence i WHERE i.structure_id=s.id AND i.block_idx=b.idx),'[]'::jsonb),
            'positions',(SELECT CASE WHEN bool_or(i.position IS NOT NULL)
                THEN jsonb_agg(i.position ORDER BY i.element_idx) ELSE 'null'::jsonb END
                FROM comb.incidence i WHERE i.structure_id=s.id AND i.block_idx=b.idx))
            ORDER BY b.idx) FROM comb.block b WHERE b.structure_id=s.id),'[]'::jsonb));
END $$;

CREATE OR REPLACE FUNCTION comb.primitive_instance(p_subject TEXT)
RETURNS JSONB LANGUAGE sql STABLE AS $$
    SELECT jsonb_build_object('kind','primitive_set','values',COALESCE(jsonb_agg(label ORDER BY
        CASE WHEN label ~ '^[1-9][0-9]*$' AND length(label)<=10000 THEN label::NUMERIC END,
        label),'[]'::jsonb)) FROM comb.element WHERE structure_id=comb.structure_id(p_subject)
$$;

CREATE OR REPLACE FUNCTION comb.check_primitive_certificate(p_subject TEXT,p_expected_digest TEXT DEFAULT NULL)
RETURNS TABLE(ok BOOLEAN,evidence JSONB,witness JSONB) LANGUAGE plpgsql STABLE AS $$
DECLARE r RECORD; v_instance JSONB; v_digest TEXT;
BEGIN
    v_instance:=comb.primitive_instance(p_subject); v_digest:=comb.instance_digest(v_instance);
    SELECT * INTO r FROM comb.is_primitive_set(p_subject);
    ok:=r.ok; evidence:=r.evidence||jsonb_build_object('instance_digest',v_digest);
    IF p_expected_digest IS NOT NULL AND p_expected_digest<>v_digest THEN
        ok:=false; evidence:=evidence||jsonb_build_object('reason','instance changed since claim was minted');
    END IF;
    witness:=comb.certificate_body(v_instance,jsonb_build_object('name','primitive_set'))
        ||jsonb_build_object('kind','primitive_set','structure',p_subject,'elements',v_instance->'values');
    RETURN NEXT;
END $$;

CREATE OR REPLACE FUNCTION comb.carried_primitive_set(p_subject TEXT)
RETURNS TABLE(ok BOOLEAN,evidence JSONB,witness JSONB) LANGUAGE sql STABLE AS $$
    SELECT * FROM comb.check_primitive_certificate(p_subject)
$$;

CREATE OR REPLACE FUNCTION comb.primitive_set_claim(p_subject TEXT)
RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE v_id BIGINT; v_digest TEXT; p JSONB:=jsonb_build_object('name','primitive_set');
BEGIN
    v_digest:=comb.instance_digest(comb.primitive_instance(p_subject));
    INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql,domain)
    VALUES('comb_construction',jsonb_build_object('structure',p_subject,'witness_schema','comb.v1',
        'predicate',p,'instance_digest',v_digest,'certificate','{}'::jsonb),
        format('the finite integer set %L is primitive [instance %s]',p_subject,v_digest),
        'computational','witness_carry',format('SELECT ok,evidence,witness FROM comb.check_primitive_certificate(%L,%L)',
        p_subject,v_digest),'exact_int')
    ON CONFLICT(statement) DO UPDATE SET probe_sql=EXCLUDED.probe_sql RETURNING id INTO v_id;
    RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION comb.check_realisation(p_config TEXT,p_subject TEXT,p_faithful BOOLEAN DEFAULT true,
                                                 p_expected_digest TEXT DEFAULT NULL)
RETURNS TABLE(ok BOOLEAN,evidence JSONB,witness JSONB) LANGUAGE plpgsql STABLE AS $$
DECLARE r RECORD; v_instance JSONB; v_digest TEXT; p JSONB;
BEGIN
    v_instance:=comb.realisation_instance(p_config,p_subject); v_digest:=comb.instance_digest(v_instance);
    p:=jsonb_build_object('name','unit_distance','faithful',p_faithful);
    SELECT * INTO r FROM comb.is_unit_distance(p_config,p_subject,p_faithful);
    ok:=r.ok; evidence:=r.evidence||jsonb_build_object('instance_digest',v_digest);
    IF p_expected_digest IS NOT NULL AND p_expected_digest<>v_digest THEN
        ok:=false; evidence:=evidence||jsonb_build_object('reason','instance changed since claim was minted');
    END IF;
    witness:=comb.certificate_body(v_instance,p)||jsonb_build_object('witness_type','comb_realisation',
        'faithful',p_faithful,'configuration',comb.serialize_configuration(p_config),
        'structure',comb.serialize_structure(p_subject));
    RETURN NEXT;
END $$;

CREATE OR REPLACE FUNCTION comb.carried_realisation(p_config TEXT,p_structure TEXT,p_faithful BOOLEAN DEFAULT true)
RETURNS TABLE(ok BOOLEAN,evidence JSONB,witness JSONB) LANGUAGE sql STABLE AS $$
    SELECT * FROM comb.check_realisation(p_config,p_structure,p_faithful)
$$;

CREATE OR REPLACE FUNCTION comb.realisation_claim(p_config TEXT,p_structure TEXT,p_faithful BOOLEAN DEFAULT true)
RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE v_id BIGINT; v_digest TEXT; p JSONB; v_domain TEXT;
BEGIN
    v_digest:=comb.instance_digest(comb.realisation_instance(p_config,p_structure));
    p:=jsonb_build_object('name','unit_distance','faithful',p_faithful);
    SELECT coord_domain INTO v_domain FROM comb.configuration WHERE id=comb.configuration_id(p_config);
    INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql,domain)
    VALUES('comb_construction',jsonb_build_object('configuration',p_config,'structure',p_structure,
        'witness_schema','comb.v1','predicate',p,'instance_digest',v_digest,'certificate','{}'::jsonb),
        format('%L realises %L as a unit-distance graph [faithful %s; instance %s]',
            p_config,p_structure,p_faithful,v_digest),'computational','witness_carry',
        format('SELECT ok,evidence,witness FROM comb.check_realisation(%L,%L,%L,%L)',
            p_config,p_structure,p_faithful,v_digest),v_domain)
    ON CONFLICT(statement) DO UPDATE SET probe_sql=EXCLUDED.probe_sql RETURNING id INTO v_id;
    RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION comb.configuration_instance(p_subject TEXT)
RETURNS JSONB LANGUAGE sql STABLE AS $$
    SELECT comb.serialize_configuration(p_subject) - 'witness_type' - 'subject_id' - 'field'
$$;

CREATE OR REPLACE FUNCTION comb.realisation_instance(p_config TEXT,p_subject TEXT)
RETURNS JSONB LANGUAGE sql STABLE AS $$
    SELECT jsonb_build_object('kind','realisation','structure',comb.structure_instance(p_subject),
        'configuration',comb.configuration_instance(p_config))
$$;

CREATE OR REPLACE FUNCTION comb.structure_digest(p_subject TEXT)
RETURNS TEXT LANGUAGE sql STABLE AS $$
    SELECT comb.instance_digest(comb.structure_instance(p_subject))
$$;

CREATE OR REPLACE FUNCTION comb.certificate_body(p_instance JSONB,p_predicate JSONB,
                                                p_certificate JSONB DEFAULT '{}')
RETURNS JSONB LANGUAGE sql IMMUTABLE AS $$
    SELECT jsonb_build_object('schema','comb.v1','instance',p_instance,
        'instance_digest',comb.instance_digest(p_instance),'predicate',p_predicate,
        'certificate',p_certificate)
$$;

CREATE OR REPLACE FUNCTION comb.json_int_array(p_value JSONB)
RETURNS INTEGER[] LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE result INTEGER[];
BEGIN
    IF p_value IS NULL OR jsonb_typeof(p_value)<>'array' OR EXISTS(
        SELECT 1 FROM jsonb_array_elements(p_value) x
         WHERE jsonb_typeof(x)<>'number' OR x::TEXT !~ '^-?[0-9]+$') THEN
        RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='expected an array of JSON integers';
    END IF;
    SELECT COALESCE(array_agg(x.value::INTEGER ORDER BY x.ord),'{}'::INTEGER[]) INTO result
      FROM jsonb_array_elements_text(p_value) WITH ORDINALITY x(value,ord);
    RETURN result;
END $$;

CREATE OR REPLACE FUNCTION comb.instance_wellformed(p_instance JSONB)
RETURNS BOOLEAN LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE n INTEGER; kind TEXT; es INTEGER[]; verts INTEGER[]; positions INTEGER[];
        b JSONB; signatures TEXT[]:='{}'; sig TEXT; idx INTEGER; v_prior_idx INTEGER:=-1; work BIGINT:=0;
BEGIN
    IF jsonb_typeof(p_instance) IS DISTINCT FROM 'object'
       OR NOT (p_instance ?& ARRAY['kind','ground_n','elements','blocks'])
       OR p_instance-ARRAY['kind','ground_n','elements','blocks']<>'{}'::jsonb THEN RETURN false; END IF;
    n:=(comb.json_int_array(jsonb_build_array(p_instance->'ground_n')))[1];
    kind:=p_instance->>'kind';
    IF n<0 OR n>10000 OR kind IS NULL OR kind NOT IN ('graph','digraph','hypergraph','set_system') THEN RETURN false; END IF;
    es:=comb.json_int_array(p_instance->'elements');
    IF es<>ARRAY(SELECT generate_series(0,n-1)) THEN RETURN false; END IF;
    IF jsonb_typeof(p_instance->'blocks') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
    IF jsonb_array_length(p_instance->'blocks')>200000 THEN RETURN false; END IF;
    IF kind IN ('graph','digraph') AND jsonb_array_length(p_instance->'blocks')::BIGINT*
        jsonb_array_length(p_instance->'blocks')>2000000 THEN RETURN false; END IF;
    FOR b IN SELECT * FROM jsonb_array_elements(p_instance->'blocks') LOOP
        IF jsonb_typeof(b) IS DISTINCT FROM 'object' OR NOT (b ?& ARRAY['idx','elements','positions'])
           OR b-ARRAY['idx','elements','positions']<>'{}'::jsonb THEN RETURN false; END IF;
        idx:=(comb.json_int_array(jsonb_build_array(b->'idx')))[1];
        IF idx<=v_prior_idx THEN RETURN false; END IF;
        v_prior_idx:=idx;
        verts:=comb.json_int_array(b->'elements'); work:=work+cardinality(verts);
        IF work>2000000 OR verts<>ARRAY(SELECT DISTINCT x FROM unnest(verts) x ORDER BY x)
           OR EXISTS(SELECT 1 FROM unnest(verts) x WHERE x<0 OR x>=n) THEN RETURN false; END IF;
        positions:=NULL;
        IF b->'positions' IS DISTINCT FROM 'null'::jsonb THEN positions:=comb.json_int_array(b->'positions'); END IF;
        IF kind IN ('graph','digraph') AND cardinality(verts)<>2 THEN RETURN false; END IF;
        IF kind='digraph' THEN
            IF positions IS NULL OR ARRAY(SELECT x FROM unnest(positions) x ORDER BY x)<>ARRAY[0,1] THEN RETURN false; END IF;
        ELSIF positions IS NOT NULL THEN RETURN false;
        END IF;
        sig:=cert.claim_canonical_json(jsonb_build_array(to_jsonb(verts),to_jsonb(positions)));
        IF kind IN ('graph','digraph') AND sig=ANY(signatures) THEN RETURN false; END IF;
        IF kind IN ('graph','digraph') THEN signatures:=array_append(signatures,sig); END IF;
    END LOOP;
    RETURN true;
EXCEPTION WHEN invalid_text_representation OR invalid_parameter_value OR numeric_value_out_of_range THEN RETURN false;
END $$;

CREATE OR REPLACE FUNCTION comb.ground_subsets(p_n INTEGER,p_t INTEGER)
RETURNS SETOF INTEGER[] LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE v_total NUMERIC:=1; i INTEGER;
BEGIN
    IF p_t IS NULL OR p_t<0 OR p_t>p_n THEN
        RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='require 0 <= t <= ground_n'; END IF;
    FOR i IN 1..least(p_t,p_n-p_t) LOOP
        v_total:=div(v_total*(p_n-i+1),i);
        IF v_total>100000 THEN
            RAISE EXCEPTION USING ERRCODE='54000',MESSAGE='finite subset verification budget exceeded'; END IF;
    END LOOP;
    IF v_total*(p_t+1)>2000000 THEN
        RAISE EXCEPTION USING ERRCODE='54000',MESSAGE='finite subset arity verification budget exceeded'; END IF;
    RETURN QUERY WITH RECURSIVE subsets(a,next_i) AS (
        SELECT '{}'::INTEGER[],0
        UNION ALL
        SELECT a||g,g+1 FROM subsets
          CROSS JOIN LATERAL generate_series(next_i,p_n-(p_t-cardinality(a))) g
         WHERE cardinality(a)<p_t
    ) SELECT a FROM subsets WHERE cardinality(a)=p_t;
END $$;

CREATE OR REPLACE FUNCTION comb.family_property(p_subject TEXT,p_predicate JSONB)
RETURNS TABLE(ok BOOLEAN,evidence JSONB) LANGUAGE plpgsql STABLE AS $$
DECLARE v_name TEXT:=p_predicate->>'name'; v_t INTEGER; v_n INTEGER; v_k INTEGER;
        v_expected INTEGER; v_actual INTEGER; v_vertices INTEGER[]; v_blocks INTEGER; a INTEGER[];
BEGIN
    SELECT ground_n INTO v_n FROM comb.structure WHERE id=comb.structure_id(p_subject);
    SELECT count(*) INTO v_blocks FROM comb.block_sets(p_subject);
    IF v_name='codegree' THEN
        v_vertices:=comb.json_int_array(p_predicate->'vertices');
        IF cardinality(v_vertices)<>(SELECT count(DISTINCT x) FROM unnest(v_vertices) x)
           OR EXISTS(SELECT 1 FROM unnest(v_vertices) x WHERE x<0 OR x>=v_n) THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='codegree vertices must form a ground subset'; END IF;
        v_expected:=(p_predicate->>'value')::INTEGER;
        IF v_expected IS NULL OR v_expected<0 THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='codegree value must be nonnegative'; END IF;
        SELECT count(*) INTO v_actual FROM comb.block_sets(p_subject) b WHERE v_vertices <@ b.elements;
        ok:=v_actual=v_expected; evidence:=jsonb_build_object('codegree',v_actual); RETURN NEXT; RETURN;
    END IF;
    v_t:=(p_predicate->>'t')::INTEGER;
    IF v_t IS NULL OR v_t<0 THEN
        RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='t must be nonnegative'; END IF;
    IF v_name='t_intersecting' THEN
        IF v_blocks::BIGINT*v_blocks>2000000 THEN
            RAISE EXCEPTION USING ERRCODE='54000',MESSAGE='pair verification budget exceeded'; END IF;
        ok:=NOT EXISTS(SELECT 1 FROM comb.block_sets(p_subject) b WHERE b.size<v_t)
            AND NOT EXISTS(SELECT 1 FROM comb.block_sets(p_subject) b JOIN comb.block_sets(p_subject) c
                ON b.block_idx<c.block_idx WHERE
                (SELECT count(*) FROM unnest(b.elements) x WHERE x=ANY(c.elements))<v_t);
        evidence:=jsonb_build_object('checked_pairs',v_blocks::BIGINT*(v_blocks-1)/2); RETURN NEXT; RETURN;
    END IF;
    v_expected:=(p_predicate->>CASE WHEN v_name='t_design' THEN 'lambda' ELSE 'max' END)::INTEGER;
    IF v_expected IS NULL OR v_expected<0 THEN
        RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='multiplicity must be nonnegative'; END IF;
    IF v_name='t_design' THEN
        v_k:=(p_predicate->>'k')::INTEGER;
        IF v_k IS NULL OR v_t>v_k OR v_k>v_n THEN
            RAISE EXCEPTION USING ERRCODE='22023',MESSAGE='design requires 0 <= t <= k <= ground_n'; END IF;
        IF EXISTS(SELECT 1 FROM comb.block_sets(p_subject) b WHERE b.size<>v_k) THEN
            ok:=false; evidence:=jsonb_build_object('reason','not k-uniform'); RETURN NEXT; RETURN; END IF;
    END IF;
    v_actual:=0;
    FOR a IN SELECT * FROM comb.ground_subsets(v_n,v_t) LOOP
        v_actual:=v_actual+1;
        IF v_actual::BIGINT*greatest(v_blocks,1)>2000000 THEN
            RAISE EXCEPTION USING ERRCODE='54000',MESSAGE='codegree verification budget exceeded'; END IF;
        SELECT count(*) INTO v_k FROM comb.block_sets(p_subject) b WHERE a <@ b.elements;
        IF (v_name='t_design' AND v_k<>v_expected) OR (v_name='codegree_at_most' AND v_k>v_expected) THEN
            ok:=false; evidence:=jsonb_build_object('violating_subset',to_jsonb(a),'codegree',v_k); RETURN NEXT; RETURN;
        END IF;
    END LOOP;
    ok:=true; evidence:=jsonb_build_object('checked_subsets',v_actual); RETURN NEXT;
END $$;

-- All producer data is checked against this deposited instance. An absent
-- witness proves nothing; malformed instances are unknown except when the
-- predicate is precisely wellformed. Certificates below contain no solver.
CREATE OR REPLACE FUNCTION comb.check_certificate(
    p_subject TEXT,p_predicate JSONB,p_certificate JSONB DEFAULT '{}',
    p_expected_digest TEXT DEFAULT NULL
) RETURNS TABLE(ok BOOLEAN,evidence JSONB,witness JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_instance JSONB; v_digest TEXT; v_name TEXT; v_kind TEXT; v_sid BIGINT;
    r RECORD; v_vertices INTEGER[]; v_selected INTEGER[]; v_colours INTEGER[];
    v_n INTEGER; v_bad INTEGER; v_count INTEGER; v_i INTEGER; v_k INTEGER;
    v_target JSONB; v_mapping INTEGER[]; v_left JSONB; v_right JSONB; v_allowed TEXT[];
BEGIN
    v_instance := comb.structure_instance(p_subject);
    v_digest := comb.instance_digest(v_instance);
    v_name := p_predicate->>'name'; v_kind := v_instance->>'kind';
    v_sid := comb.structure_id(p_subject); v_n := (v_instance->>'ground_n')::INTEGER;
    witness := comb.certificate_body(v_instance,p_predicate,p_certificate)
        || comb.serialize_structure(p_subject)
        || jsonb_build_object('property',v_name,'arg',p_predicate->'k');
    evidence := jsonb_build_object('structure',p_subject,'predicate',p_predicate,
        'instance_digest',v_digest);
    v_allowed:=CASE v_name
        WHEN 'uniform' THEN ARRAY['name','k']
        WHEN 'colouring' THEN ARRAY['name','k']
        WHEN 'independent_set' THEN ARRAY['name','k']
        WHEN 'clique' THEN ARRAY['name','k']
        WHEN 'matching' THEN ARRAY['name','k']
        WHEN 'vertex_cover' THEN ARRAY['name','k']
        WHEN 'hitting_set' THEN ARRAY['name','k']
        WHEN 'isomorphism' THEN ARRAY['name','target_digest']
        WHEN 't_intersecting' THEN ARRAY['name','t']
        WHEN 'codegree' THEN ARRAY['name','vertices','value']
        WHEN 'codegree_at_most' THEN ARRAY['name','t','max']
        WHEN 't_design' THEN ARRAY['name','t','k','lambda']
        ELSE ARRAY['name'] END;
    IF jsonb_typeof(p_predicate) IS DISTINCT FROM 'object' OR p_predicate-v_allowed<>'{}'::jsonb
       OR jsonb_typeof(p_certificate) IS DISTINCT FROM 'object' THEN
        ok:=NULL; evidence:=evidence||jsonb_build_object('reason','unsupported predicate parameters or malformed certificate');
        RETURN NEXT; RETURN;
    END IF;
    IF EXISTS(SELECT 1 FROM jsonb_each(p_predicate) j WHERE j.key IN ('k','t','max','value','lambda')
        AND (jsonb_typeof(j.value)<>'number' OR j.value::TEXT !~ '^[0-9]+$')) THEN
        ok:=NULL; evidence:=evidence||jsonb_build_object('reason','predicate parameters must be nonnegative JSON integers');
        RETURN NEXT; RETURN;
    END IF;
    IF p_expected_digest IS NOT NULL AND p_expected_digest <> v_digest THEN
        ok := false; evidence := evidence || jsonb_build_object('reason','instance changed since claim was minted',
            'expected_digest',p_expected_digest); RETURN NEXT; RETURN;
    END IF;
    SELECT * INTO r FROM comb.wellformed(p_subject);
    IF r.ok IS DISTINCT FROM true THEN
        ok := CASE WHEN v_name='wellformed' THEN false ELSE NULL END;
        evidence := evidence || jsonb_build_object('reason','malformed instance','detail',r.evidence);
        RETURN NEXT; RETURN;
    END IF;
    CASE v_name
      WHEN 'wellformed' THEN ok := true;
      WHEN 'uniform' THEN
        v_k:=(p_predicate->>'k')::INTEGER;
        IF v_k IS NULL OR v_k<0 THEN ok:=NULL;
        ELSE SELECT * INTO r FROM comb.is_uniform(p_subject,v_k); ok:=r.ok; END IF;
      WHEN 'primitive_family' THEN SELECT * INTO r FROM comb.is_primitive_family(p_subject); ok:=r.ok;
      WHEN 'intersecting_family' THEN SELECT * INTO r FROM comb.is_intersecting_family(p_subject); ok:=r.ok;
      WHEN 'triangle_free' THEN SELECT * INTO r FROM comb.is_triangle_free(p_subject); ok:=r.ok;
      WHEN 't_intersecting','codegree','codegree_at_most','t_design' THEN
        SELECT * INTO r FROM comb.family_property(p_subject,p_predicate); ok:=r.ok;
      WHEN 'colouring' THEN
        IF jsonb_typeof(p_certificate->'colouring') <> 'array' THEN
            ok:=NULL; evidence:=evidence||jsonb_build_object('reason','colouring must be an array'); RETURN NEXT; RETURN;
        END IF;
        v_colours:=comb.json_int_array(p_certificate->'colouring');
        IF cardinality(v_colours) <> v_n THEN ok:=false;
        ELSE
            SELECT count(*) INTO v_bad FROM comb.block_sets(p_subject) b
             WHERE b.size=0 OR (SELECT count(DISTINCT v_colours[u+1]) FROM unnest(b.elements) u)=1;
            SELECT count(DISTINCT u) INTO v_count FROM unnest(v_colours) u;
            v_k := (p_predicate->>'k')::INTEGER;
            ok := CASE WHEN v_k IS NULL OR v_k<0 THEN NULL ELSE v_bad=0 AND v_count<=v_k END;
            IF EXISTS(SELECT 1 FROM unnest(v_colours) x WHERE x<0 OR x>=v_k) THEN ok:=NULL; END IF;
            evidence := evidence||jsonb_build_object('colours',v_count,'monochromatic_blocks',v_bad);
        END IF;
      WHEN 'independent_set','clique','vertex_cover','hitting_set','odd_cycle' THEN
        IF jsonb_typeof(p_certificate->'vertices') <> 'array' THEN
            ok:=NULL; evidence:=evidence||jsonb_build_object('reason','vertices must be an array'); RETURN NEXT; RETURN;
        END IF;
        v_vertices:=comb.json_int_array(p_certificate->'vertices');
        SELECT count(DISTINCT x) INTO v_count FROM unnest(v_vertices) x;
        IF v_count <> cardinality(v_vertices) OR EXISTS (
            SELECT 1 FROM unnest(v_vertices) x WHERE x<0 OR x>=v_n) THEN
            ok:=NULL; evidence:=evidence||jsonb_build_object('reason','vertices are repeated or outside the ground set');
            RETURN NEXT; RETURN;
        END IF;
        IF v_name IN ('clique','vertex_cover','odd_cycle') AND v_kind<>'graph' THEN
            ok:=NULL; evidence:=evidence||jsonb_build_object('reason','predicate requires a simple graph'); RETURN NEXT; RETURN;
        END IF;
        IF v_name='independent_set' THEN
            ok:=NOT EXISTS (SELECT 1 FROM comb.block_sets(p_subject) b WHERE b.elements <@ v_vertices);
        ELSIF v_name IN ('vertex_cover','hitting_set') THEN
            ok:=NOT EXISTS (SELECT 1 FROM comb.block_sets(p_subject) b WHERE NOT (b.elements && v_vertices));
        ELSIF v_name='clique' THEN
            ok:=NOT EXISTS (SELECT 1 FROM unnest(v_vertices) a CROSS JOIN unnest(v_vertices) b
                WHERE a<b AND NOT EXISTS(SELECT 1 FROM comb.edge e
                    WHERE e.structure_id=v_sid AND e.u=a AND e.v=b));
        ELSE
            IF v_count<3 OR v_count%2=0 THEN ok:=false;
            ELSE
                ok:=true;
                FOR v_i IN 1..v_count LOOP
                    IF NOT EXISTS (SELECT 1 FROM comb.edge e WHERE e.structure_id=v_sid
                        AND e.u=least(v_vertices[v_i],v_vertices[v_i%v_count+1])
                        AND e.v=greatest(v_vertices[v_i],v_vertices[v_i%v_count+1])) THEN ok:=false; EXIT; END IF;
                END LOOP;
            END IF;
        END IF;
        evidence:=evidence||jsonb_build_object('size',v_count);
        IF p_predicate ? 'k' THEN
            v_k:=(p_predicate->>'k')::INTEGER;
            IF v_k<0 THEN ok:=NULL;
            ELSIF v_name IN ('independent_set','clique') THEN ok:=ok AND v_count>=v_k;
            ELSE ok:=ok AND v_count<=v_k; END IF;
        ELSIF v_name='clique' THEN ok:=NULL;
        END IF;
      WHEN 'matching' THEN
        IF v_kind<>'graph' THEN ok:=NULL; RETURN NEXT; RETURN; END IF;
        IF jsonb_typeof(p_certificate->'matching') <> 'array' THEN
            ok:=NULL; evidence:=evidence||jsonb_build_object('reason','matching must be an array of block indices'); RETURN NEXT; RETURN;
        END IF;
        v_selected:=comb.json_int_array(p_certificate->'matching');
        SELECT count(DISTINCT x) INTO v_count FROM unnest(v_selected) x;
        ok:=v_count=cardinality(v_selected) AND NOT EXISTS(
            SELECT 1 FROM unnest(v_selected) x WHERE NOT EXISTS(
                SELECT 1 FROM comb.block b WHERE b.structure_id=v_sid AND b.idx=x))
            AND NOT EXISTS(SELECT 1 FROM comb.incidence i WHERE i.structure_id=v_sid
                AND i.block_idx=ANY(v_selected) GROUP BY i.element_idx HAVING count(*)>1);
        evidence:=evidence||jsonb_build_object('size',v_count);
        v_k:=(p_predicate->>'k')::INTEGER;
        ok:=CASE WHEN v_k IS NULL OR v_k<0 THEN NULL ELSE ok AND v_count>=v_k END;
      WHEN 'isomorphism' THEN
        v_target:=p_certificate->'target';
        IF v_target IS NULL OR jsonb_typeof(v_target)<>'object'
           OR NOT comb.instance_wellformed(v_target)
           OR p_predicate->>'target_digest' IS DISTINCT FROM comb.instance_digest(v_target) THEN
            ok:=NULL; evidence:=evidence||jsonb_build_object('reason','target digest missing or inconsistent'); RETURN NEXT; RETURN;
        END IF;
        v_mapping:=comb.json_int_array(p_certificate->'mapping');
        SELECT count(DISTINCT x) INTO v_count FROM unnest(v_mapping) x;
        IF v_target->>'kind' IS DISTINCT FROM v_kind OR (v_target->>'ground_n')::INTEGER<>v_n
           OR cardinality(v_mapping)<>v_n OR v_count<>v_n
           OR EXISTS (SELECT 1 FROM unnest(v_mapping) x WHERE x<0 OR x>=v_n) THEN
            ok:=NULL; RETURN NEXT; RETURN;
        END IF;
        -- Compare multisets of incidence signatures; block indices are local
        -- labels, while multiplicity and direction are structural information.
        SELECT COALESCE(jsonb_agg(sig ORDER BY sig::TEXT),'[]') INTO v_left FROM (
            SELECT (SELECT COALESCE(jsonb_agg(jsonb_build_array(v_mapping[i.element_idx+1],i.position)
                ORDER BY v_mapping[i.element_idx+1]),'[]') FROM comb.incidence i
                WHERE i.structure_id=v_sid AND i.block_idx=b.idx) AS sig
            FROM comb.block b WHERE b.structure_id=v_sid) z;
        SELECT COALESCE(jsonb_agg(sig ORDER BY sig::TEXT),'[]') INTO v_right FROM (
            SELECT (SELECT COALESCE(jsonb_agg(jsonb_build_array((e.value)::INTEGER,
                CASE WHEN b.value->'positions'='null'::jsonb THEN 'null'::jsonb
                     ELSE b.value->'positions'->(e.ord::INTEGER-1) END)
                ORDER BY (e.value)::INTEGER),'[]')
                FROM jsonb_array_elements_text(b.value->'elements') WITH ORDINALITY e(value,ord)) AS sig
            FROM jsonb_array_elements(v_target->'blocks') b(value)) z;
        ok:=v_left=v_right;
      ELSE
        ok:=NULL; evidence:=evidence||jsonb_build_object('reason','unsupported predicate');
    END CASE;
    IF ok=false AND v_name IN ('colouring','independent_set','clique','odd_cycle','matching','vertex_cover','hitting_set','isomorphism') THEN
        ok:=NULL; evidence:=evidence||jsonb_build_object('certificate_ok',false,
            'reason','candidate rejected; proposition remains unverified');
    END IF;
    IF r.evidence IS NOT NULL THEN evidence:=evidence||jsonb_build_object('detail',r.evidence); END IF;
    RETURN NEXT;
EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range OR invalid_parameter_value OR program_limit_exceeded THEN
    ok:=NULL; evidence:=jsonb_build_object('reason','malformed certificate or predicate','detail',SQLERRM);
    witness:=comb.certificate_body(comb.structure_instance(p_subject),p_predicate,p_certificate);
    RETURN NEXT;
END $$;

CREATE OR REPLACE FUNCTION comb.certificate_claim(p_subject TEXT,p_predicate JSONB,
                                                  p_certificate JSONB DEFAULT '{}')
RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE v_digest TEXT; v_id BIGINT; v_stmt TEXT; v_probe TEXT; r RECORD;
BEGIN
    v_digest:=comb.structure_digest(p_subject);
    SELECT * INTO r FROM comb.check_certificate(p_subject,p_predicate,p_certificate,v_digest);
    IF p_predicate->>'name' NOT IN ('wellformed','uniform','primitive_family','intersecting_family',
        'triangle_free','t_intersecting','codegree','codegree_at_most','t_design',
        'colouring','independent_set','clique','odd_cycle','matching','vertex_cover','hitting_set','isomorphism') THEN
        RAISE EXCEPTION 'comb.certificate_claim: unknown property %',p_predicate->>'name';
    END IF;
    v_stmt:=format('comb %L satisfies %s [instance %s]',p_subject,
        cert.claim_canonical_json(p_predicate),v_digest);
    v_probe:=format('SELECT ok,evidence,witness FROM comb.check_certificate(%L,%L::jsonb,%L::jsonb,%L)',
        p_subject,p_predicate::TEXT,p_certificate::TEXT,v_digest);
    INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql,domain)
    VALUES('comb_construction',jsonb_build_object('structure',p_subject,'witness_schema','comb.v1',
        'predicate',p_predicate,'instance_digest',v_digest,'certificate',p_certificate),
        v_stmt,'computational','witness_carry',v_probe,'exact_int')
    ON CONFLICT(statement) DO UPDATE SET probe_sql=EXCLUDED.probe_sql
    RETURNING id INTO v_id;
    RETURN v_id;
END $$;

-- Preserve the public construction API while committing all stated size and
-- structure data. Legacy display fields remain outside the normalized instance.
CREATE OR REPLACE FUNCTION comb.carried_property(p_subject TEXT,p_property TEXT,p_arg INTEGER DEFAULT NULL)
RETURNS TABLE(ok BOOLEAN,evidence JSONB,witness JSONB) LANGUAGE plpgsql STABLE AS $$
DECLARE p JSONB; r RECORD;
BEGIN
    IF p_property='uniform' AND p_arg IS NULL THEN RAISE EXCEPTION 'comb.carried_property: uniform needs a k'; END IF;
    p:=jsonb_build_object('name',p_property);
    IF p_arg IS NOT NULL THEN p:=p||jsonb_build_object('k',p_arg); END IF;
    SELECT * INTO r FROM comb.check_certificate(p_subject,p);
    ok:=r.ok; evidence:=r.evidence;
    witness:=r.witness||comb.serialize_structure(p_subject)||jsonb_build_object('property',p_property,'arg',p_arg);
    RETURN NEXT;
END $$;

CREATE OR REPLACE FUNCTION comb.construction_claim(p_subject TEXT,p_property TEXT,p_arg INTEGER DEFAULT NULL)
RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE p JSONB; v_id BIGINT;
BEGIN
    IF p_property NOT IN ('wellformed','primitive_family','intersecting_family','triangle_free','uniform') THEN
        RAISE EXCEPTION 'comb.construction_claim: unknown property %',p_property;
    END IF;
    IF p_property='uniform' AND p_arg IS NULL THEN RAISE EXCEPTION 'comb.construction_claim: uniform needs a k'; END IF;
    p:=jsonb_build_object('name',p_property);
    IF p_arg IS NOT NULL THEN p:=p||jsonb_build_object('k',p_arg); END IF;
    v_id:=comb.certificate_claim(p_subject,p);
    RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION comb.subset_certificate_claim(p_subject TEXT,p_name TEXT,p_vertices INTEGER[])
RETURNS BIGINT LANGUAGE sql AS $$
    SELECT comb.certificate_claim(p_subject,jsonb_build_object('name',p_name)||
        CASE WHEN p_name='clique' THEN jsonb_build_object('k',cardinality(p_vertices)) ELSE '{}'::jsonb END,
        jsonb_build_object('vertices',to_jsonb(p_vertices)))
$$;
CREATE OR REPLACE FUNCTION comb.clique_claim(p_subject TEXT,p_vertices INTEGER[])
RETURNS BIGINT LANGUAGE sql AS $$ SELECT comb.subset_certificate_claim(p_subject,'clique',p_vertices) $$;
CREATE OR REPLACE FUNCTION comb.odd_cycle_claim(p_subject TEXT,p_vertices INTEGER[])
RETURNS BIGINT LANGUAGE sql AS $$ SELECT comb.subset_certificate_claim(p_subject,'odd_cycle',p_vertices) $$;
CREATE OR REPLACE FUNCTION comb.vertex_cover_claim(p_subject TEXT,p_vertices INTEGER[])
RETURNS BIGINT LANGUAGE sql AS $$ SELECT comb.subset_certificate_claim(p_subject,'vertex_cover',p_vertices) $$;
CREATE OR REPLACE FUNCTION comb.hitting_set_claim(p_subject TEXT,p_vertices INTEGER[])
RETURNS BIGINT LANGUAGE sql AS $$ SELECT comb.subset_certificate_claim(p_subject,'hitting_set',p_vertices) $$;
CREATE OR REPLACE FUNCTION comb.matching_claim(p_subject TEXT,p_blocks INTEGER[])
RETURNS BIGINT LANGUAGE sql AS $$
    SELECT comb.certificate_claim(p_subject,jsonb_build_object('name','matching','k',cardinality(p_blocks)),
        jsonb_build_object('matching',to_jsonb(p_blocks)))
$$;

-- Operations create a fresh deposited object with a reproducible source digest.
-- Labels are presentation data; incidences and directed positions are copied.
CREATE OR REPLACE FUNCTION comb.relabel(p_subject TEXT,p_result TEXT,p_mapping INTEGER[])
RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE s comb.structure%ROWTYPE; v_id BIGINT; b RECORD;
BEGIN
    SELECT * INTO s FROM comb.structure WHERE id=comb.structure_id(p_subject);
    IF EXISTS(SELECT 1 FROM comb.structure WHERE subject_id=p_result) THEN
        RAISE EXCEPTION 'comb.relabel: result % already exists',p_result;
    END IF;
    IF cardinality(p_mapping)<>s.ground_n OR
       (SELECT count(DISTINCT x) FROM unnest(p_mapping) x)<>s.ground_n OR
       EXISTS(SELECT 1 FROM unnest(p_mapping) x WHERE x IS NULL OR x<0 OR x>=s.ground_n) THEN
        RAISE EXCEPTION 'comb.relabel: mapping must be a permutation of the ground set';
    END IF;
    IF NOT (SELECT ok FROM comb.wellformed(p_subject)) THEN RAISE EXCEPTION 'comb.relabel: source is malformed'; END IF;
    SELECT id INTO v_id FROM comb.register_structure(p_result,s.kind,s.ground_n,'comb.relabel',
        jsonb_build_object('operation','relabel','source',p_subject,'source_digest',comb.structure_digest(p_subject),
            'mapping',to_jsonb(p_mapping)));
    INSERT INTO comb.block(structure_id,idx,label)
        SELECT v_id,idx,label FROM comb.block WHERE structure_id=s.id;
    INSERT INTO comb.incidence(structure_id,block_idx,element_idx,position)
        SELECT v_id,block_idx,p_mapping[element_idx+1],position FROM comb.incidence WHERE structure_id=s.id;
    UPDATE comb.element e SET label=src.label FROM comb.element src
     WHERE src.structure_id=s.id AND e.structure_id=v_id AND e.idx=p_mapping[src.idx+1];
    RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION comb.disjoint_union(p_left TEXT,p_right TEXT,p_result TEXT)
RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE a comb.structure%ROWTYPE; b comb.structure%ROWTYPE; v_id BIGINT; t RECORD; v_idx INTEGER:=0;
BEGIN
    SELECT * INTO a FROM comb.structure WHERE id=comb.structure_id(p_left);
    SELECT * INTO b FROM comb.structure WHERE id=comb.structure_id(p_right);
    IF a.kind<>b.kind THEN RAISE EXCEPTION 'comb.disjoint_union: kinds differ'; END IF;
    IF EXISTS(SELECT 1 FROM comb.structure WHERE subject_id=p_result) THEN RAISE EXCEPTION 'comb.disjoint_union: result already exists'; END IF;
    IF NOT (SELECT ok FROM comb.wellformed(p_left)) OR NOT (SELECT ok FROM comb.wellformed(p_right)) THEN
        RAISE EXCEPTION 'comb.disjoint_union: source is malformed'; END IF;
    SELECT id INTO v_id FROM comb.register_structure(p_result,a.kind,a.ground_n+b.ground_n,'comb.disjoint_union',
        jsonb_build_object('operation','disjoint_union','sources',jsonb_build_array(p_left,p_right),
            'source_digests',jsonb_build_array(comb.structure_digest(p_left),comb.structure_digest(p_right))));
    FOR t IN SELECT s.id AS sid,s.offset_n,bl.idx,bl.label FROM
        (VALUES(a.id,0,0),(b.id,a.ground_n,1)) s(id,offset_n,side) JOIN comb.block bl ON bl.structure_id=s.id
        ORDER BY s.side,bl.idx LOOP
        INSERT INTO comb.block(structure_id,idx,label) VALUES(v_id,v_idx,t.label);
        INSERT INTO comb.incidence(structure_id,block_idx,element_idx,position)
            SELECT v_id,v_idx,i.element_idx+t.offset_n,i.position FROM comb.incidence i
             WHERE i.structure_id=t.sid AND i.block_idx=t.idx;
        v_idx:=v_idx+1;
    END LOOP;
    UPDATE comb.element e SET label=src.label FROM comb.element src
     WHERE e.structure_id=v_id AND ((src.structure_id=a.id AND e.idx=src.idx)
        OR (src.structure_id=b.id AND e.idx=src.idx+a.ground_n));
    RETURN v_id;
END $$;

-- A pointer-resolution fact is useful provenance, but is not attainment.
-- Preserve the old probe's behavior under an accurately named public API.
CREATE OR REPLACE FUNCTION comb.bound_resolution(p_bound_id BIGINT)
RETURNS TABLE(ok BOOLEAN,evidence JSONB) LANGUAGE plpgsql STABLE AS $$
DECLARE b cert.bound%ROWTYPE; v_s TEXT; v_c TEXT;
BEGIN
    SELECT * INTO b FROM cert.bound WHERE id=p_bound_id;
    IF b.id IS NULL THEN ok:=NULL; evidence:=jsonb_build_object('reason','no such bound'); RETURN NEXT; RETURN; END IF;
    v_s:=b.attained_by->>'comb_structure'; v_c:=b.attained_by->>'comb_configuration';
    IF v_s IS NULL AND v_c IS NULL THEN
        ok:=NULL; evidence:=jsonb_build_object('reason','the bound names no comb object as its attaining witness',
            'status','nothing for this layer to check'); RETURN NEXT; RETURN;
    END IF;
    IF (v_s IS NOT NULL AND NOT EXISTS(SELECT 1 FROM comb.structure WHERE subject_id=v_s)) OR
       (v_c IS NOT NULL AND NOT EXISTS(SELECT 1 FROM comb.configuration WHERE subject_id=v_c)) THEN
        ok:=false; evidence:=jsonb_build_object('reason','a named object is not here','names',COALESCE(v_s,v_c));
        RETURN NEXT; RETURN;
    END IF;
    ok:=true; evidence:=jsonb_build_object('bound_id',b.id,'attaining_object',COALESCE(v_s,v_c),
        'witness',CASE WHEN v_s IS NOT NULL THEN comb.serialize_structure(v_s) ELSE comb.serialize_configuration(v_c) END,
        'note','pointer resolution only; no mathematical attainment is asserted');
    RETURN NEXT;
END $$;

CREATE OR REPLACE FUNCTION comb.resolution_claim(p_bound_id BIGINT)
RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE v_id BIGINT;
BEGIN
    INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql)
    VALUES('comb_resolution',jsonb_build_object('bound_id',p_bound_id),
        format('comb object pointers on bound #%s resolve to deposited objects',p_bound_id),
        'computational','comp_sql',format('SELECT ok,evidence FROM comb.bound_resolution(%s)',p_bound_id))
    ON CONFLICT(statement) DO UPDATE SET probe_sql=EXCLUDED.probe_sql RETURNING id INTO v_id;
    RETURN v_id;
END $$;

CREATE TABLE IF NOT EXISTS comb.objective_contract(
    quantity_id BIGINT PRIMARY KEY REFERENCES cert.quantity(id) ON DELETE CASCADE,
    metric TEXT NOT NULL CHECK(metric IN ('edge_count','block_count','ground_n')),
    sense TEXT NOT NULL CHECK(sense IN ('max','min')),
    kind TEXT NOT NULL CHECK(kind IN ('graph','digraph','hypergraph','set_system')),
    ground_n INTEGER CHECK(ground_n>=0),
    predicate JSONB NOT NULL,
    CHECK(predicate->>'name' IN ('wellformed','uniform','primitive_family','intersecting_family','triangle_free'))
);

CREATE OR REPLACE FUNCTION comb.register_objective(p_quantity TEXT,p_metric TEXT,p_sense TEXT,
    p_predicate JSONB,p_kind TEXT DEFAULT 'graph',p_ground_n INTEGER DEFAULT NULL)
RETURNS comb.objective_contract LANGUAGE plpgsql AS $$
DECLARE v_qid BIGINT; result comb.objective_contract%ROWTYPE;
BEGIN
    SELECT id INTO v_qid FROM cert.quantity WHERE subject_id=p_quantity;
    IF v_qid IS NULL THEN RAISE EXCEPTION 'comb.register_objective: no quantity %',p_quantity; END IF;
    IF p_metric='edge_count' AND p_kind NOT IN ('graph','digraph') THEN
        RAISE EXCEPTION 'comb.register_objective: edge_count requires graph or digraph'; END IF;
    IF p_predicate->>'name' NOT IN ('wellformed','uniform','primitive_family','intersecting_family','triangle_free')
       OR p_predicate->>'name' IS NULL THEN RAISE EXCEPTION 'comb.register_objective: unsupported feasibility predicate'; END IF;
    INSERT INTO comb.objective_contract(quantity_id,metric,sense,kind,ground_n,predicate)
    VALUES(v_qid,p_metric,p_sense,p_kind,p_ground_n,p_predicate)
    ON CONFLICT(quantity_id) DO UPDATE SET metric=EXCLUDED.metric,sense=EXCLUDED.sense,
        kind=EXCLUDED.kind,ground_n=EXCLUDED.ground_n,predicate=EXCLUDED.predicate RETURNING * INTO result;
    RETURN result;
END $$;

CREATE TABLE IF NOT EXISTS comb.attainment_contract(
    bound_id BIGINT PRIMARY KEY REFERENCES cert.bound(id) ON DELETE CASCADE,
    structure_id BIGINT NOT NULL REFERENCES comb.structure(id) ON DELETE CASCADE,
    instance_digest TEXT NOT NULL,
    objective JSONB NOT NULL,
    feasibility_claim_id BIGINT NOT NULL REFERENCES cert.claim(id)
);

CREATE OR REPLACE FUNCTION comb.bound_statement_ref(p_bound_id BIGINT)
RETURNS JSONB LANGUAGE sql STABLE AS $$
    SELECT jsonb_build_object('quantity_id',b.quantity_id,'objective_digest',comb.instance_digest(to_jsonb(c)),
        'direction',b.direction,'value',trim_scale(b.value),'is_strict',b.is_strict,
        'hypotheses',to_jsonb(b.hypotheses))
    FROM cert.bound b JOIN comb.objective_contract c ON c.quantity_id=b.quantity_id WHERE b.id=p_bound_id
$$;

-- Theorem producers get a dedicated typed proposition at creation. Attaching
-- it to a row never changes an unrelated claim's meaning or its checking probe.
CREATE OR REPLACE FUNCTION comb.register_bound_claim(p_bound_id BIGINT)
RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE ref JSONB; v_id BIGINT; v_statement TEXT;
BEGIN
    ref:=comb.bound_statement_ref(p_bound_id);
    IF ref IS NULL THEN RAISE EXCEPTION 'comb.register_bound_claim: bound needs a registered objective'; END IF;
    v_statement:=format('external theorem establishes typed bound %s [contract %s]',
        cert.claim_canonical_json(ref),comb.instance_digest(ref));
    INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql,domain)
    VALUES('comb_bound',jsonb_build_object('bound_contract',ref),v_statement,'formal','formal_external',NULL,'exact_int')
    ON CONFLICT(statement) DO NOTHING RETURNING id INTO v_id;
    IF v_id IS NULL THEN SELECT id INTO v_id FROM cert.claim WHERE statement=v_statement; END IF;
    PERFORM comb.bind_bound_claim(p_bound_id,v_id);
    RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION comb.bind_bound_claim(p_bound_id BIGINT,p_claim_id BIGINT)
RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE ref JSONB;
BEGIN
    ref:=comb.bound_statement_ref(p_bound_id);
    IF ref IS NULL THEN RAISE EXCEPTION 'comb.bind_bound_claim: bound needs a registered objective'; END IF;
    IF NOT EXISTS(SELECT 1 FROM cert.claim WHERE id=p_claim_id AND subject_kind='comb_bound'
        AND claim_kind='formal' AND method='formal_external' AND probe_sql IS NULL
        AND subject_ref->'bound_contract'=ref) THEN
        RAISE EXCEPTION 'comb.bind_bound_claim: require a matching dedicated typed formal bound claim';
    END IF;
    UPDATE cert.bound SET claim_id=p_claim_id WHERE id=p_bound_id;
    RETURN p_claim_id;
END $$;

CREATE OR REPLACE FUNCTION comb.attainment_digest(p_bound_id BIGINT)
RETURNS TEXT LANGUAGE sql STABLE AS $$
    SELECT comb.instance_digest(jsonb_build_object('bound',comb.bound_statement_ref(b.id),
        'attainment',to_jsonb(a))) FROM cert.bound b LEFT JOIN comb.attainment_contract a ON a.bound_id=b.id
    WHERE b.id=p_bound_id
$$;

CREATE OR REPLACE FUNCTION comb.bind_attainment(p_bound_id BIGINT,p_subject TEXT)
RETURNS comb.attainment_contract LANGUAGE plpgsql AS $$
DECLARE b cert.bound%ROWTYPE; c comb.objective_contract%ROWTYPE;
        result comb.attainment_contract%ROWTYPE; v_feasible BIGINT;
BEGIN
    SELECT * INTO b FROM cert.bound WHERE id=p_bound_id;
    IF b.id IS NULL THEN RAISE EXCEPTION 'comb.bind_attainment: no bound %',p_bound_id; END IF;
    SELECT * INTO c FROM comb.objective_contract WHERE quantity_id=b.quantity_id;
    IF c.quantity_id IS NULL THEN RAISE EXCEPTION 'comb.bind_attainment: register a typed objective first'; END IF;
    v_feasible:=comb.certificate_claim(p_subject,c.predicate);
    INSERT INTO comb.attainment_contract(bound_id,structure_id,instance_digest,objective,feasibility_claim_id)
    VALUES(b.id,comb.structure_id(p_subject),comb.structure_digest(p_subject),to_jsonb(c),v_feasible)
    ON CONFLICT(bound_id) DO UPDATE SET structure_id=EXCLUDED.structure_id,instance_digest=EXCLUDED.instance_digest,
        objective=EXCLUDED.objective,feasibility_claim_id=EXCLUDED.feasibility_claim_id RETURNING * INTO result;
    PERFORM comb.attain_bound(b.id,p_subject);
    RETURN result;
END $$;

CREATE OR REPLACE FUNCTION comb.bound_attainment(p_bound_id BIGINT)
RETURNS TABLE(ok BOOLEAN,evidence JSONB) LANGUAGE plpgsql STABLE AS $$
DECLARE b cert.bound%ROWTYPE; c comb.objective_contract%ROWTYPE; a comb.attainment_contract%ROWTYPE;
        s comb.structure%ROWTYPE; r RECORD; v_value NUMERIC;
BEGIN
    SELECT * INTO b FROM cert.bound WHERE id=p_bound_id;
    SELECT * INTO a FROM comb.attainment_contract WHERE bound_id=p_bound_id;
    IF b.id IS NULL OR a.bound_id IS NULL THEN
        ok:=NULL; evidence:=jsonb_build_object('reason','no typed attainment contract','bound_id',p_bound_id);
        RETURN NEXT; RETURN;
    END IF;
    SELECT * INTO c FROM comb.objective_contract WHERE quantity_id=b.quantity_id;
    SELECT * INTO s FROM comb.structure WHERE id=a.structure_id;
    IF c.quantity_id IS NULL OR to_jsonb(c)<>a.objective OR s.id IS NULL
       OR comb.structure_digest(s.subject_id)<>a.instance_digest THEN
        ok:=false; evidence:=jsonb_build_object('reason','attainment instance or objective changed'); RETURN NEXT; RETURN;
    END IF;
    IF b.is_strict OR b.direction<>(CASE WHEN c.sense='max' THEN 'lower' ELSE 'upper' END)
       OR s.kind<>c.kind OR (c.ground_n IS NOT NULL AND s.ground_n<>c.ground_n) THEN
        ok:=false; evidence:=jsonb_build_object('reason','object or bound does not satisfy objective contract'); RETURN NEXT; RETURN;
    END IF;
    SELECT * INTO r FROM comb.check_certificate(s.subject_id,c.predicate,'{}',a.instance_digest);
    IF r.ok IS DISTINCT FROM true THEN ok:=r.ok; evidence:=r.evidence; RETURN NEXT; RETURN; END IF;
    v_value:=CASE c.metric WHEN 'ground_n' THEN s.ground_n
        ELSE (SELECT count(*) FROM comb.block WHERE structure_id=s.id) END;
    ok:=v_value=b.value;
    evidence:=jsonb_build_object('bound_id',b.id,'objective',a.objective,'measured_value',v_value,
        'bound_value',b.value,'instance_digest',a.instance_digest,'feasibility_claim_id',a.feasibility_claim_id);
    RETURN NEXT;
END $$;

CREATE OR REPLACE FUNCTION comb.attainment_claim(p_bound_id BIGINT)
RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE v_id BIGINT; a comb.attainment_contract%ROWTYPE; b cert.bound%ROWTYPE; v_snapshot TEXT;
BEGIN
    SELECT * INTO b FROM cert.bound WHERE id=p_bound_id;
    IF b.id IS NULL THEN RAISE EXCEPTION 'comb.attainment_claim: no bound %',p_bound_id; END IF;
    SELECT * INTO a FROM comb.attainment_contract WHERE bound_id=p_bound_id;
    v_snapshot:=comb.attainment_digest(b.id);
    INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql)
    VALUES('comb_attainment',jsonb_build_object('bound_id',b.id,'contract',to_jsonb(a),'bound_value',b.value),
        format('typed construction attains bound #%s at %s [contract %s]',b.id,b.value,v_snapshot),
        'computational','comp_sql',format('SELECT ok,evidence FROM comb.check_attainment(%s,%L)',b.id,v_snapshot))
    ON CONFLICT(statement) DO UPDATE SET probe_sql=EXCLUDED.probe_sql RETURNING id INTO v_id;
    RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION comb.check_attainment(p_bound_id BIGINT,p_expected_digest TEXT)
RETURNS TABLE(ok BOOLEAN,evidence JSONB) LANGUAGE plpgsql STABLE AS $$
BEGIN
    IF p_expected_digest IS DISTINCT FROM comb.attainment_digest(p_bound_id) THEN
        ok:=false; evidence:=jsonb_build_object('reason','bound or objective contract changed since claim was minted');
        RETURN NEXT; RETURN;
    END IF;
    RETURN QUERY SELECT * FROM comb.bound_attainment(p_bound_id);
END $$;

CREATE OR REPLACE FUNCTION comb.exact_optimal(p_bound_id BIGINT)
RETURNS TABLE(ok BOOLEAN,evidence JSONB) LANGUAGE plpgsql STABLE AS $$
DECLARE b cert.bound%ROWTYPE; r RECORD; o RECORD;
BEGIN
    SELECT * INTO r FROM comb.bound_attainment(p_bound_id);
    IF r.ok IS DISTINCT FROM true THEN ok:=r.ok; evidence:=r.evidence; RETURN NEXT; RETURN; END IF;
    SELECT * INTO b FROM cert.bound WHERE id=p_bound_id;
    SELECT other.id,other.claim_id INTO o FROM cert.bound other
    JOIN cert.standing st ON st.claim_id=other.claim_id AND st.effective_status='valid'
    JOIN cert.claim truth ON truth.id=other.claim_id
    JOIN LATERAL cert.statement_bound(truth.id) sb ON sb.ok AND sb.evidence->>'digest_kind'='closure'
    WHERE other.quantity_id=b.quantity_id AND other.direction<>b.direction
      AND NOT other.is_strict AND other.value=b.value AND other.hypotheses <@ b.hypotheses
      AND truth.subject_ref->'bound_contract'=comb.bound_statement_ref(other.id)
      AND truth.subject_kind='comb_bound' AND truth.claim_kind='formal'
      AND truth.method='formal_external' AND truth.probe_sql IS NULL
    ORDER BY other.id LIMIT 1;
    IF o.id IS NULL THEN
        ok:=NULL; evidence:=r.evidence||jsonb_build_object('reason','no compatible verified opposite bound at the attained value');
        RETURN NEXT; RETURN;
    END IF;
    ok:=true; evidence:=r.evidence||jsonb_build_object('opposite_bound_id',o.id,'opposite_claim_id',o.claim_id,
        'hypotheses',to_jsonb(b.hypotheses),'external_theorem_dependency',true,
        'status','exact optimum conditional on the checked external theorem');
    RETURN NEXT;
END $$;

CREATE OR REPLACE FUNCTION comb.check_exact_optimal(p_bound_id BIGINT,p_expected_digest TEXT,p_opposite_claim BIGINT)
RETURNS TABLE(ok BOOLEAN,evidence JSONB) LANGUAGE plpgsql STABLE AS $$
DECLARE r RECORD;
BEGIN
    IF p_expected_digest IS DISTINCT FROM comb.attainment_digest(p_bound_id) THEN
        ok:=false; evidence:=jsonb_build_object('reason','bound or objective contract changed since claim was minted');
        RETURN NEXT; RETURN;
    END IF;
    SELECT * INTO r FROM comb.exact_optimal(p_bound_id);
    IF r.ok AND (p_opposite_claim IS NULL OR
        p_opposite_claim IS DISTINCT FROM (r.evidence->>'opposite_claim_id')::BIGINT) THEN
        ok:=NULL; evidence:=jsonb_build_object('reason','mint a new exact claim to bind the verified opposite proof');
        RETURN NEXT; RETURN;
    END IF;
    ok:=r.ok; evidence:=r.evidence; RETURN NEXT;
END $$;

CREATE OR REPLACE FUNCTION comb.attest_attainment(p_bound_id BIGINT)
RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE v_id BIGINT; v_attain BIGINT; b cert.bound%ROWTYPE; a comb.attainment_contract%ROWTYPE;
        v_premises BIGINT[]; r RECORD; v_snapshot TEXT; v_opposite BIGINT;
BEGIN
    SELECT * INTO b FROM cert.bound WHERE id=p_bound_id;
    IF b.id IS NULL THEN RAISE EXCEPTION 'comb.attest_attainment: no bound %',p_bound_id; END IF;
    SELECT * INTO a FROM comb.attainment_contract WHERE bound_id=p_bound_id;
    v_attain:=comb.attainment_claim(p_bound_id);
    v_snapshot:=comb.attainment_digest(p_bound_id);
    SELECT * INTO r FROM comb.exact_optimal(p_bound_id);
    v_premises:=ARRAY[v_attain];
    IF r.ok THEN
        v_opposite:=(r.evidence->>'opposite_claim_id')::BIGINT;
        v_premises:=array_append(v_premises,v_opposite);
    END IF;
    INSERT INTO cert.claim(subject_kind,subject_ref,statement,claim_kind,method,probe_sql)
    VALUES('comb_exact_optimum',jsonb_build_object('bound_id',b.id,'attainment_claim_id',v_attain,
        'contract',to_jsonb(a),'bound_value',b.value,'contract_digest',v_snapshot,'opposite_claim_id',v_opposite),
        format('typed objective on bound #%s has exact optimum %s [contract %s; opposite proof %s]',
            b.id,b.value,v_snapshot,COALESCE(v_opposite::TEXT,'absent')),
        'computational','comp_sql',format('SELECT ok,evidence FROM comb.check_exact_optimal(%s,%L,%s)',
            b.id,v_snapshot,COALESCE(v_opposite::TEXT,'NULL')))
    ON CONFLICT(statement) DO UPDATE SET probe_sql=EXCLUDED.probe_sql RETURNING id INTO v_id;
    IF r.ok AND NOT EXISTS(SELECT 1 FROM cert.derivation WHERE conclusion_id=v_id AND rule='comb_attainment'
        AND premise_ids=v_premises) THEN
        INSERT INTO cert.derivation(conclusion_id,premise_ids,rule) VALUES(v_id,v_premises,'comb_attainment');
    END IF;
    RETURN v_id;
END $$;
