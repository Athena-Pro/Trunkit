"""Tests for the OEIS conjecture -> attestation workflow (115).

The layer is deliberately thin -- every verification belongs to 92/93/95 and is
tested there -- so these tests are about the COMPOSITION, and overwhelmingly
about the one rule that makes the composition honest:

    a bounded prefix never implies "for all n".

So the global theorem must carry no derivation premises, the finite composite
must name its own horizon, and the horizon must be the weaker of the two finite
witnesses. Those three are asserted directly and mutation-checked, because they
are the properties a plausible-looking refactor would quietly destroy.

Fibonacci is the worked example: it has an obvious C-finite recurrence, an
obvious scale morphism onto 2*Fib, and cosine cannot tell those apart -- which
is exactly why the chain exists.

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
            cur.execute("SELECT to_regprocedure("
                        "'calx.oeis_attest(text,text,bigint,bigint,int,text,text,text)')")
            if cur.fetchone()[0] is None:
                pytest.skip("calx.oeis_attest missing — apply 115_oeis_attest.sql")
        yield c
    finally:
        c.close()


def _fib(k):
    a, b, out = 1, 1, []
    for _ in range(k):
        out.append(a)
        a, b = b, a + b
    return out


@pytest.fixture()
def seqs(conn):
    """A query sequence and an identical-prefix candidate, both descriptor-backed."""
    tag = uuid.uuid4().hex[:8]
    q, c = f"Q_{tag}", f"C_{tag}"
    terms = _fib(12)
    with conn.cursor() as cur:
        for sid in (q, c):
            cur.execute(
                "INSERT INTO calx.seq_vector (seq_id, vector_kind, k, terms, vec) "
                "VALUES (%s,'logc',%s,%s, calx.vectorize_terms(%s::numeric[], %s)) "
                "ON CONFLICT (seq_id, vector_kind) DO UPDATE "
                "SET terms=EXCLUDED.terms, vec=EXCLUDED.vec",
                (sid, len(terms), terms, terms, len(terms)))
    return q, c, terms


def _recurrence(cur, seq_id, terms):
    """Fibonacci: a_n - a_{n-1} - a_{n-2} = 0, i.e. polys [[1],[-1],[-1]]."""
    cur.execute("SELECT (cert.register_recurrence(%s,'c-finite',%s::jsonb,"
                "%s::numeric[],%s::numeric[])).id",
                (seq_id, '[[1],[-1],[-1]]', terms[:2], terms))
    return cur.fetchone()[0]


def _premises(cur, claim_id):
    cur.execute("SELECT coalesce(sum(cardinality(premise_ids)),0) FROM cert.derivation"
                " WHERE conclusion_id = %s", (claim_id,))
    return cur.fetchone()[0]


def _chain(cur, *args):
    cur.execute("SELECT step, phase, claim_id, note FROM calx.oeis_attest("
                "%s,%s,%s,%s,%s,%s,%s,%s) ORDER BY step", args)
    return cur.fetchall()


# --- step 1: the candidate is permanently unverifiable -----------------------

def test_the_candidate_is_float_heuristic(conn, seqs):
    q, c, _ = seqs
    with conn.cursor() as cur:
        cur.execute("SELECT calx.oeis_candidate_claim(%s,%s,'logc')", (q, c))
        cid = cur.fetchone()[0]
        cur.execute("SELECT domain FROM cert.claim WHERE id = %s", (cid,))
        assert cur.fetchone()[0] == "float_heuristic"


def test_the_candidate_can_never_record_valid(conn, seqs):
    """94's shield, exercised through this layer rather than assumed."""
    q, c, _ = seqs
    with conn.cursor() as cur:
        cur.execute("SELECT calx.oeis_candidate_claim(%s,%s,'logc')", (q, c))
        cid = cur.fetchone()[0]
        cur.execute("INSERT INTO cert.certificate (claim_id, seq, status)"
                    " VALUES (%s, 1, 'valid') RETURNING status", (cid,))
        assert cur.fetchone()[0] != "valid", "float_heuristic reached a valid verdict"


