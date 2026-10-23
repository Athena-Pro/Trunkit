"""Scratch-DB integration for all-symbol counts and weighted-claim honesty."""

from __future__ import annotations

import uuid

import pytest
from psycopg.types.json import Jsonb

from calx.automata_count import count_words


def _weighted(conn, payoff):
    name = "weighted-regression-" + uuid.uuid4().hex
    aid = conn.execute("SELECT nerode.wffa_create(%s,ARRAY['a'])", (name,)).fetchone()[0]
    conn.execute("SELECT nerode.wffa_add_state(%s,0,TRUE,FALSE)", (aid,))
    conn.execute("SELECT nerode.wffa_add_state(%s,1,FALSE,TRUE)", (aid,))
    conn.execute("SELECT nerode.wffa_add_edge(%s,0,'a',1,%s)", (aid, Jsonb(payoff)))
    return aid


def test_sql_counts_all_symbols_and_matches_portable_snapshot(conn):
    aid = conn.execute("SELECT nerode.from_regex('(a|b)*')").fetchone()[0]
    actual = conn.execute("SELECT word_count FROM nerode.count_language(%s,10)", (aid,)).fetchall()
    snapshot = conn.execute("SELECT nerode.dfa_snapshot(%s)", (aid,)).fetchone()[0]
    assert [int(row[0]) for row in actual] == count_words(snapshot, 10) == [2**n for n in range(11)]


def test_sql_counts_empty_word_and_rejects_negative_horizon(conn):
    aid = conn.execute("SELECT nerode.from_regex('a*')").fetchone()[0]
    count = conn.execute("SELECT word_count FROM nerode.count_language(%s,0)", (aid,)).fetchone()[0]
    assert count == 1
    with pytest.raises(Exception, match="invalid length"), conn.transaction():
        conn.execute("SELECT * FROM nerode.count_language(%s,-1)", (aid,))


def test_nested_maximum_is_an_explicit_nondecision(conn):
    expr = {"guard": {"iv": {"lo": 0, "hi": 4}, "then": {"oplus": [
        {"oplus": [{"bind": 1}, {"const": 0}]},
        {"otimes": [{"const": 3}, {"bind": -1}]},
    ]}}}
    result = conn.execute("SELECT nerode.payoff_opt(%s,'worst')", (Jsonb(expr),)).fetchone()[0]
    assert result["supported"] is False and result["val"] is None
    aid = _weighted(conn, expr)
    value, witness = conn.execute("SELECT * FROM nerode.extremal(%s,'worst',1)", (aid,)).fetchone()
    assert value is None and witness["supported"] is False and witness["feasible"] is None


def test_unbounded_payoff_is_flagged_and_affine_guard_remains_exact(conn):
    out = conn.execute("SELECT nerode.payoff_opt('{\"bind\":1}','best')").fetchone()[0]
    assert out["unbounded"] is True and out["supported"] is False
    expr = {"otimes": [{"guard": {"iv": {"lo": 2, "hi": 5}, "then": {"bind": 3}}},
                       {"const": -1}]}
    aid = _weighted(conn, expr)
    value, witness = conn.execute("SELECT * FROM nerode.extremal(%s,'best',1)", (aid,)).fetchone()
    assert value == 14 and witness["history"] == [["a", 5]]


def test_trace_proves_attainment_but_not_optimality_or_infeasibility(conn):
    aid = _weighted(conn, {"const": 3})
    trace = {"history": [["a", 0]], "value": 3, "feasible": True, "horizon": 1}
    assert conn.execute("SELECT ok FROM nerode.replay_payoff_trace(%s,%s)",
                        (aid, Jsonb(trace))).fetchone()[0] is None
    trace["claim_scope"] = "attainment"
    assert conn.execute("SELECT ok FROM nerode.replay_payoff_trace(%s,%s)",
                        (aid, Jsonb(trace))).fetchone()[0] is True
    trace["value"] = 4
    assert conn.execute("SELECT ok FROM nerode.replay_payoff_trace(%s,%s)",
                        (aid, Jsonb(trace))).fetchone()[0] is False
    forged_empty = {"feasible": False, "value": None}
    assert conn.execute("SELECT ok FROM nerode.replay_payoff_trace(%s,%s)",
                        (aid, Jsonb(forged_empty))).fetchone()[0] is None
    trace.update(value=3,history=[["a",-1]])
    assert conn.execute("SELECT ok FROM nerode.replay_payoff_trace(%s,%s)",
                        (aid, Jsonb(trace))).fetchone()[0] is False


