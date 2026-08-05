"""Tests for the numeric-bound certificate tier (105).

The tier's whole content is a relation. `cert.bound_dominates` is claimed to be
a strict partial order over (value, hypotheses), and frontier, optimality and
enclosure are all derived from it -- so these pin the order's algebra first,
including the incomparability that is the only thing stopping it collapsing
into `<` on NUMERIC, and then the three-valued verdicts built on top.

Every test registers its own quantity, so bounds never collide across tests or
across runs against a ledger that already has rows. Skips cleanly when no test
DSN is set.
"""

from __future__ import annotations

import itertools
import json
import os
import uuid
from decimal import Decimal

import psycopg
import pytest
from psycopg.types.json import Jsonb

from calx import bound as boundlib

TRUE_PROBE = "SELECT TRUE, '{}'::jsonb"


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
            cur.execute("SELECT to_regclass('cert.bound')")
            if cur.fetchone()[0] is None:
                pytest.skip("cert.bound missing — apply 105_cert_bound.sql first")
        yield c
    finally:
        c.close()


def _new_quantity(cur, note="a quantity under test") -> str:
    subject = f"test-quantity-{uuid.uuid4()}"
    cur.execute("SELECT (cert.register_quantity(%s, %s)).id", (subject, note))
    return subject


@pytest.fixture()
def quantity(conn):
    with conn.cursor() as cur:
        return _new_quantity(cur)


def _bound(cur, quantity, direction, value, *, strict=False, hypotheses=(),
           source="", claim_id=None, attained_by=None) -> int:
    cur.execute(
        "SELECT (cert.register_bound(%s, %s, %s, %s, %s, %s, %s, %s)).id",
        (quantity, direction, Decimal(str(value)), strict, list(hypotheses),
         source, claim_id, None if attained_by is None else Jsonb(attained_by)),
    )
    return cur.fetchone()[0]


def _dominates(cur, a, b) -> bool:
    cur.execute("SELECT cert.bound_dominates(%s, %s)", (a, b))
    return cur.fetchone()[0]


def _frontier(cur, quantity):
    cur.execute(
        "SELECT bound_id, value, hypotheses, dominates_n FROM cert.bound_frontier(%s)",
        (quantity,),
    )
    return cur.fetchall()


def _consistent(cur, quantity):
    cur.execute("SELECT ok, evidence FROM cert.bound_consistent(%s)", (quantity,))
    return cur.fetchone()


def _optimal(cur, bound_id):
    cur.execute("SELECT ok, evidence FROM cert.bound_optimal(%s)", (bound_id,))
    return cur.fetchone()


def _standing_claim(cur) -> int:
    """A claim whose truth stands valid — what a bound's claim_id must point at."""
    cur.execute(
        "INSERT INTO cert.claim (subject_kind, subject_ref, statement,"
        " claim_kind, method, probe_sql)"
        " VALUES ('bound_test', '{}'::jsonb, %s, 'computational', 'comp_sql', %s)"
        " RETURNING id",
        (f"bound test truth claim {uuid.uuid4()}", TRUE_PROBE),
    )
    claim_id = cur.fetchone()[0]
    cur.execute("SELECT (cert.check(%s)).id", (claim_id,))
    return claim_id


# --- the partial order ------------------------------------------------------

def test_tighter_upper_bound_dominates(conn, quantity):
    with conn.cursor() as cur:
        tight = _bound(cur, quantity, "upper", 3, source="JLR")
        loose = _bound(cur, quantity, "upper", 4, source="Bogomolov-McQuillan")
        assert _dominates(cur, tight, loose)
        assert not _dominates(cur, loose, tight)


def test_the_tighter_lower_bound_is_the_larger_one(conn, quantity):
    with conn.cursor() as cur:
        tight = _bound(cur, quantity, "lower", 5)
        loose = _bound(cur, quantity, "lower", 4)
        assert _dominates(cur, tight, loose)
        assert not _dominates(cur, loose, tight)


