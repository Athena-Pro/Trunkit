"""
Phase 1c — Product Corpus tests.

Verifies that nerode.build_product_corpus() correctly constructs pairwise
intersection product DFAs for all registered corpus pairs, and that the
relationship between state_bound (|Q1|×|Q2|) and actual_count (lcm(|Q1|,|Q2|))
holds for cycle languages over {a}.

Theoretical background
----------------------
For (a^m)* ∩ (a^n)*:
  - The product DFA's reachable states form a single cycle of length lcm(m, n).
  - The naive BFS bound is |Q1| × |Q2| = m × n.
  - actual_count = lcm(m, n) = m*n / gcd(m, n).
  - The bound is tight (actual == bound) iff gcd(m, n) = 1 (coprime).
"""

from __future__ import annotations

import math

import psycopg
import pytest

from nerode.db import apply_schema

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DSN = "postgresql://nerode:nerode@localhost:5435/nerode"

# Expected pairs: (lhs_slug, rhs_slug, lhs_states, rhs_states)
_PAIRS = [
    ("cycle_4",  "cycle_6",   4,  6),
    ("cycle_4",  "cycle_9",   4,  9),
    ("cycle_4",  "cycle_10",  4, 10),
    ("cycle_6",  "cycle_9",   6,  9),
    ("cycle_6",  "cycle_10",  6, 10),
]


@pytest.fixture(scope="module")
def conn():
    c = psycopg.connect(DSN)
    apply_schema(c)
    yield c
    c.rollback()
    c.close()


@pytest.fixture(autouse=True)
def rollback(conn):
    yield
    conn.rollback()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _product_row(conn, lhs_slug: str, rhs_slug: str):
    """Return (product_id, state_bound, actual_count) for a pair."""
    return conn.execute(
        "SELECT product_id, state_bound, actual_count "
        "FROM nerode.product_pairs "
        "WHERE lhs_slug = %s AND rhs_slug = %s",
        (lhs_slug, rhs_slug),
    ).fetchone()


def _state_count(conn, aid: int) -> int:
    """Return state_count from nerode.automata."""
    row = conn.execute(
        "SELECT state_count FROM nerode.automata WHERE id = %s", (aid,)
    ).fetchone()
    return row[0]


def _run_accept(conn, aid: int, word: str) -> bool:
    """Simulate DFA aid on word; return accept boolean."""
    row = conn.execute(
        "SELECT accept FROM nerode.run(%s, %s, FALSE)", (aid, word)
    ).fetchone()
    return row[0]


def _corpus_aid(conn, slug: str) -> int:
    """Look up automaton_id from nerode.corpus."""
    row = conn.execute(
        "SELECT automaton_id FROM nerode.corpus WHERE slug = %s", (slug,)
    ).fetchone()
    return row[0]


# ---------------------------------------------------------------------------
# 1. TestProductPairsBuilt — all 5 pairs are registered and fully populated
# ---------------------------------------------------------------------------

class TestProductPairsBuilt:

    def test_all_five_pairs_exist(self, conn):
        """All five corpus pairs are present in product_pairs."""
        count = conn.execute(
            "SELECT count(*) FROM nerode.product_pairs"
        ).fetchone()[0]
        assert count >= 5, f"expected ≥5 pairs, got {count}"

    def test_all_product_ids_non_null(self, conn):
        """Every registered pair has a product_id (DFA was built)."""
        null_count = conn.execute(
            "SELECT count(*) FROM nerode.product_pairs WHERE product_id IS NULL"
        ).fetchone()[0]
        assert null_count == 0, f"{null_count} pair(s) still lack a product_id"

    def test_all_product_dfa_types_are_dfa(self, conn):
        """All product automata are type='DFA'."""
        rows = conn.execute(
            "SELECT pp.lhs_slug, pp.rhs_slug, a.type "
            "FROM nerode.product_pairs pp "
            "JOIN nerode.automata a ON a.id = pp.product_id"
        ).fetchall()
        bad = [(l, r, t) for l, r, t in rows if t != "DFA"]
        assert bad == [], f"non-DFA product automata: {bad}"

    def test_all_actual_counts_positive(self, conn):
        """All product DFAs have a positive state count."""
        rows = conn.execute(
            "SELECT lhs_slug, rhs_slug, actual_count FROM nerode.product_pairs"
        ).fetchall()
        bad = [(l, r, ac) for l, r, ac in rows if ac is None or ac <= 0]
        assert bad == [], f"zero/null actual_count: {bad}"

    def test_build_product_corpus_idempotent(self, conn):
        """Calling build_product_corpus() twice returns consistent results."""
        rows1 = conn.execute(
            "SELECT lhs_slug, rhs_slug, product_id, state_bound, actual_count "
            "FROM nerode.build_product_corpus() ORDER BY lhs_slug, rhs_slug"
        ).fetchall()
        rows2 = conn.execute(
            "SELECT lhs_slug, rhs_slug, product_id, state_bound, actual_count "
            "FROM nerode.build_product_corpus() ORDER BY lhs_slug, rhs_slug"
        ).fetchall()
        # product_ids must be stable across repeated calls
        assert rows1 == rows2, "build_product_corpus() is not idempotent"


