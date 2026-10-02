"""Tests for the solution-provenance DAG (114).

Two things are pinned.

That propagation is INHERITED, not reimplemented. A support edge mirrors into
cert.derivation, so revoking an upstream claim taints everything downstream
through cert.tainted_closure (102) without this layer walking anything. The
revocation test therefore asserts on 102's output, which is the whole point:
if prov had its own traversal, that assertion would pass while the two
implementations quietly disagreed.

And that `corrected` does NOT carry support. A human step that repairs a flawed
AI output must not be tainted by revoking the thing it repairs -- otherwise
recording that something was wrong also destroys belief in the fix. This is the
one place the provenance graph and the proof graph deliberately disagree, so it
gets the most tests.

The end-to-end case is Erdos #728, the canonical shape from
docs/reports/Trunkit_Erdos_AI_Capability_Fit.md: prior literature, an AI
output, a Lean formalization of it, and a human verification step -- four
contributors of three kinds, all needing attribution. Names and locators are
illustrative scaffolding for the graph shape; the only external anchor is the
arXiv id the report itself cites.

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
            cur.execute("SELECT to_regprocedure('prov.link(bigint,bigint,text,text)')")
            if cur.fetchone()[0] is None:
                pytest.skip("prov.link missing — apply 114_prov_provenance.sql")
        yield c
    finally:
        c.close()


# --- helpers ----------------------------------------------------------------

def _claim(cur, statement: str) -> int:
    """A trivially-true comp_sql claim, certified valid, to hang an artifact on."""
    cur.execute(
        "INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind,"
        " method, probe_sql) VALUES ('prov_test', '{}'::jsonb, %s, 'computational',"
        " 'comp_sql', %s) RETURNING id",
        (statement, "SELECT true AS ok, '{}'::jsonb AS evidence"),
    )
    claim_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cert.certificate (claim_id, seq, status) VALUES (%s, 1, 'valid')",
        (claim_id,),
    )
    return claim_id


def _contrib(cur, name, kind, tier="unrated", ident=None) -> int:
    cur.execute("SELECT prov.register_contributor(%s, %s, %s, %s)",
                (name, kind, tier, ident))
    return cur.fetchone()[0]


def _artifact(cur, problem, kind, contributor, title,
              locator=None, claim_id=None, grade="ungraded") -> int:
    cur.execute("SELECT prov.register_artifact(%s, %s, %s, %s, %s, %s, %s)",
                (problem, kind, contributor, title, locator, claim_id, grade))
    return cur.fetchone()[0]


def _status(cur, artifact_id) -> str:
    cur.execute("SELECT effective_status FROM prov.standing WHERE artifact_id = %s",
                (artifact_id,))
    return cur.fetchone()[0]


def _tainted(cur, claim_id) -> bool:
    cur.execute("SELECT EXISTS (SELECT 1 FROM cert.tainted_closure WHERE claim_id = %s)",
                (claim_id,))
    return cur.fetchone()[0]


@pytest.fixture()
def problem():
    return f"erdos_728_test_{uuid.uuid4().hex[:8]}"


# --- the store --------------------------------------------------------------

def test_an_artifact_needs_a_registered_contributor(conn, problem):
    with conn.cursor() as cur, \
         pytest.raises(psycopg.errors.RaiseException, match="unknown contributor"):
        _artifact(cur, problem, "ai_output", "nobody-registered", "x")


def test_registration_is_idempotent(conn, problem):
    with conn.cursor() as cur:
        _contrib(cur, f"c-{problem}", "ai_system")
        a = _artifact(cur, problem, "ai_output", f"c-{problem}", "same title")
        b = _artifact(cur, problem, "ai_output", f"c-{problem}", "same title")
        assert a == b


def test_an_artifact_with_no_claim_is_unverified_however_green_its_grade(conn, problem):
    """The laundering guard: an editorial 'full' must never read as a verdict."""
    with conn.cursor() as cur:
        _contrib(cur, f"lit-{problem}", "literature", "peer_reviewed")
        art = _artifact(cur, problem, "prior_literature", f"lit-{problem}",
                        "a 1971 paper with no in-DB probe", grade="full")
        cur.execute("SELECT grade, effective_status FROM prov.standing"
                    " WHERE artifact_id = %s", (art,))
        grade, status = cur.fetchone()
        assert grade == "full"
        assert status == "unverified"


def test_trust_tier_never_moves_a_verdict(conn, problem):
    """Two artifacts on identical claims, opposite tiers, identical standing."""
    with conn.cursor() as cur:
        _contrib(cur, f"hi-{problem}", "tool", "machine_checked")
        _contrib(cur, f"lo-{problem}", "human", "self_reported")
        hi = _artifact(cur, problem, "lean_artifact", f"hi-{problem}", "hi",
                       claim_id=_claim(cur, f"hi claim {problem}"))
        lo = _artifact(cur, problem, "human_step", f"lo-{problem}", "lo",
                       claim_id=_claim(cur, f"lo claim {problem}"))
        assert _status(cur, hi) == _status(cur, lo) == "valid"


# --- the mirror into cert.derivation ----------------------------------------

@pytest.mark.parametrize("relation", ["used", "formalized", "prior_literature"])
def test_a_support_edge_mirrors_into_the_proof_dag(conn, problem, relation):
    with conn.cursor() as cur:
        _contrib(cur, f"s-{problem}", "ai_system")
        src_c, dst_c = _claim(cur, f"src {problem}"), _claim(cur, f"dst {problem}")
        src = _artifact(cur, problem, "ai_output", f"s-{problem}", "src", claim_id=src_c)
        dst = _artifact(cur, problem, "lean_artifact", f"s-{problem}", "dst", claim_id=dst_c)

        cur.execute("SELECT prov.link(%s, %s, %s, NULL)", (src, dst, relation))
        edge = cur.fetchone()[0]

        cur.execute("SELECT derivation_id FROM prov.edge WHERE id = %s", (edge,))
        deriv = cur.fetchone()[0]
        assert deriv is not None, f"{relation} should carry support"

        # Direction matters: dst holds BECAUSE src holds.
        cur.execute("SELECT conclusion_id, premise_ids, rule FROM cert.derivation"
                    " WHERE id = %s", (deriv,))
        conclusion, premises, rule = cur.fetchone()
        assert conclusion == dst_c
        assert premises == [src_c]
        assert rule == f"prov_{relation}"


def test_linking_is_idempotent_and_does_not_stack_derivations(conn, problem):
    with conn.cursor() as cur:
        _contrib(cur, f"i-{problem}", "ai_system")
        src = _artifact(cur, problem, "ai_output", f"i-{problem}", "src",
                        claim_id=_claim(cur, f"src {problem}"))
        dst = _artifact(cur, problem, "lean_artifact", f"i-{problem}", "dst",
                        claim_id=_claim(cur, f"dst {problem}"))
        cur.execute("SELECT prov.link(%s, %s, 'formalized', NULL)", (src, dst))
        first = cur.fetchone()[0]
        cur.execute("SELECT prov.link(%s, %s, 'formalized', NULL)", (src, dst))
        assert cur.fetchone()[0] == first

        cur.execute("SELECT count(*) FROM cert.derivation WHERE rule = 'prov_formalized'"
                    " AND conclusion_id = (SELECT claim_id FROM prov.artifact WHERE id = %s)",
                    (dst,))
        assert cur.fetchone()[0] == 1


def test_a_support_edge_without_claims_is_recorded_but_transmits_nothing(conn, problem):
    """Most prior literature has no probe. The honest outcome is a visible edge
    with no derivation, not a fabricated claim to hang it on."""
    with conn.cursor() as cur:
        _contrib(cur, f"n-{problem}", "literature")
        src = _artifact(cur, problem, "prior_literature", f"n-{problem}", "unprobed paper")
        dst = _artifact(cur, problem, "ai_output", f"n-{problem}", "built on it")
        cur.execute("SELECT prov.link(%s, %s, 'used', NULL)", (src, dst))
        edge = cur.fetchone()[0]
        cur.execute("SELECT derivation_id FROM prov.edge WHERE id = %s", (edge,))
        assert cur.fetchone()[0] is None


def test_an_artifact_cannot_support_itself(conn, problem):
    with conn.cursor() as cur:
        _contrib(cur, f"self-{problem}", "human")
        a = _artifact(cur, problem, "human_step", f"self-{problem}", "a")
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute("SELECT prov.link(%s, %s, 'used', NULL)", (a, a))


# --- revocation propagation -------------------------------------------------

def test_revoking_an_upstream_claim_taints_everything_downstream(conn, problem):
    """The T5 headline, asserted on 102's closure rather than on anything here."""
    with conn.cursor() as cur:
        _contrib(cur, f"r-{problem}", "ai_system")
        lit_c = _claim(cur, f"lit {problem}")
        ai_c = _claim(cur, f"ai {problem}")
        lean_c = _claim(cur, f"lean {problem}")

        lit = _artifact(cur, problem, "prior_literature", f"r-{problem}", "lit",
                        claim_id=lit_c)
        ai = _artifact(cur, problem, "ai_output", f"r-{problem}", "ai", claim_id=ai_c)
        lean = _artifact(cur, problem, "lean_artifact", f"r-{problem}", "lean",
                         claim_id=lean_c)

        cur.execute("SELECT prov.link(%s, %s, 'used', NULL)", (lit, ai))
        cur.execute("SELECT prov.link(%s, %s, 'formalized', NULL)", (ai, lean))

        assert _status(cur, lit) == _status(cur, ai) == _status(cur, lean) == "valid"
        assert not _tainted(cur, lean_c)

        # Revoke the root. Two hops away, the Lean artifact must feel it.
        cur.execute("SELECT cert.revoke_claim(%s, 'withdrawn by author', '{}'::jsonb)",
                    (lit_c,))

        assert _status(cur, lit) == "revoked"
        assert _tainted(cur, ai_c), "direct dependent not tainted"
        assert _tainted(cur, lean_c), "transitive dependent not tainted — 102 not reached"

        cur.execute("SELECT taint_status, taint_depth FROM prov.standing"
                    " WHERE artifact_id = %s", (lean,))
        taint_status, depth = cur.fetchone()
        assert taint_status == "revoked"
        assert depth == 2, "should be two hops from the revoked root"


