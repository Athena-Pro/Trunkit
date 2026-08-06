-- Unified model, step 110: the `comb` schema -- finite combinatorial structures.
--
-- Implements gap T2 of docs/reports/Trunkit_Erdos_AI_Capability_Fit.md, per the
-- sketch in docs/DESIGN_COMB_FINITE_STRUCTURES.md. This step is the store and
-- its well-formedness probe only; property probes are 111, geometry is 112.
--
-- WHY A NEW SCHEMA AND NOT calx. calx's universe is Z[1..N] and its
-- factorization lattice; every table in it keys on an integer. A set family has
-- no n. The decisive reason is not taxonomy but lifecycle: `trunkit reset`
-- runs DROP TABLE calx.factorizations, calx.primes, calx.integers CASCADE, so
-- anything holding a foreign key into calx.integers is cascade-dropped by a
-- routine regeneration. calx is GENERATED -- rebuilt whenever the limit
-- changes. Combinatorial objects are DEPOSITED: a counterexample someone
-- carried in is not derivable from a sieve and must survive reset. Hence the
-- hard rule, which the rest of this layer must keep: `comb` declares no
-- foreign key into `calx`. Where a ground set happens to be {1..n} that is a
-- coincidence of labelling. A view may join the two; a constraint may not.
--
-- WHY NOT kan. kan.object/kan.morphism model finitely-presented categories:
-- morphisms compose and kan.sync_category() reflects real foreign keys into
-- them (20). A graph edge does not compose with an adjacent edge, and storing
-- edges as kan morphisms would assert that every graph is a category. The
-- reuse arrives from the other direction and for free: because sync_category
-- reflects ANY schema, `comb` becomes a kan category the moment it exists.
--
-- ONE TRIO FOR THREE OBJECTS. A set system is a family of subsets of a ground
-- set; a hypergraph is a set system; a graph is the 2-uniform case. Modelling
-- them as one incidence structure buys one probe language instead of three
-- parallel ones. comb.edge is a VIEW over the 2-uniform case, not a second
-- copy of the data.
--
-- WHAT THIS DELIBERATELY CANNOT REPRESENT. comb.incidence is keyed
-- (structure_id, block_idx, element_idx), so an element cannot appear twice in
-- one block: blocks are SETS, not multisets. Self-loops and multi-edges are
-- therefore not representable, which is correct for set systems and simple
-- graphs and is a real limit for multigraphs. Stated here rather than
-- discovered later.
--
-- GROUND_N IS A DECLARATION, THE ELEMENTS ARE THE FACT. register_structure
-- populates elements 0..ground_n-1 so the two agree by construction, and
-- comb.wellformed compares them so that later tampering is caught rather than
-- assumed away -- the same binding/observation split as 106.
--
-- Idempotent; additive only.

CREATE SCHEMA IF NOT EXISTS comb;

COMMENT ON SCHEMA comb IS
    'Finite combinatorial structures (T2): graphs, hypergraphs and set systems '
    'as one incidence trio. Deposited, not generated -- and by rule holds no '
    'foreign key into calx, whose tables are dropped CASCADE by trunkit reset.';