# ---------------------------------------------------------------------------
# 2. TestBoundVsActual — for each pair: actual = lcm, bound = m*n
# ---------------------------------------------------------------------------

class TestBoundVsActual:

    @pytest.mark.parametrize("lhs,rhs,m,n", _PAIRS)
    def test_state_bound_equals_product_of_sizes(self, conn, lhs, rhs, m, n):
        """state_bound == |Q_lhs| * |Q_rhs| for each pair."""
        row = _product_row(conn, lhs, rhs)
        assert row is not None, f"pair ({lhs}, {rhs}) not found"
        _, bound, _ = row
        assert bound == m * n, (
            f"({lhs}, {rhs}): expected bound={m*n}, got {bound}"
        )

    @pytest.mark.parametrize("lhs,rhs,m,n", _PAIRS)
    def test_actual_count_equals_lcm(self, conn, lhs, rhs, m, n):
        """actual_count == lcm(|Q_lhs|, |Q_rhs|) for each pair."""
        row = _product_row(conn, lhs, rhs)
        assert row is not None, f"pair ({lhs}, {rhs}) not found"
        _, _, actual = row
        expected_lcm = math.lcm(m, n)
        assert actual == expected_lcm, (
            f"({lhs}, {rhs}): expected actual={expected_lcm}, got {actual}"
        )

    @pytest.mark.parametrize("lhs,rhs,m,n", _PAIRS)
    def test_actual_leq_bound(self, conn, lhs, rhs, m, n):
        """actual_count <= state_bound for every pair."""
        row = _product_row(conn, lhs, rhs)
        _, bound, actual = row
        assert actual <= bound, (
            f"({lhs}, {rhs}): actual {actual} > bound {bound}"
        )


# ---------------------------------------------------------------------------
# 3. TestCoprimeVsNonCoprime — coprime pairs hit the bound; others don't
# ---------------------------------------------------------------------------

class TestCoprimeVsNonCoprime:

    @pytest.mark.parametrize("lhs,rhs,m,n", [
        p for p in _PAIRS if math.gcd(p[2], p[3]) == 1
    ])
    def test_coprime_pairs_are_tight(self, conn, lhs, rhs, m, n):
        """Coprime pairs (gcd=1): actual_count == state_bound (bound tight)."""
        row = _product_row(conn, lhs, rhs)
        _, bound, actual = row
        assert actual == bound, (
            f"({lhs}, {rhs}) is coprime but actual {actual} != bound {bound}"
        )

    @pytest.mark.parametrize("lhs,rhs,m,n", [
        p for p in _PAIRS if math.gcd(p[2], p[3]) > 1
    ])
    def test_non_coprime_pairs_are_not_tight(self, conn, lhs, rhs, m, n):
        """Non-coprime pairs (gcd>1): actual_count < state_bound."""
        row = _product_row(conn, lhs, rhs)
        _, bound, actual = row
        assert actual < bound, (
            f"({lhs}, {rhs}) has gcd={math.gcd(m,n)} but actual {actual} >= bound {bound}"
        )


# ---------------------------------------------------------------------------
# 4. TestProductDFALanguage — the intersection DFA accepts the right language
# ---------------------------------------------------------------------------