def test_upper_and_lower_bounds_never_compare(conn, quantity):
    with conn.cursor() as cur:
        up = _bound(cur, quantity, "upper", 3)
        lo = _bound(cur, quantity, "lower", 3)
        assert not _dominates(cur, up, lo)
        assert not _dominates(cur, lo, up)


def test_strict_beats_non_strict_at_equal_value(conn, quantity):
    with conn.cursor() as cur:
        strict = _bound(cur, quantity, "upper", 3, strict=True)
        loose = _bound(cur, quantity, "upper", 3)
        assert _dominates(cur, strict, loose)
        assert not _dominates(cur, loose, strict)


def test_a_tighter_conditional_bound_is_incomparable(conn, quantity):
    """The case that must not collapse: sharper, but assuming more."""
    with conn.cursor() as cur:
        sharp = _bound(cur, quantity, "upper", 3, hypotheses=["GRH"])
        blunt = _bound(cur, quantity, "upper", 4)
        assert not _dominates(cur, sharp, blunt)
        assert not _dominates(cur, blunt, sharp)


def test_weaker_hypotheses_dominate_at_equal_value(conn, quantity):
    with conn.cursor() as cur:
        uncond = _bound(cur, quantity, "upper", 4)
        cond = _bound(cur, quantity, "upper", 4, hypotheses=["GRH"])
        assert _dominates(cur, uncond, cond)
        assert not _dominates(cur, cond, uncond)


def test_a_bound_does_not_dominate_itself(conn, quantity):
    with conn.cursor() as cur:
        b = _bound(cur, quantity, "upper", 3)
        assert not _dominates(cur, b, b)


def test_bounds_on_different_quantities_never_compare(conn, quantity):
    with conn.cursor() as cur:
        other = _new_quantity(cur, "an unrelated quantity")
        a = _bound(cur, quantity, "upper", 3)
        b = _bound(cur, other, "upper", 4)
        assert not _dominates(cur, a, b)
        assert not _dominates(cur, b, a)


def test_domination_is_a_strict_partial_order(conn, quantity):
    """Irreflexive, antisymmetric and transitive over a deliberately mixed set."""
    with conn.cursor() as cur:
        ids = [
            _bound(cur, quantity, "upper", 6),
            _bound(cur, quantity, "upper", 4),
            _bound(cur, quantity, "upper", 3),
            _bound(cur, quantity, "upper", 3, strict=True),
            _bound(cur, quantity, "upper", 2, hypotheses=["GRH"]),
            _bound(cur, quantity, "upper", 2, hypotheses=["GRH", "Cramer"]),
            _bound(cur, quantity, "lower", 1),
            _bound(cur, quantity, "lower", 2, hypotheses=["GRH"]),
        ]
        d = {(a, b): _dominates(cur, a, b) for a in ids for b in ids}

    assert not any(d[(a, a)] for a in ids)
    assert not any(d[(a, b)] and d[(b, a)] for a, b in itertools.permutations(ids, 2))
    assert all(
        d[(a, c)]
        for a, b, c in itertools.permutations(ids, 3)
        if d[(a, b)] and d[(b, c)]
    )


# --- the frontier -----------------------------------------------------------

def test_frontier_is_the_undominated_set(conn, quantity):
    """The bend-and-break chain of arXiv:2607.06447 §3.1: 6, then 4, then 3."""
    with conn.cursor() as cur:
        _bound(cur, quantity, "upper", 6, source="Shepherd-Barron")
        _bound(cur, quantity, "upper", 4, source="Bogomolov-McQuillan")
        best = _bound(cur, quantity, "upper", 3, source="JLR")
        rows = _frontier(cur, quantity)
    assert [r[0] for r in rows] == [best]
    assert rows[0][3] == 2  # and it is on record as having beaten both


def test_a_conditional_bound_widens_the_frontier(conn, quantity):
    with conn.cursor() as cur:
        uncond = _bound(cur, quantity, "upper", 3)
        cond = _bound(cur, quantity, "upper", 2, hypotheses=["GRH"])
        rows = _frontier(cur, quantity)
    assert sorted(r[0] for r in rows) == sorted([uncond, cond])