def test_the_candidate_probe_does_not_claim_a_finding(conn, seqs):
    """ok=false by construction: a candidate is not a result, and relying on 94
    to downgrade an ok=true would be misusing the shield."""
    q, c, _ = seqs
    with conn.cursor() as cur:
        # Two statements: calling the minting function inside the WHERE clause
        # would insert the row outside the SELECT's own snapshot.
        cur.execute("SELECT calx.oeis_candidate_claim(%s,%s,'logc')", (q, c))
        cur.execute("SELECT probe_sql FROM cert.claim WHERE id = %s", (cur.fetchone()[0],))
        cur.execute(cur.fetchone()[0])
        ok, evidence = cur.fetchone()
        assert ok is False
        assert evidence["cosine"] == pytest.approx(1.0, rel=1e-9)  # identical prefixes


# --- the finite composition -------------------------------------------------

def test_composition_earns_exactly_two_premises(conn, seqs):
    q, c, terms = seqs
    with conn.cursor() as cur:
        rec = _recurrence(cur, c, terms)
        chain = _chain(cur, q, c, rec, None, 8, "logc", None, None)
        phases = [row[1] for row in chain]
        assert phases == ["candidate", "exact_prefix", "recurrence", "finite_composition"]

        comp = chain[3][2]
        cur.execute("SELECT premise_ids, rule FROM cert.derivation"
                    " WHERE conclusion_id = %s", (comp,))
        premises, rule = cur.fetchone()
        assert sorted(premises) == sorted([chain[1][2], chain[2][2]])
        assert rule == "oeis_finite_agreement"


def test_the_composite_names_its_horizon(conn, seqs):
    q, c, terms = seqs
    with conn.cursor() as cur:
        rec = _recurrence(cur, c, terms)
        chain = _chain(cur, q, c, rec, None, 8, "logc", None, None)
        cur.execute("SELECT statement, subject_ref FROM cert.claim WHERE id = %s",
                    (chain[3][2],))
        stmt, ref = cur.fetchone()
        assert "over 8 terms" in stmt
        assert ref["horizon"] == 8


def test_the_horizon_is_the_weaker_of_the_two_witnesses(conn, seqs):
    """12 recurrence terms but a prefix of 5 must yield 5, never 12."""
    q, c, terms = seqs
    with conn.cursor() as cur:
        rec = _recurrence(cur, c, terms)
        chain = _chain(cur, q, c, rec, None, 5, "logc", None, None)
        cur.execute("SELECT subject_ref->>'horizon' FROM cert.claim WHERE id = %s",
                    (chain[3][2],))
        assert cur.fetchone()[0] == "5"


def test_the_morphism_path_composes_too(conn, seqs):
    q, c, terms = seqs
    doubled = [t * 2 for t in terms]
    with conn.cursor() as cur:
        cur.execute("SELECT (cert.register_morphism(%s,%s,'scale',%s::jsonb,"
                    "%s::numeric[],%s::numeric[])).id",
                    (c, f"{c}_x2", '{"c": 2}', terms, doubled))
        morph = cur.fetchone()[0]
        chain = _chain(cur, q, c, None, morph, 8, "logc", None, None)
        assert [r[1] for r in chain] == [
            "candidate", "exact_prefix", "morphism", "finite_composition"]
        assert _premises(cur, chain[3][2]) == 2


def test_a_recurrence_and_a_morphism_together_are_refused(conn, seqs):
    q, c, terms = seqs
    with conn.cursor() as cur:
        rec = _recurrence(cur, c, terms)
        cur.execute("SELECT (cert.register_morphism(%s,%s,'scale',%s::jsonb,"
                    "%s::numeric[],%s::numeric[])).id",
                    (c, f"{c}_x2", '{"c": 2}', terms, [t * 2 for t in terms]))
        morph = cur.fetchone()[0]
        with pytest.raises(psycopg.errors.RaiseException, match="not both"):
            _chain(cur, q, c, rec, morph, 8, "logc", None, None)


def test_the_composite_degrades_when_a_premise_is_revoked(conn, seqs):
    """Propagation is 102's, same as T5 -- asserted, not assumed."""
    q, c, terms = seqs
    with conn.cursor() as cur:
        rec = _recurrence(cur, c, terms)
        chain = _chain(cur, q, c, rec, None, 8, "logc", None, None)
        prefix_claim, comp = chain[1][2], chain[3][2]

        cur.execute("INSERT INTO cert.certificate (claim_id, seq, status)"
                    " VALUES (%s,1,'valid'), (%s,1,'valid')", (prefix_claim, chain[2][2]))
        cur.execute("INSERT INTO cert.certificate (claim_id, seq, status)"
                    " VALUES (%s,1,'valid')", (comp,))

        cur.execute("SELECT EXISTS (SELECT 1 FROM cert.tainted_closure"
                    " WHERE claim_id = %s)", (comp,))
        assert cur.fetchone()[0] is False

        cur.execute("SELECT cert.revoke_claim(%s,'withdrawn','{}'::jsonb)", (prefix_claim,))
        cur.execute("SELECT EXISTS (SELECT 1 FROM cert.tainted_closure"
                    " WHERE claim_id = %s)", (comp,))
        assert cur.fetchone()[0] is True