class TestProductDFALanguage:
    """
    Verify language correctness for selected product DFAs.

    The intersection language of (a^m)* and (a^n)* is (a^{lcm(m,n)})*.
    A word a^k belongs to this language iff k is divisible by lcm(m, n).
    """

    # --- cycle_4 ∩ cycle_10  (lcm=20, gcd=2, non-coprime) ---

    def test_cycle4_x_cycle10_accepts_empty(self, conn):
        """cycle_4 ∩ cycle_10: '' (length 0) is accepted."""
        pid, _, _ = _product_row(conn, "cycle_4", "cycle_10")
        assert _run_accept(conn, pid, "") is True

    def test_cycle4_x_cycle10_accepts_lcm(self, conn):
        """cycle_4 ∩ cycle_10: 'a'^20 is accepted (length = lcm)."""
        pid, _, _ = _product_row(conn, "cycle_4", "cycle_10")
        assert _run_accept(conn, pid, "a" * 20) is True

    def test_cycle4_x_cycle10_rejects_multiple_of_4_only(self, conn):
        """cycle_4 ∩ cycle_10: 'a'^4 is rejected (not a multiple of 10)."""
        pid, _, _ = _product_row(conn, "cycle_4", "cycle_10")
        assert _run_accept(conn, pid, "a" * 4) is False

    def test_cycle4_x_cycle10_rejects_multiple_of_10_only(self, conn):
        """cycle_4 ∩ cycle_10: 'a'^10 is rejected (not a multiple of 4)."""
        pid, _, _ = _product_row(conn, "cycle_4", "cycle_10")
        assert _run_accept(conn, pid, "a" * 10) is False

    # --- cycle_4 ∩ cycle_9  (lcm=36, gcd=1, coprime → bound tight) ---

    def test_cycle4_x_cycle9_accepts_lcm(self, conn):
        """cycle_4 ∩ cycle_9: 'a'^36 is accepted (length = lcm = bound)."""
        pid, _, _ = _product_row(conn, "cycle_4", "cycle_9")
        assert _run_accept(conn, pid, "a" * 36) is True

    def test_cycle4_x_cycle9_rejects_multiple_of_4_only(self, conn):
        """cycle_4 ∩ cycle_9: 'a'^4 is rejected (not a multiple of 9)."""
        pid, _, _ = _product_row(conn, "cycle_4", "cycle_9")
        assert _run_accept(conn, pid, "a" * 4) is False

    def test_cycle4_x_cycle9_rejects_multiple_of_9_only(self, conn):
        """cycle_4 ∩ cycle_9: 'a'^9 is rejected (not a multiple of 4)."""
        pid, _, _ = _product_row(conn, "cycle_4", "cycle_9")
        assert _run_accept(conn, pid, "a" * 9) is False

    # --- cycle_6 ∩ cycle_9  (lcm=18, gcd=3, non-coprime) ---

    def test_cycle6_x_cycle9_accepts_lcm(self, conn):
        """cycle_6 ∩ cycle_9: 'a'^18 is accepted (length = lcm)."""
        pid, _, _ = _product_row(conn, "cycle_6", "cycle_9")
        assert _run_accept(conn, pid, "a" * 18) is True

    def test_cycle6_x_cycle9_rejects_multiple_of_6_only(self, conn):
        """cycle_6 ∩ cycle_9: 'a'^6 is rejected (not a multiple of 9)."""
        pid, _, _ = _product_row(conn, "cycle_6", "cycle_9")
        assert _run_accept(conn, pid, "a" * 6) is False

    # --- product DFA state_count matches actual_count in table ---

    def test_product_dfa_state_count_consistent_with_table(self, conn):
        """The product DFA's automata.state_count agrees with product_pairs.actual_count."""
        rows = conn.execute(
            "SELECT pp.lhs_slug, pp.rhs_slug, pp.actual_count, a.state_count "
            "FROM nerode.product_pairs pp "
            "JOIN nerode.automata a ON a.id = pp.product_id"
        ).fetchall()
        mismatches = [
            (l, r, ac, sc)
            for l, r, ac, sc in rows
            if ac != sc
        ]
        assert mismatches == [], (
            f"actual_count / automata.state_count mismatch: {mismatches}"
        )