def test_frontier_is_never_empty_while_bounds_exist(conn, quantity):
    with conn.cursor() as cur:
        for value in (6, 5, 4):
            _bound(cur, quantity, "upper", value)
        assert _frontier(cur, quantity)


# --- consistency ------------------------------------------------------------

def test_separated_bounds_are_consistent(conn, quantity):
    with conn.cursor() as cur:
        _bound(cur, quantity, "lower", 2)
        _bound(cur, quantity, "upper", 5)
        ok, ev = _consistent(cur, quantity)
    assert ok is True
    assert ev["bounds"] == 2


def test_lower_above_upper_is_refuted_and_names_both(conn, quantity):
    with conn.cursor() as cur:
        lo = _bound(cur, quantity, "lower", 5, source="a bad transcription")
        up = _bound(cur, quantity, "upper", 3, source="the literature")
        ok, ev = _consistent(cur, quantity)
    assert ok is False
    conflict = ev["conflicts"][0]
    assert (conflict["lower_bound_id"], conflict["upper_bound_id"]) == (lo, up)


def test_meeting_endpoints_are_consistent_when_neither_is_strict(conn, quantity):
    """3 <= q <= 3 pins q; it is not a collision."""
    with conn.cursor() as cur:
        _bound(cur, quantity, "lower", 3)
        _bound(cur, quantity, "upper", 3)
        assert _consistent(cur, quantity)[0] is True


def test_meeting_endpoints_collide_when_one_side_is_strict(conn, quantity):
    with conn.cursor() as cur:
        _bound(cur, quantity, "lower", 3)
        _bound(cur, quantity, "upper", 3, strict=True)
        assert _consistent(cur, quantity)[0] is False


def test_conflict_evidence_carries_the_union_of_hypotheses(conn, quantity):
    with conn.cursor() as cur:
        _bound(cur, quantity, "lower", 5, hypotheses=["GRH"])
        _bound(cur, quantity, "upper", 3, hypotheses=["Cramer"])
        ok, ev = _consistent(cur, quantity)
    assert ok is False
    assert ev["conflicts"][0]["under_hypotheses"] == ["Cramer", "GRH"]


def test_an_unregistered_quantity_is_not_consistent(conn):
    with conn.cursor() as cur:
        ok, ev = _consistent(cur, f"never-registered-{uuid.uuid4()}")
    assert ok is False
    assert ev["reason"] == "no such quantity"


# --- enclosure --------------------------------------------------------------

def test_enclosure_reports_the_tightest_pair_and_an_exact_width(conn, quantity):
    with conn.cursor() as cur:
        _bound(cur, quantity, "lower", "0.05")
        _bound(cur, quantity, "lower", "0.1")
        _bound(cur, quantity, "upper", "0.3")
        _bound(cur, quantity, "upper", "0.9")
        cur.execute(
            "SELECT lower_value, upper_value, width FROM cert.enclosure(%s)", (quantity,)
        )
        lower, upper, width = cur.fetchone()
    assert (lower, upper) == (Decimal("0.1"), Decimal("0.3"))
    # NUMERIC, not float8 — in binary floating point this width comes out
    # 0.19999999999999998, i.e. an enclosure reported tighter than it is.
    assert width == Decimal("0.2")


def test_enclosure_excludes_bounds_outside_the_hypothesis_budget(conn, quantity):
    with conn.cursor() as cur:
        _bound(cur, quantity, "lower", 1)
        _bound(cur, quantity, "upper", 5)
        _bound(cur, quantity, "upper", 2, hypotheses=["GRH"])
        cur.execute("SELECT upper_value FROM cert.enclosure(%s)", (quantity,))
        assert cur.fetchone()[0] == Decimal("5")
        cur.execute("SELECT upper_value FROM cert.enclosure(%s, %s)", (quantity, ["GRH"]))
        assert cur.fetchone()[0] == Decimal("2")


def test_a_one_sided_enclosure_has_no_width(conn, quantity):
    with conn.cursor() as cur:
        _bound(cur, quantity, "upper", 5)
        cur.execute(
            "SELECT lower_value, upper_value, width FROM cert.enclosure(%s)", (quantity,)
        )
        lower, upper, width = cur.fetchone()
    assert (lower, upper, width) == (None, Decimal("5"), None)


