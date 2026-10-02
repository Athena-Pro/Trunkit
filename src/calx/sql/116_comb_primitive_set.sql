-- Unified model, step 116: primitive sets of integers -- the finite half of #1196.
--
-- 111 already has comb.is_primitive_family, the SET-SYSTEM form: no block
-- strictly contains another. Its header calls that "the finite, in-DB half of
-- Erdos #1196", and for squarefree integers under the prime-support map that is
-- exactly right -- a | b iff supp(a) subset supp(b). It stops being right the
-- moment an exponent exceeds 1, because 2 divides 4 while {2} does not strictly
-- contain {2}. Containment of SETS is not divisibility of INTEGERS, and #1196 is
-- about integers. This step adds the divisibility form beside it rather than
-- pretending the analogy covers the general case.
--
-- WHY THE LABELS AND NOT calx.integers. 110's rule stands: comb declares no
-- foreign key into calx, because calx is GENERATED and `trunkit reset` drops it
-- CASCADE while a deposited object must survive. A primitive set someone
-- carried in is deposited. So the integers live in comb.element.label -- text,
-- because that column already exists and because, as 110 puts it, a ground set
-- that happens to be {1..n} is "a coincidence of labelling". The probe parses
-- the labels and says so in its evidence; it never joins calx.
--
-- ============================================================================
-- WHAT THIS SETTLES, AND WHAT IT CANNOT TOUCH.
-- ============================================================================
-- comb.is_primitive_set decides primitivity OF A GIVEN FINITE SET, completely
-- and exactly. That is a real verdict and it is the whole of what calx can do
-- here.
--
-- Erdos #1196 is the conjecture that sum 1/(a log a) over a primitive set is
-- maximised by the primes. That is an asymptotic statement over infinitely many
-- infinite sets. No finite probe approaches it, and the capability report is
-- blunt about the failure mode -- "Trunkit can anchor the Lean proof and attest
-- finite corollaries, but not the theorem. Don't oversell calx here."
--
-- So this file mints finite claims only. The asymptotic statement is anchored
-- as formal_external with NO derivation edge into any of them, exactly as 115
-- does for "for all n": a set being primitive is not evidence for a conjecture
-- about all primitive sets, and a ledger that recorded it as such would be
-- reporting a theorem as supported because one example behaved.
--
-- DUPLICATES ARE A WELL-FORMEDNESS REMARK, NOT A REFUTATION -- following 111's
-- ruling for equal blocks. Two elements labelled 6 do divide each other, but
-- what that reveals is a set entered twice, not a failure of primitivity. They
-- are counted and reported separately so the reader can see why a set looks
-- smaller than its element count.
--
-- Idempotent; additive only.

-- ---------------------------------------------------------------------------
-- 1. labelling -- register_structure creates elements with NULL labels
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION comb.label_elements(p_subject TEXT, p_labels TEXT[])
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE v_sid BIGINT; v_n INTEGER; v_have INTEGER;
BEGIN
    v_sid := comb.structure_id(p_subject);
    IF v_sid IS NULL THEN
        RAISE EXCEPTION 'comb.label_elements: no structure %', p_subject;
    END IF;
    SELECT COUNT(*) INTO v_have FROM comb.element WHERE structure_id = v_sid;
    v_n := cardinality(p_labels);
    IF v_n <> v_have THEN
        RAISE EXCEPTION 'comb.label_elements: % labels for % elements', v_n, v_have;
    END IF;
    UPDATE comb.element e
       SET label = p_labels[e.idx + 1]     -- elements are 0-indexed, arrays are 1-
     WHERE e.structure_id = v_sid;
    RETURN v_n;
END $$;

COMMENT ON FUNCTION comb.label_elements(TEXT, TEXT[]) IS
    'Attach labels to a structure''s elements, in index order. For a primitive '
    'set the labels ARE the integers -- comb holds no foreign key into calx.';

-- ---------------------------------------------------------------------------
-- 2. the probe
-- ---------------------------------------------------------------------------

