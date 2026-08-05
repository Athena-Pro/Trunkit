-- Unified model, step 111: property probes over comb structures.
--
-- Second step of gap T2 (docs/DESIGN_COMB_FINITE_STRUCTURES.md, Part 3). 110
-- stores the objects; this decides things about them. It is the step that makes
-- the Kleitman set-system cluster (#447, #487, #497, #498, #505, #1023)
-- hostable, and it needs none of the geometry.
--
-- THE ORGANISING SPLIT IS THE QUANTIFIER, NOT THE SUBJECT. Combinatorial claims
-- divide by shape, and the division decides which ones a probe can settle:
--
--   the object HAS property P, P checkable per block or per pair
--     -- k-uniform, triangle-free, primitive family, intersecting family.
--     A total scan. Reaches valid or refuted, and refutation always names the
--     offending pair.
--
--   THERE EXISTS a substructure with P, and here it is
--     -- this colouring is proper, this subset is independent, this bijection
--     is an isomorphism. The witness is supplied by a producer and checked in
--     polynomial time. Reaches valid or refuted.
--
--   THERE EXISTS NO substructure with P
--     -- chi >= k, no independent set of size m. Not in this step and not
--     cheaply in any step: absence is not witnessed by anything small.
--
-- WHY WITNESSES ARE STORED AND NOT PASSED. A cert probe_sql must be
-- self-contained, because the whole point is that it re-runs later without the
-- caller. A witness handed to a probe as an argument would live only in the
-- string that invoked it once. So comb.witness holds it, and 113 wires the
-- same rows into cert.witness_carry.
--
-- REFUTING A WITNESS IS NOT REFUTING THE STATEMENT. This is the rule the whole
-- step is arranged around. A supplied 5-colouring with a monochromatic edge
-- refutes THAT COLOURING; it says nothing whatever about chi(G). So they are
-- two different functions with two different verdict sets:
--
--   comb.colouring_is_proper  -> valid / refuted. About the witness.
--   comb.chromatic_at_most    -> valid / UNVERIFIED, never refuted. About the
--                                graph, and only ever reporting what is on
--                                record: "no proper k-colouring has been
--                                deposited" is a fact about the ledger, in
--                                exactly the sense cert.bound_optimal (105)
--                                already means by "best known on record".
--
-- Anything else would let a producer's bad guess be recorded as a mathematical
-- falsehood, which is the failure this layer exists to make impossible.
--
-- GENERALISATION IS BY BLOCK, NOT BY SPECIAL CASE. A colouring is proper when
-- no BLOCK is monochromatic; a subset is independent when no BLOCK lies inside
-- it. On a graph, where blocks are edges, both collapse to the familiar
-- definitions -- so hypergraphs and set systems come free rather than needing a
-- parallel implementation.
--
-- Idempotent; additive only. Read-only over 110 apart from comb.witness.

INSERT INTO cert.method (name, claim_kind, checker_kind, description)
VALUES ('comp_sql', 'computational', 'sql', 'in-DB probe returning (ok, evidence)')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 1. Supplied witnesses
-- ---------------------------------------------------------------------------

-- A producer's proposed substructure. Untrusted by construction: nothing here
-- is evidence until a probe below has re-derived it. `kind` picks which probe
-- is meaningful, and target_structure_id is used only by bijections.
CREATE TABLE IF NOT EXISTS comb.witness (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    structure_id        BIGINT NOT NULL REFERENCES comb.structure(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN ('colouring', 'subset', 'bijection')),
    target_structure_id BIGINT REFERENCES comb.structure(id) ON DELETE CASCADE,
    source              TEXT NOT NULL DEFAULT '',
    provenance          JSONB NOT NULL DEFAULT '{}'::jsonb,
    registered_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (structure_id, name),
    CHECK ((kind = 'bijection') = (target_structure_id IS NOT NULL))
);

-- One assignment per element. For a colouring, `value` is the colour class;
-- for a bijection, the image element's idx in the target; for a subset, the
-- presence of the row IS the membership and value is 1.
CREATE TABLE IF NOT EXISTS comb.witness_value (
    witness_id  BIGINT  NOT NULL REFERENCES comb.witness(id) ON DELETE CASCADE,
    element_idx INTEGER NOT NULL,
    value       INTEGER NOT NULL,
    PRIMARY KEY (witness_id, element_idx)
);

CREATE OR REPLACE FUNCTION comb.witness_id(p_subject TEXT, p_name TEXT)
RETURNS BIGINT
LANGUAGE plpgsql STABLE AS $$
DECLARE v_id BIGINT;
BEGIN
    SELECT w.id INTO v_id
      FROM comb.witness w
     WHERE w.structure_id = comb.structure_id(p_subject) AND w.name = p_name;
    IF v_id IS NULL THEN
        RAISE EXCEPTION 'comb: structure % has no witness %', p_subject, p_name;
    END IF;
    RETURN v_id;
END
$$;

-- Register (or replace the assignment of) a witness. Replacing rather than
-- appending is deliberate: a witness is producer data, and a claim minted over
-- it is supposed to flip when the data changes -- the same reason
-- comb.wellformed re-reads the object instead of trusting an earlier pass.
--
-- p_values may be NULL for a subset, where membership is the whole content.
CREATE OR REPLACE FUNCTION comb.register_witness(
    p_subject  TEXT,
    p_name     TEXT,
    p_kind     TEXT,
    p_elements INTEGER[],
    p_values   INTEGER[] DEFAULT NULL,
    p_target   TEXT      DEFAULT NULL,
    p_source   TEXT      DEFAULT ''
) RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE
    v_sid     BIGINT;
    v_tid     BIGINT;
    v_id      BIGINT;
    v_missing INTEGER;
BEGIN
    v_sid := comb.structure_id(p_subject);
    IF p_target IS NOT NULL THEN
        v_tid := comb.structure_id(p_target);
    END IF;

    IF p_elements IS NULL OR cardinality(p_elements) = 0 THEN
        RAISE EXCEPTION 'comb.register_witness: % assigns nothing', p_name;
    END IF;
    IF cardinality(p_elements) <> cardinality(ARRAY(SELECT DISTINCT unnest(p_elements))) THEN
        RAISE EXCEPTION 'comb.register_witness: % assigns an element twice', p_name;
    END IF;
    IF p_kind <> 'subset' AND p_values IS NULL THEN
        RAISE EXCEPTION 'comb.register_witness: a % needs values', p_kind;
    END IF;
    IF p_values IS NOT NULL AND cardinality(p_values) <> cardinality(p_elements) THEN
        RAISE EXCEPTION 'comb.register_witness: values and elements differ in length';
    END IF;

    SELECT e INTO v_missing
      FROM unnest(p_elements) e
     WHERE NOT EXISTS (SELECT 1 FROM comb.element
                        WHERE structure_id = v_sid AND idx = e)
     LIMIT 1;
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'comb.register_witness: element % is not in the ground set of %',
                        v_missing, p_subject;
    END IF;

    INSERT INTO comb.witness (structure_id, name, kind, target_structure_id, source)
    VALUES (v_sid, p_name, p_kind, v_tid, p_source)
    ON CONFLICT (structure_id, name) DO UPDATE
        SET kind = EXCLUDED.kind,
            target_structure_id = EXCLUDED.target_structure_id,
            source = EXCLUDED.source
    RETURNING id INTO v_id;

    DELETE FROM comb.witness_value WHERE witness_id = v_id;
    INSERT INTO comb.witness_value (witness_id, element_idx, value)
    SELECT v_id, p_elements[i],
           CASE WHEN p_values IS NULL THEN 1 ELSE p_values[i] END
      FROM generate_subscripts(p_elements, 1) i;

    RETURN v_id;
END
$$;

-- ---------------------------------------------------------------------------
-- 2. Shape helpers
-- ---------------------------------------------------------------------------

-- Blocks as sorted element arrays. Every elementwise probe below is one query
-- over this, and array containment (<@) and overlap (&&) do the set algebra
-- exactly, on integers, with no floating point anywhere near it.
CREATE OR REPLACE FUNCTION comb.block_sets(p_subject TEXT)
RETURNS TABLE (block_idx INTEGER, elements INTEGER[], size INTEGER)
LANGUAGE sql STABLE AS $$
    SELECT i.block_idx,
           array_agg(i.element_idx ORDER BY i.element_idx),
           COUNT(*)::INTEGER
      FROM comb.incidence i
     WHERE i.structure_id = comb.structure_id(p_subject)
     GROUP BY i.block_idx;
$$;

CREATE OR REPLACE FUNCTION comb.degree_sequence(p_subject TEXT)
RETURNS TABLE (element_idx INTEGER, degree INTEGER)
LANGUAGE sql STABLE AS $$
    SELECT e.idx, COUNT(i.block_idx)::INTEGER
      FROM comb.element e
      LEFT JOIN comb.incidence i
        ON i.structure_id = e.structure_id AND i.element_idx = e.idx
     WHERE e.structure_id = comb.structure_id(p_subject)
     GROUP BY e.idx
     ORDER BY e.idx;
$$;

-- ---------------------------------------------------------------------------
-- 3. Elementwise properties -- total scans, valid or refuted
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION comb.is_uniform(p_subject TEXT, p_k INTEGER)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE v_bad JSONB; v_n INTEGER;
BEGIN
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
               'block_idx', b.block_idx, 'size', b.size)), '[]'::jsonb), COUNT(*)
      INTO v_bad, v_n
      FROM comb.block_sets(p_subject) b
     WHERE b.size <> p_k;

    IF v_n > 0 THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', format('not %s-uniform', p_k),
            'structure', p_subject, 'k', p_k, 'offending', v_bad, 'n', v_n);
        RETURN NEXT; RETURN;
    END IF;
    ok := true;
    evidence := jsonb_build_object(
        'structure', p_subject, 'k', p_k,
        'blocks', (SELECT COUNT(*) FROM comb.block_sets(p_subject)));
    RETURN NEXT;