def test_enclosure_of_an_unbounded_quantity_is_empty(conn, quantity):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM cert.enclosure(%s)", (quantity,))
        assert cur.fetchall() == []


# --- optimality: the three-valued half --------------------------------------

def test_a_dominated_bound_is_not_optimal(conn, quantity):
    with conn.cursor() as cur:
        loose = _bound(cur, quantity, "upper", 4)
        _bound(cur, quantity, "upper", 3)
        ok, ev = _optimal(cur, loose)
    assert ok is False
    assert ev["dominated_by"][0]["value"] == 3


def test_undominated_without_a_witness_is_unverified_not_valid(conn, quantity):
    """"Nothing tighter is on record" is a fact about the ledger, not about
    mathematics — so it must never read as valid."""
    with conn.cursor() as cur:
        best = _bound(cur, quantity, "upper", 3)
        ok, ev = _optimal(cur, best)
    assert ok is None
    assert ev["status"] == "best known on record"


def test_attained_but_uncertified_is_still_unverified(conn, quantity):
    with conn.cursor() as cur:
        best = _bound(cur, quantity, "upper", 3, attained_by={"construction": "r+1"})
        ok, ev = _optimal(cur, best)
    assert ok is None
    assert "truth is not certified" in ev["reason"]


def test_optimal_when_undominated_attained_and_its_truth_stands(conn, quantity):
    with conn.cursor() as cur:
        truth = _standing_claim(cur)
        best = _bound(cur, quantity, "upper", 3, claim_id=truth,
                      attained_by={"construction": "r+1"})
        ok, ev = _optimal(cur, best)
    assert ok is True
    assert ev["truth_claim_status"] == "valid"


def test_revoking_the_truth_claim_pulls_optimality_back_to_unverified(conn, quantity):
    """Optimality reads cert.standing, so a revocation upstream lands here free."""
    with conn.cursor() as cur:
        truth = _standing_claim(cur)
        best = _bound(cur, quantity, "upper", 3, claim_id=truth,
                      attained_by={"construction": "r+1"})
        assert _optimal(cur, best)[0] is True
        cur.execute("SELECT (cert.revoke_claim(%s, %s)).id", (truth, "test revocation"))
        ok, ev = _optimal(cur, best)
    assert ok is None
    assert ev["truth_claim_status"] == "revoked"


def test_optimality_of_an_unknown_bound_is_refused(conn):
    with conn.cursor() as cur:
        ok, ev = _optimal(cur, -1)
    assert ok is False
    assert ev["reason"] == "no such bound"


# --- the claims these mint --------------------------------------------------

def test_consistency_claim_flips_when_a_colliding_bound_lands(conn, quantity):
    """Why this is a claim and not a one-off report."""
    with conn.cursor() as cur:
        _bound(cur, quantity, "lower", 2)
        _bound(cur, quantity, "upper", 5)
        cur.execute("SELECT cert.bound_consistency_claim(%s)", (quantity,))
        claim = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (claim,))
        assert cur.fetchone()[0] == "valid"

        _bound(cur, quantity, "lower", 9, source="a later, wrong transcription")
        cur.execute("SELECT (cert.check(%s)).status", (claim,))
        assert cur.fetchone()[0] == "refuted"


def test_domination_claim_is_recheckable(conn, quantity):
    with conn.cursor() as cur:
        tight = _bound(cur, quantity, "upper", 3, source="JLR")
        loose = _bound(cur, quantity, "upper", 4, source="Bogomolov-McQuillan")
        cur.execute("SELECT cert.bound_domination_claim(%s, %s)", (tight, loose))
        claim = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (claim,))
        assert cur.fetchone()[0] == "valid"


def test_optimality_claim_reads_unverified_for_a_merely_best_known_bound(conn, quantity):
    with conn.cursor() as cur:
        best = _bound(cur, quantity, "upper", 3)
        cur.execute("SELECT cert.bound_optimality_claim(%s)", (best,))
        claim = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (claim,))
        assert cur.fetchone()[0] == "unverified"