def test_certification_marks_extrema_unverified_and_attainment_valid(conn):
    aid = _weighted(conn, {"guard": {"iv": {"lo": 0, "hi": 4}, "then": {"bind": 1}}})
    claim = conn.execute("SELECT nerode.certify_extremal(%s,'best',1)", (aid,)).fetchone()[0]
    status = conn.execute("SELECT status FROM cert.certificate WHERE claim_id=%s",
                          (claim,)).fetchone()[0]
    assert status == "unverified"
    assert conn.execute("SELECT ok FROM nerode.verify(%s)", (claim,)).fetchone()[0] is None
    claim = conn.execute("SELECT nerode.certify_payoff_attainment(%s,%s,1)",
                         (aid, Jsonb([["a", 2]]))).fetchone()[0]
    assert conn.execute("SELECT ok FROM nerode.verify(%s)", (claim,)).fetchone()[0] is True


def test_claim_verifier_binds_automaton_and_attained_value(conn):
    aid = _weighted(conn, {"const": 3})
    other = _weighted(conn, {"const": 5})
    claim = conn.execute("SELECT nerode.certify_payoff_attainment(%s,%s,1)",
                         (aid, Jsonb([["a", 0]]))).fetchone()[0]
    replacement = {"kind": "payoff_trace", "claim_scope": "attainment", "product_id": other,
                   "value": 5, "horizon": 1, "feasible": True, "history": [["a", 0]]}
    conn.execute("UPDATE cert.witness SET body=%s WHERE certificate_id IN "
                 "(SELECT id FROM cert.certificate WHERE claim_id=%s)",
                 (Jsonb(replacement), claim))
    assert conn.execute("SELECT ok FROM nerode.verify(%s)", (claim,)).fetchone()[0] is False
    replacement.update(product_id=aid, value=3, horizon=2)
    conn.execute("UPDATE cert.witness SET body=%s WHERE certificate_id IN "
                 "(SELECT id FROM cert.certificate WHERE claim_id=%s)",
                 (Jsonb(replacement), claim))
    assert conn.execute("SELECT ok FROM nerode.verify(%s)", (claim,)).fetchone()[0] is False


def test_infinite_negative_terminal_weight_is_not_replaced_with_zero(conn):
    aid = _weighted(conn, {"const": 3})
    conn.execute("UPDATE nerode.wffa_terminal SET weight=NULL "
                 "WHERE automaton_id=%s AND role='initial'", (aid,))
    value, witness = conn.execute("SELECT * FROM nerode.extremal(%s,'best',1)", (aid,)).fetchone()
    assert value is None and witness["supported"] is False


def test_sql_language_counts_reject_nondeterministic_dfa(conn):
    aid = conn.execute("SELECT nerode.from_regex('a*')").fetchone()[0]
    source = conn.execute("SELECT state_id FROM nerode.states "
                          "WHERE automaton_id=%s AND is_initial", (aid,)).fetchone()[0]
    conn.execute("INSERT INTO nerode.states(automaton_id,state_id) VALUES (%s,999)", (aid,))
    conn.execute("INSERT INTO nerode.transitions(automaton_id,from_state,symbol,to_state) "
                 "VALUES (%s,%s,'a',999)", (aid, source))
    with pytest.raises(Exception, match="nondeterministic"), conn.transaction():
        conn.execute("SELECT * FROM nerode.count_language(%s,2)", (aid,))