END
$$;

-- No block strictly contains another: the set-system form of a primitive set,
-- which is the finite, in-DB half of Erdos #1196.
--
-- Equal blocks are NOT strict containment and so do not refute here -- a
-- duplicate is a well-formedness remark (110), not a failure of primitivity.
-- They are reported anyway so the reader can see why a family looks smaller
-- than its block count.
CREATE OR REPLACE FUNCTION comb.is_primitive_family(p_subject TEXT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE v_chains JSONB; v_n INTEGER; v_dups INTEGER;
BEGIN
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
               'contained_block', a.block_idx, 'contained', a.elements,
               'container_block', b.block_idx, 'container', b.elements)),
           '[]'::jsonb), COUNT(*)
      INTO v_chains, v_n
      FROM comb.block_sets(p_subject) a
      JOIN comb.block_sets(p_subject) b
        ON a.block_idx <> b.block_idx
     WHERE a.size < b.size AND a.elements <@ b.elements;

    SELECT COUNT(*) INTO v_dups FROM (
        SELECT elements FROM comb.block_sets(p_subject)
         GROUP BY elements HAVING COUNT(*) > 1) d;

    IF v_n > 0 THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', 'a block strictly contains another -- family is not primitive',
            'structure', p_subject, 'containments', v_chains, 'n', v_n);
        RETURN NEXT; RETURN;
    END IF;
    ok := true;
    evidence := jsonb_build_object(
        'structure', p_subject,
        'blocks', (SELECT COUNT(*) FROM comb.block_sets(p_subject)),
        'repeated_blocks', v_dups);
    RETURN NEXT;