-- Exhaustive over ordered pairs: for a deposited counterexample family the sets
-- are small, and a partial check would be worse than none. The witness is every
-- dividing pair found, not just the first -- a reader repairing a set wants the
-- whole list.
CREATE OR REPLACE FUNCTION comb.is_primitive_set(p_subject TEXT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_sid BIGINT; v_n INTEGER; v_bad INTEGER; v_dups INTEGER;
    v_unparsed INTEGER; v_chains JSONB;
BEGIN
    v_sid := comb.structure_id(p_subject);
    IF v_sid IS NULL THEN
        ok := false;
        evidence := jsonb_build_object('reason', 'no such structure',
                                       'structure', p_subject);
        RETURN NEXT; RETURN;
    END IF;

    -- A label that is not a positive integer cannot be tested for divisibility.
    -- Refusing beats guessing: an unparsable set is UNVERIFIED territory, and
    -- returning ok=true over the parsable subset would be a verdict on a
    -- different set than the one asked about.
    SELECT COUNT(*) INTO v_unparsed
      FROM comb.element
     WHERE structure_id = v_sid
       AND (label IS NULL OR label !~ '^[1-9][0-9]*$');
    IF v_unparsed > 0 THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', 'elements without a positive-integer label -- not decidable here',
            'structure', p_subject, 'unlabelled_or_unparsable', v_unparsed);
        RETURN NEXT; RETURN;
    END IF;

    SELECT COUNT(*) INTO v_n FROM comb.element WHERE structure_id = v_sid;

    -- Equal labels are duplicates, not divisibility failures (111's ruling for
    -- equal blocks, applied to elements).
    SELECT COUNT(*) INTO v_dups FROM (
        SELECT label FROM comb.element WHERE structure_id = v_sid
         GROUP BY label HAVING COUNT(*) > 1) d;

    SELECT COALESCE(jsonb_agg(jsonb_build_object(
               'divisor', a.label::BIGINT, 'multiple', b.label::BIGINT,
               'quotient', b.label::BIGINT / a.label::BIGINT)
               ORDER BY a.label::BIGINT, b.label::BIGINT), '[]'::jsonb),
           COUNT(*)
      INTO v_chains, v_bad
      FROM comb.element a
      JOIN comb.element b
        ON b.structure_id = a.structure_id
       AND a.label::BIGINT < b.label::BIGINT          -- strict: excludes duplicates
     WHERE a.structure_id = v_sid
       AND b.label::BIGINT % a.label::BIGINT = 0;

    IF v_bad > 0 THEN
        ok := false;
        evidence := jsonb_build_object(
            'reason', 'an element divides another -- set is not primitive',
            'structure', p_subject, 'elements', v_n,
            'divisibility_chains', v_chains, 'violations', v_bad,
            'repeated_labels', v_dups);
        RETURN NEXT; RETURN;
    END IF;

    ok := true;
    evidence := jsonb_build_object(
        'structure', p_subject, 'elements', v_n,
        'pairs_checked', (v_n * (v_n - 1)) / 2,
        'repeated_labels', v_dups,
        'note', 'primitive: no element divides another. Says nothing about #1196.');
    RETURN NEXT;
END $$;

COMMENT ON FUNCTION comb.is_primitive_set(TEXT) IS
    'Divisibility form of primitivity: no element divides another. Complete and '
    'exact for the given finite set, and silent about the asymptotic conjecture. '
    'The set-system form is comb.is_primitive_family (111); the two agree only '
    'on squarefree integers.';

-- ---------------------------------------------------------------------------
-- 3. claims
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION comb.primitive_set_claim(p_subject TEXT)
RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE v_id BIGINT;
BEGIN
    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind,
                            method, probe_sql, domain)
    VALUES ('comb_structure',
            jsonb_build_object('structure', p_subject, 'property', 'primitive_set'),
            format('the set %L is primitive: no element divides another', p_subject),
            'computational', 'comp_sql',
            format('SELECT ok, evidence FROM comb.is_primitive_set(%L)', p_subject),
            'exact_int')
    ON CONFLICT (statement) DO UPDATE SET probe_sql = EXCLUDED.probe_sql
    RETURNING id INTO v_id;
    RETURN v_id;
END $$;

-- The carried form: the object travels with the verdict, so a consumer
-- re-derives primitivity from the set itself rather than trusting that someone
-- once ran a probe. Same reasoning as 113 -- for a construction the object IS
-- the proof term.
CREATE OR REPLACE FUNCTION comb.carried_primitive_set(p_subject TEXT)
RETURNS TABLE (ok BOOLEAN, evidence JSONB, witness JSONB)
LANGUAGE plpgsql STABLE AS $$
DECLARE v_ok BOOLEAN; v_ev JSONB; v_sid BIGINT;
BEGIN
    SELECT p.ok, p.evidence INTO v_ok, v_ev FROM comb.is_primitive_set(p_subject) p;
    v_sid := comb.structure_id(p_subject);
    ok := v_ok; evidence := v_ev;
    witness := jsonb_build_object(
        'kind', 'primitive_set',
        'structure', p_subject,
        'elements', COALESCE((SELECT jsonb_agg(e.label ORDER BY e.idx)
                                FROM comb.element e WHERE e.structure_id = v_sid),
                             '[]'::jsonb));
    RETURN NEXT;
END $$;

-- ---------------------------------------------------------------------------
-- 4. the asymptotic statement -- anchored, never derived
-- ---------------------------------------------------------------------------

-- Deliberately mirrors calx.oeis_anchor_theorem (115), including the refusal to
-- record a derivation edge. The finite claims are motivation in subject_ref and
-- nothing more: "this 12-element set is primitive" is not evidence for a
-- conjecture quantified over all primitive sets.
CREATE OR REPLACE FUNCTION comb.anchor_asymptotic(
    p_statement TEXT, p_problem TEXT,
    p_finite_claims BIGINT[] DEFAULT '{}', p_locator TEXT DEFAULT NULL
) RETURNS BIGINT LANGUAGE plpgsql AS $$
DECLARE v_id BIGINT;
BEGIN
    INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind,
                            method, probe_sql, domain)
    VALUES ('erdos_problem',
            jsonb_build_object(
                'problem', p_problem, 'locator', p_locator,
                'finite_evidence', to_jsonb(p_finite_claims),
                'note', 'finite corollaries motivate this; they do not support it'),
            p_statement, 'formal', 'formal_external', NULL, 'unspecified')
    ON CONFLICT (statement) DO UPDATE SET subject_ref = EXCLUDED.subject_ref
    RETURNING id INTO v_id;
    RETURN v_id;
END $$;

COMMENT ON FUNCTION comb.anchor_asymptotic(TEXT, TEXT, BIGINT[], TEXT) IS
    'Anchor an asymptotic statement as formal_external with no premise edge '
    'into the finite chain. subject_kind is erdos_problem, matching the '
    'convention already in the ledger, so the anchor joins the existing '
    'Lean-bridge claims instead of starting a parallel namespace.';