def test_taint_is_not_refutation(conn, problem):
    """A tainted artifact's own claim keeps its own verdict. Loss of upstream
    standing and refutation of this artifact are different facts."""
    with conn.cursor() as cur:
        _contrib(cur, f"t-{problem}", "ai_system")
        src_c, dst_c = _claim(cur, f"src {problem}"), _claim(cur, f"dst {problem}")
        src = _artifact(cur, problem, "ai_output", f"t-{problem}", "src", claim_id=src_c)
        dst = _artifact(cur, problem, "lean_artifact", f"t-{problem}", "dst",
                        claim_id=dst_c)
        cur.execute("SELECT prov.link(%s, %s, 'formalized', NULL)", (src, dst))
        cur.execute("SELECT cert.revoke_claim(%s, 'r', '{}'::jsonb)", (src_c,))

        assert _tainted(cur, dst_c)
        # Still 'valid', with the taint reported alongside rather than folded in.
        cur.execute("SELECT effective_status, taint_status FROM prov.standing"
                    " WHERE artifact_id = %s", (dst,))
        status, taint = cur.fetchone()
        assert status == "valid"
        assert taint == "revoked"


# --- the design decision ----------------------------------------------------

def test_corrected_is_not_a_support_relation(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT prov.is_support_relation('used'),"
                    " prov.is_support_relation('formalized'),"
                    " prov.is_support_relation('prior_literature'),"
                    " prov.is_support_relation('corrected')")
        used, formalized, prior, corrected = cur.fetchone()
        assert used and formalized and prior
        assert not corrected