END
$$;

-- Every two blocks meet. The Kleitman-cluster workhorse.
CREATE OR REPLACE FUNCTION comb.is_intersecting_family(p_subject TEXT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE v_bad JSONB; v_n INTEGER;
BEGIN
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
               'block_a', a.block_idx, 'elements_a', a.elements,
               'block_b', b.block_idx, 'elements_b', b.elements)), '[]'::jsonb),
           COUNT(*)
      INTO v_bad, v_n
      FROM comb.block_sets(p_subject) a
      JOIN comb.block_sets(p_subject) b
        ON a.block_idx < b.block_idx
     WHERE NOT (a.elements && b.elements);

    IF v_n > 0 THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', 'two blocks are disjoint -- family is not intersecting',
            'structure', p_subject, 'disjoint_pairs', v_bad, 'n', v_n);
        RETURN NEXT; RETURN;
    END IF;
    ok := true;
    evidence := jsonb_build_object(
        'structure', p_subject,
        'blocks', (SELECT COUNT(*) FROM comb.block_sets(p_subject)));
    RETURN NEXT;
END
$$;

-- Graph-only, and it says so rather than returning a confident answer about an
-- object the question does not apply to. A triangle is named when found.
CREATE OR REPLACE FUNCTION comb.is_triangle_free(p_subject TEXT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE v_sid BIGINT; v_kind TEXT; v_tri JSONB; v_n INTEGER;
BEGIN
    SELECT s.id, s.kind INTO v_sid, v_kind
      FROM comb.structure s WHERE s.subject_id = p_subject;
    IF v_sid IS NULL THEN
        ok := false;
        evidence := jsonb_build_object('reason', 'no such structure',
                                       'structure', p_subject);
        RETURN NEXT; RETURN;
    END IF;
    IF v_kind <> 'graph' THEN
        ok := NULL;   -- the question does not apply; do not answer it
        evidence := jsonb_build_object(
            'reason', 'triangle-freeness is defined here for kind=graph',
            'structure', p_subject, 'kind', v_kind);
        RETURN NEXT; RETURN;
    END IF;

    -- comb.edge normalises to u < v, so a triangle a<b<c is exactly the
    -- edges (a,b), (b,c), (a,c).
    SELECT COALESCE(jsonb_agg(jsonb_build_array(t.a, t.b, t.c)), '[]'::jsonb),
           COUNT(*)
      INTO v_tri, v_n
      FROM (SELECT e1.u AS a, e1.v AS b, e2.v AS c
              FROM comb.edge e1
              JOIN comb.edge e2 ON e2.structure_id = e1.structure_id AND e2.u = e1.v
              JOIN comb.edge e3 ON e3.structure_id = e1.structure_id
                               AND e3.u = e1.u AND e3.v = e2.v
             WHERE e1.structure_id = v_sid
             LIMIT 16) t;

    IF v_n > 0 THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', 'triangle found', 'structure', p_subject,
            'triangles', v_tri, 'note', 'at most 16 reported');
        RETURN NEXT; RETURN;
    END IF;
    ok := true;
    evidence := jsonb_build_object(
        'structure', p_subject,
        'edges', (SELECT COUNT(*) FROM comb.edge WHERE structure_id = v_sid));
    RETURN NEXT;