INSERT INTO cert.method (name, claim_kind, checker_kind, description)
VALUES ('comp_sql', 'computational', 'sql', 'in-DB probe returning (ok, evidence)')
ON CONFLICT (name) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 1. The store
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS comb.structure (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_id   TEXT NOT NULL UNIQUE,
    kind         TEXT NOT NULL CHECK (kind IN
                     ('graph', 'digraph', 'hypergraph', 'set_system')),
    ground_n     INTEGER NOT NULL CHECK (ground_n >= 0),
    -- Canonical-form digest from an EXTERNAL canonicaliser (nauty and friends
    -- stay outside the package). Graph isomorphism has no known polynomial
    -- algorithm and no in-SQL implementation is going to change that, so this
    -- is a deduplication HINT and never evidence: equal digests must not
    -- decide any verdict on their own. To make the canonicaliser trustworthy,
    -- the honest route already exists -- attest it as a tool fact under 104.
    canon_digest TEXT,
    canon_tool   TEXT,
    source       TEXT NOT NULL DEFAULT '',
    provenance   JSONB NOT NULL DEFAULT '{}'::jsonb,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The ground set. `idx` is canonical WITHIN one structure and meaningless
-- across two: a probe that joins on idx alone between structures is a bug that
-- will produce plausible answers.
CREATE TABLE IF NOT EXISTS comb.element (
    structure_id BIGINT NOT NULL REFERENCES comb.structure(id) ON DELETE CASCADE,
    idx          INTEGER NOT NULL CHECK (idx >= 0),
    label        TEXT,
    PRIMARY KEY (structure_id, idx)
);

-- Edges / hyperedges / member sets.
CREATE TABLE IF NOT EXISTS comb.block (
    structure_id BIGINT NOT NULL REFERENCES comb.structure(id) ON DELETE CASCADE,
    idx          INTEGER NOT NULL CHECK (idx >= 0),
    label        TEXT,
    PRIMARY KEY (structure_id, idx)
);

CREATE TABLE IF NOT EXISTS comb.incidence (
    structure_id BIGINT   NOT NULL,
    block_idx    INTEGER  NOT NULL,
    element_idx  INTEGER  NOT NULL,
    -- NULL for unordered blocks. For a digraph arc: 0 = tail, 1 = head.
    position     SMALLINT CHECK (position IS NULL OR position >= 0),
    PRIMARY KEY (structure_id, block_idx, element_idx),
    FOREIGN KEY (structure_id, block_idx)
        REFERENCES comb.block(structure_id, idx) ON DELETE CASCADE,
    FOREIGN KEY (structure_id, element_idx)
        REFERENCES comb.element(structure_id, idx) ON DELETE CASCADE
);

-- "Which blocks contain element x" is the access path every property probe in
-- 111 will take; the primary key only serves the other direction.
CREATE INDEX IF NOT EXISTS incidence_by_element_idx
    ON comb.incidence (structure_id, element_idx, block_idx);

-- The 2-uniform surface, so graph probes never hand-roll the join. A block
-- with any arity other than 2 is simply absent here rather than misreported.
CREATE OR REPLACE VIEW comb.edge AS
SELECT structure_id, block_idx,
       MIN(element_idx) AS u,
       MAX(element_idx) AS v
  FROM comb.incidence
 GROUP BY structure_id, block_idx
HAVING COUNT(*) = 2;

COMMENT ON VIEW comb.edge IS
    'Unordered 2-uniform blocks as (u, v) with u < v. Blocks of other arity do '
    'not appear. For digraphs use comb.arc, which keeps tail/head apart.';

-- Digraph arcs, where the order is the content.
CREATE OR REPLACE VIEW comb.arc AS
SELECT t.structure_id, t.block_idx,
       t.element_idx AS tail,
       h.element_idx AS head
  FROM comb.incidence t
  JOIN comb.incidence h
    ON h.structure_id = t.structure_id
   AND h.block_idx    = t.block_idx
   AND h.position     = 1
 WHERE t.position = 0;

-- ---------------------------------------------------------------------------
-- 2. Ingestion
-- ---------------------------------------------------------------------------

-- Re-runnable: re-registering an existing subject_id updates its metadata and
-- tops up its ground set, never truncating. Shrinking ground_n is refused
-- rather than silently deleting elements that blocks may still reference.
CREATE OR REPLACE FUNCTION comb.register_structure(
    p_subject    TEXT,
    p_kind       TEXT,
    p_ground_n   INTEGER,
    p_source     TEXT  DEFAULT '',
    p_provenance JSONB DEFAULT '{}'::jsonb
) RETURNS comb.structure
LANGUAGE plpgsql AS $$
DECLARE v_row comb.structure%ROWTYPE; v_prior INTEGER;
BEGIN
    SELECT ground_n INTO v_prior FROM comb.structure WHERE subject_id = p_subject;
    IF v_prior IS NOT NULL AND p_ground_n < v_prior THEN
        RAISE EXCEPTION
            'comb.register_structure: % already has ground_n=%, refusing to shrink to %',
            p_subject, v_prior, p_ground_n;
    END IF;

    INSERT INTO comb.structure (subject_id, kind, ground_n, source, provenance)
    VALUES (p_subject, p_kind, p_ground_n, p_source, p_provenance)
    ON CONFLICT (subject_id) DO UPDATE
        SET kind       = EXCLUDED.kind,
            ground_n   = EXCLUDED.ground_n,
            source     = EXCLUDED.source,
            provenance = EXCLUDED.provenance
    RETURNING * INTO v_row;

    INSERT INTO comb.element (structure_id, idx)
    SELECT v_row.id, g FROM generate_series(0, p_ground_n - 1) g
    ON CONFLICT (structure_id, idx) DO NOTHING;

    RETURN v_row;
END
$$;

CREATE OR REPLACE FUNCTION comb.structure_id(p_subject TEXT)
RETURNS BIGINT
LANGUAGE plpgsql STABLE AS $$
DECLARE v_id BIGINT;
BEGIN
    SELECT id INTO v_id FROM comb.structure WHERE subject_id = p_subject;
    IF v_id IS NULL THEN
        RAISE EXCEPTION 'comb: no structure % (register_structure first)', p_subject;
    END IF;
    RETURN v_id;
END
$$;

-- The signature of a block: its incidences as a sorted, position-tagged text
-- array. Two blocks are the same block iff their signatures match, which is
-- what makes ingestion re-runnable without a natural key on a set-valued
-- column.
CREATE OR REPLACE FUNCTION comb.block_signature(p_sid BIGINT, p_block INTEGER)
RETURNS TEXT[]
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(
        array_agg(i.element_idx || ':' || COALESCE(i.position::TEXT, '-')
                  ORDER BY i.element_idx),
        '{}')
      FROM comb.incidence i
     WHERE i.structure_id = p_sid AND i.block_idx = p_block;
$$;

-- Add one block. Returns its idx -- the existing one when an identical block
-- is already present, so replaying an ingestion script is a no-op rather than
-- a slow way to build a multiset.
--
-- p_positions, when given, must be the same length as p_elements and pairs up
-- with it positionally; NULL means an unordered block.
CREATE OR REPLACE FUNCTION comb.add_block(
    p_subject   TEXT,
    p_elements  INTEGER[],
    p_positions SMALLINT[] DEFAULT NULL,
    p_label     TEXT DEFAULT NULL
) RETURNS INTEGER
LANGUAGE plpgsql AS $$
DECLARE
    v_sid     BIGINT;
    v_n       INTEGER;
    v_sig     TEXT[];
    v_idx     INTEGER;
    v_missing INTEGER;
BEGIN
    v_sid := comb.structure_id(p_subject);

    IF p_elements IS NULL OR cardinality(p_elements) = 0 THEN
        RAISE EXCEPTION 'comb.add_block: a block needs at least one element';
    END IF;
    IF cardinality(p_elements) <> cardinality(ARRAY(SELECT DISTINCT unnest(p_elements))) THEN
        RAISE EXCEPTION
            'comb.add_block: a block is a set -- % repeats an element', p_elements;
    END IF;
    IF p_positions IS NOT NULL
       AND cardinality(p_positions) <> cardinality(p_elements) THEN
        RAISE EXCEPTION 'comb.add_block: positions and elements differ in length';
    END IF;

    SELECT e INTO v_missing
      FROM unnest(p_elements) e
     WHERE NOT EXISTS (SELECT 1 FROM comb.element
                        WHERE structure_id = v_sid AND idx = e)
     LIMIT 1;
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'comb.add_block: element % is not in the ground set of %',
                        v_missing, p_subject;
    END IF;

    -- Signature of the block we are about to add, built the same way
    -- comb.block_signature builds one from stored rows.
    SELECT COALESCE(array_agg(x.el || ':' || COALESCE(x.pos::TEXT, '-')
                              ORDER BY x.el), '{}')
      INTO v_sig
      FROM (SELECT p_elements[i] AS el,
                   CASE WHEN p_positions IS NULL THEN NULL ELSE p_positions[i] END AS pos
              FROM generate_subscripts(p_elements, 1) i) x;

    SELECT b.idx INTO v_idx
      FROM comb.block b
     WHERE b.structure_id = v_sid
       AND comb.block_signature(v_sid, b.idx) = v_sig
     LIMIT 1;
    IF v_idx IS NOT NULL THEN
        RETURN v_idx;
    END IF;

    SELECT COALESCE(MAX(idx), -1) + 1 INTO v_n
      FROM comb.block WHERE structure_id = v_sid;

    INSERT INTO comb.block (structure_id, idx, label) VALUES (v_sid, v_n, p_label);
    INSERT INTO comb.incidence (structure_id, block_idx, element_idx, position)
    SELECT v_sid, v_n, p_elements[i],
           CASE WHEN p_positions IS NULL THEN NULL ELSE p_positions[i] END
      FROM generate_subscripts(p_elements, 1) i;

    RETURN v_n;
END
$$;

-- Undirected edge. u = v is refused rather than silently dropped: the
-- incidence key cannot represent a self-loop, and a caller who asked for one
-- should be told, not quietly given a different graph.
CREATE OR REPLACE FUNCTION comb.add_edge(p_subject TEXT, p_u INTEGER, p_v INTEGER)
RETURNS INTEGER
LANGUAGE plpgsql AS $$
BEGIN
    IF p_u = p_v THEN
        RAISE EXCEPTION
            'comb.add_edge: self-loop at % -- blocks are sets, loops are not representable',
            p_u;
    END IF;
    RETURN comb.add_block(p_subject, ARRAY[p_u, p_v], NULL, NULL);
END
$$;

CREATE OR REPLACE FUNCTION comb.add_arc(p_subject TEXT, p_tail INTEGER, p_head INTEGER)
RETURNS INTEGER
LANGUAGE plpgsql AS $$
BEGIN
    IF p_tail = p_head THEN
        RAISE EXCEPTION
            'comb.add_arc: self-loop at % -- blocks are sets, loops are not representable',
            p_tail;
    END IF;
    RETURN comb.add_block(p_subject, ARRAY[p_tail, p_head],
                          ARRAY[0, 1]::SMALLINT[], NULL);
END
$$;

-- Record an external canonical form. Deliberately separate from registration:
-- a digest arrives from a tool run, not from the producer of the object, and
-- the tool that produced it is part of the record.
CREATE OR REPLACE FUNCTION comb.set_canonical(
    p_subject TEXT, p_digest TEXT, p_tool TEXT
) RETURNS comb.structure
LANGUAGE plpgsql AS $$
DECLARE v_row comb.structure%ROWTYPE;
BEGIN
    UPDATE comb.structure
       SET canon_digest = p_digest, canon_tool = p_tool
     WHERE id = comb.structure_id(p_subject)
    RETURNING * INTO v_row;
    RETURN v_row;
END
$$;

-- ---------------------------------------------------------------------------
-- 3. Well-formedness -- the one thing this step certifies
-- ---------------------------------------------------------------------------

-- Does the stored object actually satisfy the invariants of the kind it claims
-- to be? Everything here is structural: no mathematics is asserted, so this is
-- the rare probe that is total and cheap and can be re-run forever.
--
-- Refutes (ok = false) on:
--   * the declared ground_n disagreeing with the elements on record,
--   * an element index outside [0, ground_n),
--   * an empty block,
--   * graph/digraph blocks whose arity is not 2,
--   * digraph blocks not carrying exactly the positions {0, 1},
--   * graph blocks carrying positions at all,
--   * two blocks with identical signatures, for the kinds where that is a
--     contradiction (a simple graph has no parallel edges; a set FAMILY may
--     legitimately be reported without duplicates, so duplicates there are
--     surfaced in the evidence but do not refute).
--
-- A structure with no blocks is well-formed: the empty graph on n vertices is
-- a graph, and refusing it would make the register/populate sequence unable to
-- pass its own check between two statements.
CREATE OR REPLACE FUNCTION comb.wellformed(p_subject TEXT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    s          comb.structure%ROWTYPE;
    v_elements INTEGER;
    v_blocks   INTEGER;
    v_faults   JSONB := '[]'::jsonb;
    v_dups     JSONB;
    v_dup_n    INTEGER;
BEGIN
    SELECT * INTO s FROM comb.structure WHERE subject_id = p_subject;
    IF s.id IS NULL THEN
        ok := false;
        evidence := jsonb_build_object('reason', 'no such structure',
                                       'structure', p_subject);
        RETURN NEXT; RETURN;
    END IF;

    SELECT COUNT(*) INTO v_elements FROM comb.element WHERE structure_id = s.id;
    SELECT COUNT(*) INTO v_blocks   FROM comb.block   WHERE structure_id = s.id;

    IF v_elements <> s.ground_n THEN
        v_faults := v_faults || jsonb_build_object(
            'fault', 'ground_n disagrees with the elements on record',
            'declared', s.ground_n, 'stored', v_elements);
    END IF;

    v_faults := v_faults || COALESCE((
        SELECT jsonb_agg(jsonb_build_object(
                   'fault', 'element index outside [0, ground_n)',
                   'element_idx', e.idx, 'ground_n', s.ground_n))
          FROM comb.element e
         WHERE e.structure_id = s.id AND e.idx >= s.ground_n), '[]'::jsonb);

    -- Arity and position faults, per kind.
    v_faults := v_faults || COALESCE((
        SELECT jsonb_agg(f) FROM (
            SELECT jsonb_build_object(
                       'fault', CASE
                           WHEN a.arity = 0 THEN 'empty block'
                           WHEN s.kind IN ('graph', 'digraph') AND a.arity <> 2
                               THEN 'block arity is not 2'
                           WHEN s.kind = 'digraph' AND NOT a.is_arc
                               THEN 'digraph block does not carry positions {0,1}'
                           WHEN s.kind = 'graph' AND a.positioned
                               THEN 'graph block carries positions'
                           WHEN s.kind IN ('hypergraph', 'set_system') AND a.positioned
                               THEN 'unordered block carries positions'
                       END,
                       'block_idx', a.block_idx,
                       'arity', a.arity)
              FROM (SELECT b.idx AS block_idx,
                           COUNT(i.element_idx) AS arity,
                           bool_or(i.position IS NOT NULL) AS positioned,
                           COALESCE(array_agg(i.position ORDER BY i.position)
                                    FILTER (WHERE i.position IS NOT NULL)
                                    = ARRAY[0, 1]::SMALLINT[], false) AS is_arc
                      FROM comb.block b
                      LEFT JOIN comb.incidence i
                        ON i.structure_id = b.structure_id AND i.block_idx = b.idx
                     WHERE b.structure_id = s.id
                     GROUP BY b.idx) a
             WHERE a.arity = 0
                OR (s.kind IN ('graph', 'digraph') AND a.arity <> 2)
                OR (s.kind = 'digraph' AND NOT a.is_arc)
                OR (s.kind = 'graph' AND a.positioned)
                OR (s.kind IN ('hypergraph', 'set_system') AND a.positioned)
        ) t(f)), '[]'::jsonb);

    -- Duplicate blocks: a contradiction for graph/digraph, a remark otherwise.
    SELECT COALESCE(jsonb_agg(jsonb_build_object(
               'signature', d.sig, 'block_idxs', d.idxs)), '[]'::jsonb), COUNT(*)
      INTO v_dups, v_dup_n
      FROM (SELECT comb.block_signature(s.id, b.idx) AS sig,
                   array_agg(b.idx ORDER BY b.idx) AS idxs
              FROM comb.block b
             WHERE b.structure_id = s.id
             GROUP BY comb.block_signature(s.id, b.idx)
            HAVING COUNT(*) > 1) d;

    IF v_dup_n > 0 AND s.kind IN ('graph', 'digraph') THEN
        v_faults := v_faults || jsonb_build_object(
            'fault', 'parallel blocks in a simple structure', 'duplicates', v_dups);
    END IF;

    IF jsonb_array_length(v_faults) > 0 THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', 'structure violates the invariants of its kind',
            'structure', p_subject, 'kind', s.kind,
            'faults', v_faults, 'n_faults', jsonb_array_length(v_faults));
        RETURN NEXT; RETURN;
    END IF;

    ok := true;
    evidence := jsonb_build_object(
        'structure', p_subject, 'kind', s.kind,
        'ground_n', s.ground_n, 'blocks', v_blocks,
        'duplicate_blocks', CASE WHEN v_dup_n > 0 THEN v_dups ELSE NULL END);
    RETURN NEXT;
END
$$;

-- The well-formedness fact as a re-checkable claim, so that a structure edited
-- after it was accepted stops reading as accepted. Same stance as
-- cert.bound_consistency_claim (105): the facts worth storing as claims are the
-- ones that go stale.
CREATE OR REPLACE FUNCTION comb.wellformed_claim(p_subject TEXT)
RETURNS BIGINT
LANGUAGE plpgsql AS $$
DECLARE s comb.structure%ROWTYPE; v_stmt TEXT; v_probe TEXT; v_id BIGINT;
BEGIN
    SELECT * INTO s FROM comb.structure WHERE subject_id = p_subject;
    IF s.id IS NULL THEN
        RAISE EXCEPTION 'comb: no structure %', p_subject;
    END IF;
    v_stmt  := format('%s %L is a well-formed %s on %s elements',
                      'combinatorial structure', p_subject, s.kind, s.ground_n);
    v_probe := format('SELECT ok, evidence FROM comb.wellformed(%L)', p_subject);
    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind,
                            method, probe_sql)
    VALUES ('comb_structure', jsonb_build_object('structure', p_subject),
            v_stmt, 'computational', 'comp_sql', v_probe)
    ON CONFLICT (statement) DO UPDATE SET probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$;

COMMENT ON TABLE comb.structure IS
    'Finite combinatorial structure (T2): graph, digraph, hypergraph or set '
    'system, all as one incidence trio with comb.edge/comb.arc as views over '
    'it. Blocks are sets, so self-loops and multi-edges are not representable. '
    'ground_n is the declaration and comb.element is the fact; comb.wellformed '
    'compares them.';