def test_a_correction_records_no_derivation_edge(conn, problem):
    with conn.cursor() as cur:
        _contrib(cur, f"c1-{problem}", "ai_system")
        _contrib(cur, f"c2-{problem}", "human")
        flawed = _artifact(cur, problem, "ai_output", f"c1-{problem}", "flawed",
                           claim_id=_claim(cur, f"flawed {problem}"), grade="incorrect")
        fix = _artifact(cur, problem, "human_step", f"c2-{problem}", "fix",
                        claim_id=_claim(cur, f"fix {problem}"), grade="full")
        cur.execute("SELECT prov.link(%s, %s, 'corrected', 'sign error in step 3')",
                    (flawed, fix))
        edge = cur.fetchone()[0]
        cur.execute("SELECT derivation_id, note FROM prov.edge WHERE id = %s", (edge,))
        deriv, note = cur.fetchone()
        assert deriv is None
        assert note == "sign error in step 3"   # recorded as audit history, not support


def test_revoking_a_flawed_artifact_does_not_taint_its_correction(conn, problem):
    """The failure this design exists to prevent: recording that something was
    wrong must not destroy belief in the thing that made it right."""
    with conn.cursor() as cur:
        _contrib(cur, f"f1-{problem}", "ai_system")
        _contrib(cur, f"f2-{problem}", "human")
        flawed_c = _claim(cur, f"flawed {problem}")
        fix_c = _claim(cur, f"fix {problem}")
        flawed = _artifact(cur, problem, "ai_output", f"f1-{problem}", "flawed",
                           claim_id=flawed_c, grade="incorrect")
        fix = _artifact(cur, problem, "human_step", f"f2-{problem}", "fix",
                        claim_id=fix_c, grade="full")
        cur.execute("SELECT prov.link(%s, %s, 'corrected', NULL)", (flawed, fix))

        cur.execute("SELECT cert.revoke_claim(%s, 'defective', '{}'::jsonb)", (flawed_c,))

        assert _status(cur, flawed) == "revoked"
        assert not _tainted(cur, fix_c), "the correction inherited the defect it repairs"
        assert _status(cur, fix) == "valid"