END
$$;

-- ---------------------------------------------------------------------------
-- 4. Witness checks -- about the witness, never about the object
-- ---------------------------------------------------------------------------

-- Proper iff no block is monochromatic. On a graph, blocks are edges and this
-- is the usual definition; hypergraphs come free.
--
-- A partial colouring is refuted, not silently treated as a colouring of the
-- part it covers: leaving a vertex out is the easiest way to make a bad
-- colouring look proper.
CREATE OR REPLACE FUNCTION comb.colouring_is_proper(p_subject TEXT, p_name TEXT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_wid     BIGINT;
    v_kind    TEXT;
    v_uncol   JSONB;
    v_mono    JSONB;
    v_n       INTEGER;
    v_colours INTEGER;
BEGIN
    v_wid := comb.witness_id(p_subject, p_name);
    SELECT w.kind INTO v_kind FROM comb.witness w WHERE w.id = v_wid;
    IF v_kind <> 'colouring' THEN
        ok := false;
        evidence := jsonb_build_object('reason', 'witness is not a colouring',
                                       'witness', p_name, 'kind', v_kind);
        RETURN NEXT; RETURN;
    END IF;

    SELECT COALESCE(jsonb_agg(e.idx ORDER BY e.idx), '[]'::jsonb)
      INTO v_uncol
      FROM comb.element e
     WHERE e.structure_id = comb.structure_id(p_subject)
       AND NOT EXISTS (SELECT 1 FROM comb.witness_value v
                        WHERE v.witness_id = v_wid AND v.element_idx = e.idx);
    IF jsonb_array_length(v_uncol) > 0 THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', 'colouring is partial -- some elements are unassigned',
            'witness', p_name, 'uncoloured', v_uncol);
        RETURN NEXT; RETURN;
    END IF;

    SELECT COALESCE(jsonb_agg(jsonb_build_object(
               'block_idx', m.block_idx, 'elements', m.elements,
               'colour', m.colour)), '[]'::jsonb), COUNT(*)
      INTO v_mono, v_n
      FROM (SELECT b.block_idx, b.elements, MIN(v.value) AS colour
              FROM comb.block_sets(p_subject) b
              JOIN comb.incidence i
                ON i.structure_id = comb.structure_id(p_subject)
               AND i.block_idx = b.block_idx
              JOIN comb.witness_value v
                ON v.witness_id = v_wid AND v.element_idx = i.element_idx
             GROUP BY b.block_idx, b.elements
            HAVING COUNT(DISTINCT v.value) = 1) m;

    SELECT COUNT(DISTINCT value) INTO v_colours
      FROM comb.witness_value WHERE witness_id = v_wid;

    IF v_n > 0 THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', 'a block is monochromatic',
            'witness', p_name, 'structure', p_subject,
            'monochromatic', v_mono, 'n', v_n, 'colours', v_colours);
        RETURN NEXT; RETURN;
    END IF;
    ok := true;
    evidence := jsonb_build_object(
        'witness', p_name, 'structure', p_subject, 'colours', v_colours);
    RETURN NEXT;