# --- registration -----------------------------------------------------------

def test_hypotheses_are_canonicalised_so_one_bound_registers_once(conn, quantity):
    with conn.cursor() as cur:
        first = _bound(cur, quantity, "upper", 3, hypotheses=["GRH", "Cramer", "GRH"])
        again = _bound(cur, quantity, "upper", 3, hypotheses=["Cramer", "GRH"])
        cur.execute("SELECT hypotheses FROM cert.bound WHERE id = %s", (first,))
        assert cur.fetchone()[0] == ["Cramer", "GRH"]
    assert again == first


def test_re_registering_fills_attribution_in_without_clearing_it(conn, quantity):
    with conn.cursor() as cur:
        truth = _standing_claim(cur)
        b = _bound(cur, quantity, "upper", 3, source="JLR")
        assert _bound(cur, quantity, "upper", 3, source="JLR", claim_id=truth) == b
        # A later bare re-registration must not drop what is already known.
        _bound(cur, quantity, "upper", 3, source="JLR")
        cur.execute("SELECT claim_id FROM cert.bound WHERE id = %s", (b,))
        assert cur.fetchone()[0] == truth


def test_a_bound_needs_a_registered_quantity(conn):
    with conn.cursor() as cur, pytest.raises(psycopg.Error):
        _bound(cur, f"never-registered-{uuid.uuid4()}", "upper", 3)


def test_registering_a_quantity_is_idempotent(conn):
    with conn.cursor() as cur:
        subject = _new_quantity(cur, "first description")
        cur.execute(
            "SELECT (cert.register_quantity(%s, %s)).id", (subject, "revised description")
        )
        again = cur.fetchone()[0]
        cur.execute("SELECT id, description FROM cert.quantity WHERE subject_id = %s",
                    (subject,))
        row = cur.fetchone()
    assert row[0] == again
    assert row[1] == "revised description"


# ═══════════════════════════════════════════════════════════════════════════
# calx.bound — the DB-free mirror a consumer verifies with
# ═══════════════════════════════════════════════════════════════════════════

def _py(id, direction, value, **kw):
    return boundlib.Bound(id=id, direction=direction, value=Decimal(str(value)),
                          hypotheses=frozenset(kw.pop("hypotheses", ())), **kw)


# --- decoding ---------------------------------------------------------------

def test_parse_rejects_shapes_it_cannot_report_on():
    with pytest.raises(ValueError):
        boundlib.parse({"id": 1})                              # not a list
    with pytest.raises(ValueError):
        boundlib.parse([{"id": 1, "direction": "tighter", "value": 1}])
    with pytest.raises(ValueError):
        boundlib.parse([{"direction": "upper", "value": 1}])   # no id
    with pytest.raises(ValueError):
        boundlib.parse([{"id": 1, "direction": "upper", "value": "x"}])
    with pytest.raises(ValueError):
        boundlib.parse([{"id": 1, "direction": "upper", "value": 1},
                        {"id": 1, "direction": "upper", "value": 2}])


def test_decoding_with_parse_float_keeps_the_value_exact():
    """The trap this tier exists to avoid: a bound tighter than it is."""
    text = '[{"id": 1, "direction": "upper", "value": 0.1},' \
           ' {"id": 2, "direction": "lower", "value": 0.3}]'
    exact = boundlib.parse(json.loads(text, parse_float=Decimal))
    assert [b.value for b in exact] == [Decimal("0.1"), Decimal("0.3")]
    # The same literal decoded as a float does not survive the round trip,
    # which is why the tool decodes with parse_float=Decimal.
    assert Decimal(json.loads(text)[1]["value"]) != Decimal("0.3")


def test_hypotheses_are_a_set_regardless_of_order_or_repeats():
    a = boundlib.from_json({"id": 1, "direction": "upper", "value": "3",
                            "hypotheses": ["GRH", "Cramer", "GRH"]})
    b = boundlib.from_json({"id": 2, "direction": "upper", "value": "3",
                            "hypotheses": ["Cramer", "GRH"]})
    assert a.hypotheses == b.hypotheses


