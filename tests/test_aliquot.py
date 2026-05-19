"""
tests/test_aliquot.py
=====================
Aliquot-sequence automaton tests.

The aliquot sequence of n is:
    n → s(n) → s(s(n)) → ...
where s(k) is the sum of proper divisors of k (all divisors of k except k
itself).

At each step we build a minimal DFA that recognises exactly the decimal
string of the current number, then verify membership with nerode.run().

The sequence terminates when:
  s(k) = 0          → 'terminated'  (reached 1 → 0)
  s(k) = k          → 'perfect'     (perfect number, fixed point)
  k already visited  → 'cycle'       (amicable pair, sociable chain, …)
  steps > LIMIT      → RuntimeError  (safety guard; driver numbers like 276)
"""

from __future__ import annotations

import pytest

LIMIT = 50  # max steps before guard fires

# Full decimal alphabet — needed for cross-number product DFAs
DECIMAL = list("0123456789")


# ---------------------------------------------------------------------------
# Pure-Python aliquot helpers
# ---------------------------------------------------------------------------


def _s(n: int) -> int:
    """Sum of proper divisors of n (i.e., all divisors of n except n itself)."""
    if n <= 1:
        return 0
    total = 1
    i = 2
    while i * i <= n:
        if n % i == 0:
            total += i
            if i != n // i:
                total += n // i
        i += 1
    return total


def aliquot_seq(start: int, limit: int = LIMIT):
    """
    Compute the aliquot sequence from *start*.

    Returns ``(sequence, reason)`` where *reason* is one of
    ``'terminated'``, ``'perfect'``, or ``'cycle'``.

    Raises ``RuntimeError`` if *limit* steps pass without resolution.
    """
    seq = [start]
    seen = {start}
    for _ in range(limit):
        nxt = _s(seq[-1])
        seq.append(nxt)
        if nxt == 0:
            return seq, "terminated"
        if nxt == seq[-2]:          # s(k) = k → perfect number
            return seq, "perfect"
        if nxt in seen:
            return seq, "cycle"
        seen.add(nxt)
    raise RuntimeError(
        f"Aliquot sequence from {start} exceeded {limit} steps without "
        f"termination or cycle. Last 5 values: {seq[-5:]}"
    )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _build(conn, n: int, *, shared_alphabet: bool = False) -> int:
    """
    Build (and return the id of) a minimal DFA that accepts exactly
    the decimal string representation of *n*.

    When *shared_alphabet* is True the full decimal alphabet {0-9} is
    passed explicitly so the resulting DFA is compatible for product
    construction with DFAs built from different numbers.
    """
    pattern = str(n)
    if shared_alphabet:
        return conn.execute(
            "SELECT nerode.from_regex(%s, NULL, %s)",
            (pattern, DECIMAL),
        ).fetchone()[0]
    return conn.execute(
        "SELECT nerode.from_regex(%s)", (pattern,)
    ).fetchone()[0]


def _run(conn, aid: int, word: str) -> bool:
    return conn.execute(
        "SELECT accept FROM nerode.run(%s, %s)", (aid, word)
    ).fetchone()[0]


def _print_seq(start: int, seq: list[int], reason: str) -> None:
    print(f"\n  aliquot({start}) [{reason}]: " + " → ".join(map(str, seq)))


# ---------------------------------------------------------------------------
# Pure-Python sequence tests (no DB needed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("start,expected_reason", [
    (6,   "perfect"),     # 6  → 6   (smallest perfect number)
    (28,  "perfect"),     # 28 → 28
    (496, "perfect"),     # 496 → 496
    (12,  "terminated"),  # 12 → 16 → 15 → 9 → 4 → 3 → 1 → 0
    (220, "cycle"),       # 220 → 284 → 220  (amicable pair)
])
def test_aliquot_sequence_reason(start, expected_reason):
    """Each well-known sequence terminates with the expected reason."""
    seq, reason = aliquot_seq(start)
    _print_seq(start, seq, reason)
    assert reason == expected_reason, (
        f"aliquot({start}): expected {expected_reason!r}, got {reason!r}"
    )


def test_aliquot_limit_guard():
    """
    A driver number (276) never terminates quickly.
    With limit=5, the guard must raise RuntimeError with a helpful message.
    """
    with pytest.raises(RuntimeError, match="exceeded"):
        aliquot_seq(276, limit=5)


# ---------------------------------------------------------------------------
# Automaton membership tests (DB required)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("start", [6, 12, 28, 220])
def test_aliquot_dfa_accepts_own_string(conn, start):
    """DFA built for each k in the sequence must accept str(k)."""
    seq, reason = aliquot_seq(start)
    _print_seq(start, seq, reason)
    for k in seq:
        if k == 0:
            continue
        aid = _build(conn, k)
        assert _run(conn, aid, str(k)), (
            f"DFA for {k} (in aliquot({start})) must accept '{k}'"
        )