END
$$;

-- Independent iff no block lies wholly inside the subset. On a graph that is
-- "no edge has both ends in S"; on a hypergraph it is the standard notion.
CREATE OR REPLACE FUNCTION comb.subset_is_independent(p_subject TEXT, p_name TEXT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE v_wid BIGINT; v_kind TEXT; v_set INTEGER[]; v_bad JSONB; v_n INTEGER;
BEGIN
    v_wid := comb.witness_id(p_subject, p_name);
    SELECT w.kind INTO v_kind FROM comb.witness w WHERE w.id = v_wid;
    IF v_kind <> 'subset' THEN
        ok := false;
        evidence := jsonb_build_object('reason', 'witness is not a subset',
                                       'witness', p_name, 'kind', v_kind);
        RETURN NEXT; RETURN;
    END IF;

    SELECT array_agg(element_idx ORDER BY element_idx) INTO v_set
      FROM comb.witness_value WHERE witness_id = v_wid;

    SELECT COALESCE(jsonb_agg(jsonb_build_object(
               'block_idx', b.block_idx, 'elements', b.elements)), '[]'::jsonb),
           COUNT(*)
      INTO v_bad, v_n
      FROM comb.block_sets(p_subject) b
     WHERE b.elements <@ v_set;

    IF v_n > 0 THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', 'a block lies inside the subset -- not independent',
            'witness', p_name, 'structure', p_subject,
            'contained_blocks', v_bad, 'n', v_n, 'size', cardinality(v_set));
        RETURN NEXT; RETURN;
    END IF;
    ok := true;
    evidence := jsonb_build_object(
        'witness', p_name, 'structure', p_subject,
        'size', cardinality(v_set), 'subset', to_jsonb(v_set));
    RETURN NEXT;
END
$$;