# --- Erdos #728, end to end -------------------------------------------------

def test_the_canonical_four_contributor_shape(conn, problem):
    """Prior literature, an AI output, a Lean formalization, a human check.

    Three contributor kinds, four artifacts, and a credit table derived rather
    than hand-maintained -- the wiki bookkeeping the tracker does by hand.
    """
    with conn.cursor() as cur:
        _contrib(cur, f"lit-{problem}", "literature", "peer_reviewed", "arXiv:2601.07421")
        _contrib(cur, f"ai-{problem}", "ai_system", "self_reported")
        _contrib(cur, f"lean-{problem}", "tool", "machine_checked", "leanprover/lean4:v4.27")
        _contrib(cur, f"human-{problem}", "human", "community")

        lit_c = _claim(cur, f"728 lit {problem}")
        ai_c = _claim(cur, f"728 ai {problem}")
        lean_c = _claim(cur, f"728 lean {problem}")
        chk_c = _claim(cur, f"728 human {problem}")

        lit = _artifact(cur, problem, "prior_literature", f"lit-{problem}",
                        "prior bound in the literature",
                        locator="arXiv:2601.07421", claim_id=lit_c, grade="full")
        ai = _artifact(cur, problem, "ai_output", f"ai-{problem}",
                       "AI-produced proof", claim_id=ai_c, grade="full")
        lean = _artifact(cur, problem, "lean_artifact", f"lean-{problem}",
                         "Lean formalization", claim_id=lean_c, grade="full")
        chk = _artifact(cur, problem, "human_step", f"human-{problem}",
                        "human verification of the writeup", claim_id=chk_c,
                        grade="full")

        cur.execute("SELECT prov.link(%s, %s, 'prior_literature', NULL)", (lit, ai))
        cur.execute("SELECT prov.link(%s, %s, 'formalized', NULL)", (ai, lean))
        cur.execute("SELECT prov.link(%s, %s, 'used', NULL)", (lean, chk))

        cur.execute("SELECT count(*) FROM prov.standing WHERE problem = %s"
                    " AND effective_status = 'valid'", (problem,))
        assert cur.fetchone()[0] == 4

        cur.execute("SELECT contributor_kind, count(*) FROM prov.standing"
                    " WHERE problem = %s GROUP BY 1 ORDER BY 1", (problem,))
        assert dict(cur.fetchall()) == {
            "ai_system": 1, "human": 1, "literature": 1, "tool": 1}

        cur.execute("SELECT contributor, artifacts, standing FROM prov.credit(%s)"
                    " ORDER BY contributor", (problem,))
        credit = cur.fetchall()
        assert len(credit) == 4
        assert all(row[1] == 1 and row[2] == {"valid": 1} for row in credit)

        # Withdrawing the literature at the root reaches the human check three
        # hops downstream -- the whole chain, through one revoke.
        cur.execute("SELECT cert.revoke_claim(%s, 'prior art withdrawn', '{}'::jsonb)",
                    (lit_c,))
        for claim_id, label in ((ai_c, "ai"), (lean_c, "lean"), (chk_c, "human check")):
            assert _tainted(cur, claim_id), f"{label} not reached by propagation"

        cur.execute("SELECT taint_depth FROM prov.standing WHERE artifact_id = %s", (chk,))
        assert cur.fetchone()[0] == 3