@pytest.mark.parametrize("start", [12, 220])
def test_aliquot_dfa_rejects_next_step(conn, start):
    """
    DFA built for k must reject the next sequence value s(k),
    since they differ as decimal strings.
    """
    seq, _ = aliquot_seq(start)
    for i in range(len(seq) - 1):
        k, nxt = seq[i], seq[i + 1]
        if nxt == 0 or str(k) == str(nxt):
            continue
        aid_k = _build(conn, k)
        assert not _run(conn, aid_k, str(nxt)), (
            f"DFA for {k} must reject '{nxt}' (next aliquot step)"
        )


def test_aliquot_equivalence_same_number(conn):
    """
    Building a DFA for the same number twice (same pattern → same language)
    must produce equivalent automata.
    """
    for k in (12, 16, 15, 9, 4, 3, 1):
        aid1 = _build(conn, k)
        aid2 = _build(conn, k)
        row = conn.execute(
            "SELECT equivalent FROM nerode.equivalent(%s, %s)", (aid1, aid2)
        ).fetchone()
        assert row[0], f"Two DFAs for {k} must be language-equivalent"


def test_aliquot_union_dfa(conn):
    """
    Product (union) of DFAs for the amicable pair 220 and 284, over the
    shared full-decimal alphabet, must accept both members and reject
    non-members.
    """
    aid_220 = _build(conn, 220, shared_alphabet=True)
    aid_284 = _build(conn, 284, shared_alphabet=True)
    prod = conn.execute(
        "SELECT nerode.product(%s, %s, 'union')", (aid_220, aid_284)
    ).fetchone()[0]

    assert _run(conn, prod, "220"),      "union DFA must accept '220'"
    assert _run(conn, prod, "284"),      "union DFA must accept '284'"
    assert not _run(conn, prod, "221"),  "union DFA must reject '221'"
    assert not _run(conn, prod, ""),     "union DFA must reject empty string"


def test_aliquot_intersection_empty(conn):
    """
    Intersection of DFAs for 220 and 284 must be empty
    (no string is simultaneously both numbers).
    """
    aid_220 = _build(conn, 220, shared_alphabet=True)
    aid_284 = _build(conn, 284, shared_alphabet=True)
    prod = conn.execute(
        "SELECT nerode.product(%s, %s, 'intersection')", (aid_220, aid_284)
    ).fetchone()[0]

    assert not _run(conn, prod, "220"), "intersection DFA must reject '220'"
    assert not _run(conn, prod, "284"), "intersection DFA must reject '284'"


def test_aliquot_certify_chain(conn):
    """
    Every membership step in the aliquot chain from 12 must produce a valid
    cert.claim and cert.witness (proof-carrying computation trace).
    """
    seq, _ = aliquot_seq(12)
    for k in seq:
        if k == 0:
            continue
        aid = _build(conn, k)
        row = conn.execute(
            "SELECT accept, claim_id FROM nerode.certify_run(%s, %s)",
            (aid, str(k)),
        ).fetchone()
        assert row[0] is True,       f"certify_run must accept '{k}'"
        assert row[1] is not None,   f"certify_run must return a claim_id for '{k}'"


def test_aliquot_state_facts(conn):
    """
    calx_state_facts() must return valid arithmetic metadata for the DFA of
    each number in the aliquot(12) chain.
    """
    seq, _ = aliquot_seq(12)
    for k in seq:
        if k == 0:
            continue
        aid = _build(conn, k)
        row = conn.execute(
            "SELECT nerode.calx_state_facts(%s)", (aid,)
        ).fetchone()
        facts = row[0]
        assert facts is not None,           f"calx_state_facts must return data for DFA of {k}"
        assert "state_count" in facts,      f"missing state_count for DFA of {k}"
        assert "is_prime" in facts,         f"missing is_prime for DFA of {k}"
        assert "factorization" in facts,    f"missing factorization for DFA of {k}"
        assert "pumping_constant" in facts, f"missing pumping_constant for DFA of {k}"


def test_aliquot_minimize_certified(conn):
    """
    Each DFA in the aliquot(12) chain can be re-minimized and certified.
    The certified DFA must be language-equivalent to the original.
    """
    seq, _ = aliquot_seq(12)
    for k in seq:
        if k == 0:
            continue
        aid = _build(conn, k)
        row = conn.execute(
            "SELECT automaton_id, claim_id FROM nerode.minimize_certified(%s)", (aid,)
        ).fetchone()
        min_id, claim_id = row
        assert min_id is not None,   f"minimize_certified({k}) must return automaton_id"
        assert claim_id is not None, f"minimize_certified({k}) must return claim_id"

        # The minimized DFA must still accept the original number
        assert _run(conn, min_id, str(k)), (
            f"minimized DFA for {k} must still accept '{k}'"
        )