-- Does the supplied bijection carry one structure onto another?
--
-- This checks a MAP. It does not search for one, and a refutation here says
-- the producer's map is wrong, never that the two structures are
-- non-isomorphic -- graph isomorphism has no known polynomial algorithm and
-- nothing in SQL is going to change that.
--
-- Block comparison goes through comb.block_signature, which carries positions,
-- so a digraph is compared as a digraph rather than as its underlying graph.
CREATE OR REPLACE FUNCTION comb.bijection_is_isomorphism(p_subject TEXT, p_name TEXT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_wid   BIGINT;
    v_kind  TEXT;
    v_sid   BIGINT;
    v_tid   BIGINT;
    s_n     INTEGER;
    t_n     INTEGER;
    v_dom   INTEGER;
    v_img   INTEGER;
    v_inj   INTEGER;
    v_bad   JSONB;
    v_n     INTEGER;
    s_blk   INTEGER;
    t_blk   INTEGER;
BEGIN
    v_wid := comb.witness_id(p_subject, p_name);
    SELECT w.kind, w.structure_id, w.target_structure_id
      INTO v_kind, v_sid, v_tid
      FROM comb.witness w WHERE w.id = v_wid;
    IF v_kind <> 'bijection' THEN
        ok := false;
        evidence := jsonb_build_object('reason', 'witness is not a bijection',
                                       'witness', p_name, 'kind', v_kind);
        RETURN NEXT; RETURN;
    END IF;

    SELECT ground_n INTO s_n FROM comb.structure WHERE id = v_sid;
    SELECT ground_n INTO t_n FROM comb.structure WHERE id = v_tid;
    IF s_n <> t_n THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', 'ground sets differ in size', 'source_n', s_n, 'target_n', t_n);
        RETURN NEXT; RETURN;
    END IF;

    SELECT COUNT(*) INTO v_dom FROM comb.witness_value WHERE witness_id = v_wid;
    SELECT COUNT(DISTINCT value) INTO v_inj FROM comb.witness_value WHERE witness_id = v_wid;
    SELECT COUNT(*) INTO v_img
      FROM comb.witness_value v
     WHERE v.witness_id = v_wid
       AND EXISTS (SELECT 1 FROM comb.element e
                    WHERE e.structure_id = v_tid AND e.idx = v.value);

    IF v_dom <> s_n OR v_inj <> v_dom OR v_img <> v_dom THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', 'the map is not a bijection of the ground sets',
            'assigned', v_dom, 'distinct_images', v_inj,
            'images_in_target', v_img, 'ground_n', s_n);
        RETURN NEXT; RETURN;
    END IF;

    -- Every source block must have an image block in the target, signature and
    -- all; equal block counts then make the correspondence onto.
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
               'block_idx', x.block_idx, 'image_signature', x.sig)), '[]'::jsonb),
           COUNT(*)
      INTO v_bad, v_n
      FROM (SELECT b.idx AS block_idx,
                   (SELECT COALESCE(array_agg(
                               m.value || ':' || COALESCE(i.position::TEXT, '-')
                               ORDER BY m.value), '{}')
                      FROM comb.incidence i
                      JOIN comb.witness_value m
                        ON m.witness_id = v_wid AND m.element_idx = i.element_idx
                     WHERE i.structure_id = v_sid AND i.block_idx = b.idx) AS sig
              FROM comb.block b WHERE b.structure_id = v_sid) x
     WHERE NOT EXISTS (
         SELECT 1 FROM comb.block tb
          WHERE tb.structure_id = v_tid
            AND comb.block_signature(v_tid, tb.idx) = x.sig);

    SELECT COUNT(*) INTO s_blk FROM comb.block WHERE structure_id = v_sid;
    SELECT COUNT(*) INTO t_blk FROM comb.block WHERE structure_id = v_tid;

    IF v_n > 0 OR s_blk <> t_blk THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', CASE WHEN v_n > 0
                           THEN 'a source block has no image block in the target'
                           ELSE 'block counts differ' END,
            'witness', p_name, 'unmapped_blocks', v_bad,
            'source_blocks', s_blk, 'target_blocks', t_blk);
        RETURN NEXT; RETURN;
    END IF;

    ok := true;
    evidence := jsonb_build_object(
        'witness', p_name, 'ground_n', s_n, 'blocks', s_blk,
        'source', p_subject,
        'target', (SELECT subject_id FROM comb.structure WHERE id = v_tid));
    RETURN NEXT;
END
$$;

-- ---------------------------------------------------------------------------
-- 5. Existential bounds -- valid or unverified, never refuted
-- ---------------------------------------------------------------------------