# --- the central rule -------------------------------------------------------

def test_the_global_theorem_has_no_premises(conn, seqs):
    """The rule this whole file is arranged around. If a refactor ever makes the
    theorem derive from the finite chain, the ledger would report a theorem as
    valid because twelve terms lined up."""
    q, c, terms = seqs
    with conn.cursor() as cur:
        rec = _recurrence(cur, c, terms)
        chain = _chain(cur, q, c, rec, None, 8, "logc",
                       f"{q} satisfies the Fibonacci recurrence for all n", "arXiv:0000.00000")
        assert chain[4][1] == "global_theorem"
        assert _premises(cur, chain[4][2]) == 0


def test_the_theorem_is_formal_external_and_stays_unverified(conn, seqs):
    q, c, terms = seqs
    with conn.cursor() as cur:
        rec = _recurrence(cur, c, terms)
        chain = _chain(cur, q, c, rec, None, 8, "logc",
                       f"{q} closed form holds for all n", None)
        cur.execute("SELECT method, probe_sql, claim_kind FROM cert.claim WHERE id = %s",
                    (chain[4][2],))
        method, probe, kind = cur.fetchone()
        assert method == "formal_external"
        assert probe is None          # no in-DB probe for "for all n"
        assert kind == "formal"
        # 'unchecked' is cert.standing's word for "no certificate has ever been
        # recorded", which is where an anchored theorem sits until T1 runs Lean
        # against it. The load-bearing part is that it is not, and cannot be,
        # 'valid' -- no amount of finite agreement moves it.
        cur.execute("SELECT effective_status FROM cert.standing WHERE claim_id = %s",
                    (chain[4][2],))
        row = cur.fetchone()
        assert row is None or row[0] in ("unchecked", "unverified")
        assert row is None or row[0] != "valid"


def test_the_theorem_records_its_finite_evidence_as_motivation(conn, seqs):
    """Recorded and walkable, but in subject_ref -- not as premises."""
    q, c, terms = seqs
    with conn.cursor() as cur:
        rec = _recurrence(cur, c, terms)
        chain = _chain(cur, q, c, rec, None, 8, "logc", f"{q} for all n", None)
        cur.execute("SELECT subject_ref FROM cert.claim WHERE id = %s", (chain[4][2],))
        ref = cur.fetchone()[0]
        assert set(ref["finite_evidence"]) == {chain[1][2], chain[2][2], chain[3][2]}
        assert "does not support" in ref["note"]


def test_the_view_exposes_the_rule(conn, seqs):
    q, c, terms = seqs
    with conn.cursor() as cur:
        rec = _recurrence(cur, c, terms)
        _chain(cur, q, c, rec, None, 8, "logc", f"{q} for all n", None)
        cur.execute("SELECT phase, support_premises FROM calx.oeis_attestation"
                    " WHERE query = %s ORDER BY phase", (q,))
        rows = dict(cur.fetchall())
        assert rows["oeis_theorem"] == 0
        assert rows["oeis_finite_composition"] == 1   # one derivation, two premises
        assert rows["oeis_candidate"] == 0


# --- idempotency ------------------------------------------------------------

def test_rerunning_reuses_claims_and_does_not_stack_derivations(conn, seqs):
    q, c, terms = seqs
    with conn.cursor() as cur:
        rec = _recurrence(cur, c, terms)
        first = _chain(cur, q, c, rec, None, 8, "logc", f"{q} for all n", None)
        second = _chain(cur, q, c, rec, None, 8, "logc", f"{q} for all n", None)
        assert [r[2] for r in first] == [r[2] for r in second]

        cur.execute("SELECT count(*) FROM cert.derivation WHERE conclusion_id = %s",
                    (first[3][2],))
        assert cur.fetchone()[0] == 1
