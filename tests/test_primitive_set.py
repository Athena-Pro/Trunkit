"""Tests for divisibility-primitive sets (116) -- the finite half of Erdos #1196.

Two things are pinned.

That the probe is COMPLETE on the set it is given: primes pass, any dividing
pair fails with the whole list of pairs, and 1 poisons every set it joins.
Divisibility is decidable and finite here, so there is no excuse for a partial
answer -- an unparsable label makes the probe refuse rather than quietly rule on
the parsable subset.

And that it does not reach further than that. #1196 is a conjecture about all
primitive sets; "this 12-element set is primitive" is not evidence for it. So
the asymptotic anchor carries no derivation edge into the finite claims, the
same rule 115 keeps for "for all n".

Also pinned: the divisibility form and the set-system form (111) agree on
squarefree integers and diverge the moment an exponent exceeds 1. That
divergence is the reason this step exists, so it gets a test rather than a
comment.

Skips cleanly when no test DSN is set.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest


def _dsn():
    dsn = os.environ.get("CALX_TEST_DSN") or os.environ.get("ARITHMETIC_DB_TEST_DSN")
    if not dsn:
        pytest.skip("No test DSN provided. Refusing to write to default/production ledger.")
    return dsn


@pytest.fixture()
def conn():
    try:
        c = psycopg.connect(_dsn(), connect_timeout=3)
    except psycopg.Error as exc:
        pytest.skip(f"test DB not reachable: {exc}")
    c.autocommit = True
    try:
        with c.cursor() as cur:
            cur.execute("SELECT to_regprocedure('comb.is_primitive_set(text)')")
            if cur.fetchone()[0] is None:
                pytest.skip("comb.is_primitive_set missing — apply 116_comb_primitive_set.sql")
        yield c
    finally:
        c.close()


def _set(cur, labels, kind="set_system") -> str:
    """Deposit a labelled integer set and return its subject id."""
    subject = f"pset-{uuid.uuid4()}"
    cur.execute("SELECT comb.register_structure(%s,%s,%s,%s)",
                (subject, kind, len(labels), "test"))
    if labels:
        cur.execute("SELECT comb.label_elements(%s,%s)",
                    (subject, [str(x) for x in labels]))
    return subject


def _probe(cur, subject):
    cur.execute("SELECT ok, evidence FROM comb.is_primitive_set(%s)", (subject,))
    return cur.fetchone()


# --- completeness on the given set ------------------------------------------

def test_the_primes_are_primitive(conn):
    with conn.cursor() as cur:
        ok, ev = _probe(cur, _set(cur, [2, 3, 5, 7, 11, 13]))
        assert ok is True
        assert ev["elements"] == 6
        assert ev["pairs_checked"] == 15


def test_a_dividing_pair_refutes_with_its_witness(conn):
    with conn.cursor() as cur:
        ok, ev = _probe(cur, _set(cur, [2, 3, 4]))
        assert ok is False
        assert ev["divisibility_chains"] == [{"divisor": 2, "multiple": 4, "quotient": 2}]


def test_every_dividing_pair_is_reported_not_just_the_first(conn):
    """A reader repairing a set wants the whole list."""
    with conn.cursor() as cur:
        ok, ev = _probe(cur, _set(cur, [2, 4, 8]))
        assert ok is False
        assert ev["violations"] == 3        # 2|4, 2|8, 4|8
        pairs = {(c["divisor"], c["multiple"]) for c in ev["divisibility_chains"]}
        assert pairs == {(2, 4), (2, 8), (4, 8)}


def test_one_poisons_any_set_it_joins(conn):
    with conn.cursor() as cur:
        ok, ev = _probe(cur, _set(cur, [1, 7, 11]))
        assert ok is False
        assert {c["divisor"] for c in ev["divisibility_chains"]} == {1}
        assert ev["violations"] == 2


def test_a_singleton_and_the_empty_set_are_primitive(conn):
    with conn.cursor() as cur:
        assert _probe(cur, _set(cur, [6]))[0] is True
        assert _probe(cur, _set(cur, []))[0] is True


def test_coprimality_is_not_required(conn):
    """6 and 10 share a factor and neither divides the other."""
    with conn.cursor() as cur:
        assert _probe(cur, _set(cur, [6, 10, 15]))[0] is True


def test_an_unparsable_label_makes_the_probe_refuse(conn):
    """Ruling on the parsable subset would answer about a different set."""
    with conn.cursor() as cur:
        subject = _set(cur, [2, 3])
        cur.execute("UPDATE comb.element SET label = 'x' WHERE idx = 0"
                    " AND structure_id = comb.structure_id(%s)", (subject,))
        ok, ev = _probe(cur, subject)
        assert ok is False
        assert "not decidable here" in ev["reason"]
        assert ev["unlabelled_or_unparsable"] == 1


def test_unlabelled_elements_are_refused_too(conn):
    with conn.cursor() as cur:
        subject = f"pset-{uuid.uuid4()}"
        cur.execute("SELECT comb.register_structure(%s,'set_system',3,'test')", (subject,))
        ok, ev = _probe(cur, subject)
        assert ok is False
        assert ev["unlabelled_or_unparsable"] == 3


def test_a_repeated_label_is_a_remark_not_a_refutation(conn):
    """111's ruling for equal blocks, applied to elements: a set entered twice
    is a well-formedness observation, not a failure of primitivity."""
    with conn.cursor() as cur:
        ok, ev = _probe(cur, _set(cur, [6, 6, 35]))
        assert ok is True
        assert ev["repeated_labels"] == 1


def test_labelling_rejects_a_length_mismatch(conn):
    with conn.cursor() as cur:
        subject = f"pset-{uuid.uuid4()}"
        cur.execute("SELECT comb.register_structure(%s,'set_system',3,'test')", (subject,))
        with pytest.raises(psycopg.errors.RaiseException, match="3 elements"):
            cur.execute("SELECT comb.label_elements(%s,%s)", (subject, ["2", "3"]))


# --- the two forms of primitivity -------------------------------------------

def test_divisibility_and_set_system_forms_diverge_on_prime_powers(conn):
    """The reason 116 exists. Under the prime-support map the two agree on
    squarefree integers; 2 | 4 while supp(2)={2} does not strictly contain
    itself, so containment misses it."""
    with conn.cursor() as cur:
        # As integers: 2 divides 4, so NOT primitive.
        assert _probe(cur, _set(cur, [2, 4]))[0] is False

        # As a set system over prime supports: {2} and {2} -- equal, not strict
        # containment, so the family form calls it primitive.
        family = f"fam-{uuid.uuid4()}"
        cur.execute("SELECT comb.register_structure(%s,'set_system',1,'test')", (family,))
        cur.execute("SELECT comb.add_block(%s,%s)", (family, [0]))
        cur.execute("SELECT comb.add_block(%s,%s)", (family, [0]))
        cur.execute("SELECT ok FROM comb.is_primitive_family(%s)", (family,))
        assert cur.fetchone()[0] is True


# --- claims and the carried object ------------------------------------------

def test_the_claim_re_derives_the_verdict(conn):
    with conn.cursor() as cur:
        subject = _set(cur, [2, 3, 5])
        cur.execute("SELECT comb.primitive_set_claim(%s)", (subject,))
        claim = cur.fetchone()[0]
        cur.execute("SELECT probe_sql, domain FROM cert.claim WHERE id = %s", (claim,))
        probe, domain = cur.fetchone()
        assert domain == "exact_int"
        cur.execute(probe)
        assert cur.fetchone()[0] is True


def test_the_object_travels_with_the_verdict(conn):
    with conn.cursor() as cur:
        subject = _set(cur, [3, 5, 7])
        cur.execute("SELECT ok, witness FROM comb.carried_primitive_set(%s)", (subject,))
        ok, witness = cur.fetchone()
        assert ok is True
        assert witness["kind"] == "primitive_set"
        assert witness["elements"] == ["3", "5", "7"]


def test_the_object_travels_even_when_refuted(conn):
    """A reader most wants to see the set when the verdict went against it."""
    with conn.cursor() as cur:
        subject = _set(cur, [2, 6])
        cur.execute("SELECT ok, witness FROM comb.carried_primitive_set(%s)", (subject,))
        ok, witness = cur.fetchone()
        assert ok is False
        assert witness["elements"] == ["2", "6"]


# --- the honesty rule -------------------------------------------------------

def test_the_asymptotic_anchor_has_no_premises(conn):
    """#1196 is quantified over all primitive sets. One primitive set is not
    evidence, and the ledger must not record it as such."""
    with conn.cursor() as cur:
        subject = _set(cur, [2, 3, 5, 7])
        cur.execute("SELECT comb.primitive_set_claim(%s)", (subject,))
        finite = cur.fetchone()[0]
        stmt = (f"Erdos 1196 test anchor {uuid.uuid4()}: the sum of 1/(a log a) over "
                "a primitive set is maximised by the primes")
        cur.execute("SELECT comb.anchor_asymptotic(%s,%s,%s,%s)",
                    (stmt, "erdos_1196", [finite], "arXiv:0000.00000"))
        anchor = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM cert.derivation WHERE conclusion_id = %s",
                    (anchor,))
        assert cur.fetchone()[0] == 0

        cur.execute("SELECT method, probe_sql, subject_kind, subject_ref"
                    " FROM cert.claim WHERE id = %s", (anchor,))
        method, probe, kind, ref = cur.fetchone()
        assert method == "formal_external"
        assert probe is None
        # Joins the convention already in the ledger rather than starting a
        # parallel namespace.
        assert kind == "erdos_problem"
        assert ref["finite_evidence"] == [finite]
        assert "do not support it" in ref["note"]