-- chi(G) <= k, decided ONLY by what has been deposited. A proper colouring on
-- record using at most k colours proves it. Nothing on record proves nothing:
-- that is `unverified`, and it must never be reported as refuted, which would
-- assert chi > k on the strength of no one having tried.
CREATE OR REPLACE FUNCTION comb.chromatic_at_most(p_subject TEXT, p_k INTEGER)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE v_hit RECORD; v_tried INTEGER;
BEGIN
    SELECT w.name,
           (SELECT COUNT(DISTINCT value) FROM comb.witness_value
             WHERE witness_id = w.id) AS colours
      INTO v_hit
      FROM comb.witness w
      CROSS JOIN LATERAL comb.colouring_is_proper(p_subject, w.name) c
     WHERE w.structure_id = comb.structure_id(p_subject)
       AND w.kind = 'colouring'
       AND c.ok
       AND (SELECT COUNT(DISTINCT value) FROM comb.witness_value
             WHERE witness_id = w.id) <= p_k
     ORDER BY colours
     LIMIT 1;

    SELECT COUNT(*) INTO v_tried
      FROM comb.witness w
     WHERE w.structure_id = comb.structure_id(p_subject) AND w.kind = 'colouring';

    IF v_hit.name IS NOT NULL THEN
        ok := true;
        evidence := jsonb_build_object(
            'structure', p_subject, 'k', p_k,
            'witness', v_hit.name, 'colours_used', v_hit.colours);
        RETURN NEXT; RETURN;
    END IF;

    ok := NULL;
    evidence := jsonb_build_object(
        'reason', format('no proper colouring with at most %s colours is on record', p_k),
        'structure', p_subject, 'k', p_k,
        'colourings_on_record', v_tried,
        'status', 'absence of evidence, not evidence of absence');
    RETURN NEXT;
END
$$;

-- alpha(G) >= m, on the same terms and for the same reason.
CREATE OR REPLACE FUNCTION comb.independence_at_least(p_subject TEXT, p_m INTEGER)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE v_hit RECORD; v_tried INTEGER;
BEGIN
    SELECT w.name,
           (SELECT COUNT(*) FROM comb.witness_value WHERE witness_id = w.id) AS size
      INTO v_hit
      FROM comb.witness w
      CROSS JOIN LATERAL comb.subset_is_independent(p_subject, w.name) c
     WHERE w.structure_id = comb.structure_id(p_subject)
       AND w.kind = 'subset'
       AND c.ok
       AND (SELECT COUNT(*) FROM comb.witness_value WHERE witness_id = w.id) >= p_m
     ORDER BY size DESC
     LIMIT 1;

    SELECT COUNT(*) INTO v_tried
      FROM comb.witness w
     WHERE w.structure_id = comb.structure_id(p_subject) AND w.kind = 'subset';

    IF v_hit.name IS NOT NULL THEN
        ok := true;
        evidence := jsonb_build_object(
            'structure', p_subject, 'm', p_m,
            'witness', v_hit.name, 'size', v_hit.size);
        RETURN NEXT; RETURN;
    END IF;

    ok := NULL;
    evidence := jsonb_build_object(
        'reason', format('no independent set of size %s or more is on record', p_m),
        'structure', p_subject, 'm', p_m,
        'subsets_on_record', v_tried,
        'status', 'absence of evidence, not evidence of absence');
    RETURN NEXT;
END
$$;

-- ---------------------------------------------------------------------------
-- 6. Claims
-- ---------------------------------------------------------------------------

-- Internal. Callers below build both arguments from typed parameters; there is
-- no path from user input to probe_sql that does not go through format(%L).
CREATE OR REPLACE FUNCTION comb.mint_claim(
    p_subject TEXT, p_statement TEXT, p_probe TEXT, p_ref JSONB
) RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE v_id BIGINT;
BEGIN
    PERFORM comb.structure_id(p_subject);
    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind,
                            method, probe_sql)
    VALUES ('comb_property', p_ref, p_statement, 'computational', 'comp_sql', p_probe)
    ON CONFLICT (statement) DO UPDATE SET probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

CREATE OR REPLACE FUNCTION comb.primitive_family_claim(p_subject TEXT)
RETURNS BIGINT LANGUAGE sql AS $$
    SELECT comb.mint_claim(p_subject,
        format('the family %L is primitive: no block contains another', p_subject),
        format('SELECT ok, evidence FROM comb.is_primitive_family(%L)', p_subject),
        jsonb_build_object('structure', p_subject, 'property', 'primitive_family'));
$$;

CREATE OR REPLACE FUNCTION comb.intersecting_family_claim(p_subject TEXT)
RETURNS BIGINT LANGUAGE sql AS $$
    SELECT comb.mint_claim(p_subject,
        format('the family %L is intersecting: every two blocks meet', p_subject),
        format('SELECT ok, evidence FROM comb.is_intersecting_family(%L)', p_subject),
        jsonb_build_object('structure', p_subject, 'property', 'intersecting_family'));