# --- the order --------------------------------------------------------------

def test_mirror_order_matches_the_documented_cases():
    assert boundlib.dominates(_py(1, "upper", 3), _py(2, "upper", 4))
    assert not boundlib.dominates(_py(1, "upper", 4), _py(2, "upper", 3))
    assert boundlib.dominates(_py(1, "lower", 5), _py(2, "lower", 4))
    assert boundlib.dominates(_py(1, "upper", 3, is_strict=True), _py(2, "upper", 3))
    assert not boundlib.dominates(_py(1, "upper", 3), _py(2, "lower", 3))
    assert not boundlib.dominates(_py(1, "upper", 3), _py(1, "upper", 4))  # same id


def test_mirror_keeps_a_tighter_conditional_bound_incomparable():
    sharp = _py(1, "upper", 3, hypotheses=["GRH"])
    blunt = _py(2, "upper", 4)
    assert not boundlib.dominates(sharp, blunt)
    assert not boundlib.dominates(blunt, sharp)


def test_mirror_frontier_counts_what_each_survivor_beat():
    bounds = [_py(1, "upper", 6), _py(2, "upper", 4), _py(3, "upper", 3)]
    rows = boundlib.frontier(bounds)
    assert [r["bound_id"] for r in rows] == [3]
    assert rows[0]["dominates_n"] == 2


# --- consistency and enclosure ---------------------------------------------

def test_mirror_reports_the_colliding_pair_not_a_bare_false():
    ok, ev = boundlib.consistent([_py(1, "lower", 5, source="typo"),
                                  _py(2, "upper", 3, source="literature")])
    assert ok is False
    assert (ev["conflicts"][0]["lower_bound_id"],
            ev["conflicts"][0]["upper_bound_id"]) == (1, 2)


def test_mirror_treats_meeting_endpoints_as_a_pinned_value():
    assert boundlib.consistent([_py(1, "lower", 3), _py(2, "upper", 3)])[0] is True
    assert boundlib.consistent(
        [_py(1, "lower", 3), _py(2, "upper", 3, is_strict=True)])[0] is False


def test_mirror_enclosure_width_is_exact():
    enc = boundlib.enclosure([_py(1, "lower", "0.1"), _py(2, "upper", "0.3")])
    assert enc["width"] == "0.2"


def test_mirror_enclosure_respects_the_hypothesis_budget():
    bounds = [_py(1, "lower", 1), _py(2, "upper", 5),
              _py(3, "upper", 2, hypotheses=["GRH"])]
    assert boundlib.enclosure(bounds)["upper_value"] == "5"
    assert boundlib.enclosure(bounds, ["GRH"])["upper_value"] == "2"


def test_mirror_enclosure_is_none_when_nothing_is_bounded():
    assert boundlib.enclosure([_py(1, "upper", 5, hypotheses=["GRH"])]) is None
    one_sided = boundlib.enclosure([_py(1, "upper", 5)])
    assert one_sided["width"] is None and one_sided["lower_value"] is None


# --- optimality: what a consumer may and may not settle ---------------------

def test_mirror_refutes_optimality_of_a_dominated_bound():
    ok, ev = boundlib.optimal(2, [_py(1, "upper", 3), _py(2, "upper", 4)])
    assert ok is False
    assert ev["dominated_by"][0]["bound_id"] == 1


def test_mirror_will_not_call_a_bound_optimal_without_carried_standing():
    """A consumer with no DB cannot confirm the truth-claim still stands."""
    best = _py(1, "upper", 3, claim_id=7, attained_by={"construction": "r+1"})
    ok, ev = boundlib.optimal(1, [best])
    assert ok is None
    assert ev["truth_claim_status"] == "uncarried"


def test_mirror_calls_a_bound_optimal_once_standing_is_carried():
    best = _py(1, "upper", 3, claim_id=7, attained_by={"construction": "r+1"},
               claim_standing="valid")
    assert boundlib.optimal(1, [best])[0] is True