$$;

CREATE OR REPLACE FUNCTION comb.uniformity_claim(p_subject TEXT, p_k INTEGER)
RETURNS BIGINT LANGUAGE sql AS $$
    SELECT comb.mint_claim(p_subject,
        format('every block of %L has exactly %s elements', p_subject, p_k),
        format('SELECT ok, evidence FROM comb.is_uniform(%L, %s)', p_subject, p_k),
        jsonb_build_object('structure', p_subject, 'property', 'uniform', 'k', p_k));
$$;

CREATE OR REPLACE FUNCTION comb.triangle_free_claim(p_subject TEXT)
RETURNS BIGINT LANGUAGE sql AS $$
    SELECT comb.mint_claim(p_subject,
        format('the graph %L is triangle-free', p_subject),
        format('SELECT ok, evidence FROM comb.is_triangle_free(%L)', p_subject),
        jsonb_build_object('structure', p_subject, 'property', 'triangle_free'));
$$;

-- About the witness. The statement names it, so that a reader of the ledger
-- cannot mistake a refutation here for a statement about the graph.
CREATE OR REPLACE FUNCTION comb.colouring_claim(p_subject TEXT, p_name TEXT)
RETURNS BIGINT LANGUAGE sql AS $$
    SELECT comb.mint_claim(p_subject,
        format('colouring %L properly colours %L', p_name, p_subject),
        format('SELECT ok, evidence FROM comb.colouring_is_proper(%L, %L)',
               p_subject, p_name),
        jsonb_build_object('structure', p_subject, 'witness', p_name,
                           'property', 'proper_colouring'));
$$;

CREATE OR REPLACE FUNCTION comb.independent_set_claim(p_subject TEXT, p_name TEXT)
RETURNS BIGINT LANGUAGE sql AS $$
    SELECT comb.mint_claim(p_subject,
        format('subset %L is independent in %L', p_name, p_subject),
        format('SELECT ok, evidence FROM comb.subset_is_independent(%L, %L)',
               p_subject, p_name),
        jsonb_build_object('structure', p_subject, 'witness', p_name,
                           'property', 'independent_set'));
$$;

CREATE OR REPLACE FUNCTION comb.isomorphism_claim(p_subject TEXT, p_name TEXT)
RETURNS BIGINT LANGUAGE sql AS $$
    SELECT comb.mint_claim(p_subject,
        format('bijection %L is an isomorphism from %L', p_name, p_subject),
        format('SELECT ok, evidence FROM comb.bijection_is_isomorphism(%L, %L)',
               p_subject, p_name),
        jsonb_build_object('structure', p_subject, 'witness', p_name,
                           'property', 'isomorphism'));
$$;

-- About the object, and one-sided: these can go valid or stay unverified, and
-- cert.check maps a NULL ok to unverified without any special casing.
CREATE OR REPLACE FUNCTION comb.chromatic_bound_claim(p_subject TEXT, p_k INTEGER)
RETURNS BIGINT LANGUAGE sql AS $$
    SELECT comb.mint_claim(p_subject,
        format('chi(%L) <= %s', p_subject, p_k),
        format('SELECT ok, evidence FROM comb.chromatic_at_most(%L, %s)', p_subject, p_k),
        jsonb_build_object('structure', p_subject, 'property', 'chromatic_upper',
                           'k', p_k));
$$;

CREATE OR REPLACE FUNCTION comb.independence_bound_claim(p_subject TEXT, p_m INTEGER)
RETURNS BIGINT LANGUAGE sql AS $$
    SELECT comb.mint_claim(p_subject,
        format('alpha(%L) >= %s', p_subject, p_m),
        format('SELECT ok, evidence FROM comb.independence_at_least(%L, %s)',
               p_subject, p_m),
        jsonb_build_object('structure', p_subject, 'property', 'independence_lower',
                           'm', p_m));
$$;

COMMENT ON TABLE comb.witness IS
    'A producer-supplied substructure -- colouring, subset or bijection -- '
    'stored rather than passed so that a cert probe can re-check it later '
    'without the caller. Untrusted until a 111 probe re-derives it. Refuting a '
    'witness refutes the witness, never the object it was proposed about.';