def test_mirror_declines_a_revoked_truth_claim():
    best = _py(1, "upper", 3, claim_id=7, attained_by={"construction": "r+1"},
               claim_standing="revoked")
    ok, ev = boundlib.optimal(1, [best])
    assert ok is None
    assert ev["truth_claim_status"] == "revoked"


def test_mirror_optimality_of_an_unknown_bound_is_refused():
    assert boundlib.optimal(99, [_py(1, "upper", 3)])[0] is False


# --- the mirror must agree with the SQL -------------------------------------

def _mirror_rows(cur, quantity):
    cur.execute(
        "SELECT b.id, b.direction, b.value, b.is_strict, b.hypotheses, b.source,"
        "       b.claim_id, b.attained_by"
        "  FROM cert.bound b JOIN cert.quantity q ON q.id = b.quantity_id"
        " WHERE q.subject_id = %s ORDER BY b.id",
        (quantity,),
    )
    return [
        boundlib.Bound(id=r[0], direction=r[1], value=r[2], is_strict=r[3],
                       hypotheses=frozenset(r[4]), source=r[5],
                       claim_id=r[6], attained_by=r[7])
        for r in cur.fetchall()
    ]


def test_python_order_matches_sql_on_a_mixed_population(conn, quantity):
    """The guarantee that makes the mirror usable: 105 and calx.bound are one
    definition of the order, stated twice."""
    with conn.cursor() as cur:
        ids = [
            _bound(cur, quantity, "upper", 6),
            _bound(cur, quantity, "upper", 4),
            _bound(cur, quantity, "upper", 3),
            _bound(cur, quantity, "upper", 3, strict=True),
            _bound(cur, quantity, "upper", "2.5", hypotheses=["GRH"]),
            _bound(cur, quantity, "upper", "2.5", hypotheses=["GRH", "Cramer"]),
            _bound(cur, quantity, "lower", 1),
            _bound(cur, quantity, "lower", "1.5", hypotheses=["GRH"]),
        ]
        rows = {b.id: b for b in _mirror_rows(cur, quantity)}
        disagreements = []
        for a in ids:
            for b in ids:
                cur.execute("SELECT cert.bound_dominates(%s, %s)", (a, b))
                if cur.fetchone()[0] != boundlib.dominates(rows[a], rows[b]):
                    disagreements.append((a, b))
    assert not disagreements


def test_python_frontier_and_consistency_match_sql(conn, quantity):
    with conn.cursor() as cur:
        _bound(cur, quantity, "upper", 6)
        _bound(cur, quantity, "upper", 3)
        _bound(cur, quantity, "upper", "2.5", hypotheses=["GRH"])
        _bound(cur, quantity, "lower", 1)
        bounds = _mirror_rows(cur, quantity)

        sql_frontier = [r[0] for r in _frontier(cur, quantity)]
        assert [r["bound_id"] for r in boundlib.frontier(bounds)] == sql_frontier

        sql_ok, _ = _consistent(cur, quantity)
        assert boundlib.consistent(bounds)[0] == sql_ok

        cur.execute(
            "SELECT lower_value, upper_value, width FROM cert.enclosure(%s)", (quantity,)
        )
        lower, upper, width = cur.fetchone()
        enc = boundlib.enclosure(bounds)
        assert Decimal(enc["lower_value"]) == lower
        assert Decimal(enc["upper_value"]) == upper
        assert Decimal(enc["width"]) == width


def test_python_consistency_matches_sql_on_a_colliding_set(conn, quantity):
    with conn.cursor() as cur:
        _bound(cur, quantity, "lower", 5)
        _bound(cur, quantity, "upper", 3)
        bounds = _mirror_rows(cur, quantity)
        sql_ok, sql_ev = _consistent(cur, quantity)
    py_ok, py_ev = boundlib.consistent(bounds)
    assert py_ok == sql_ok is False
    assert py_ev["n"] == sql_ev["n"]
    assert ({(c["lower_bound_id"], c["upper_bound_id"]) for c in py_ev["conflicts"]}
            == {(c["lower_bound_id"], c["upper_bound_id"]) for c in sql_ev["conflicts"]})
