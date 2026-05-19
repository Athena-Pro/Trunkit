"""
tests/test_phase1b.py — Phase 1b: Validation Corpus

Verifies that the named corpus DFAs (cycle_4, cycle_6, cycle_9, cycle_10):
  1. Are built with the correct composite state counts.
  2. Produce is_prime=False and correct factorizations from calx arithmetic.
  3. Are identified as their own eigenforms (fast path — built by from_regex).
  4. Interact correctly with non-minimal composite-state DFAs (slow path):
       NON_MINIMAL_CYCLE4: 5 states → minimize → 4 states (2²)
       NON_MINIMAL_CYCLE6: 7 states → minimize → 6 states (2·3)

Non-minimal fixtures use the "extra accepting state" construction:
  a period-n cycle DFA has n states {q0…q_{n-1}};
  add q_n as a copy of q0 (also accepting), with q_{n-1} →a→ q_n and q_n →a→ q1.
  q0 and q_n are Nerode-equivalent (same future), so minimize merges them → n states.
"""

from __future__ import annotations

import psycopg

from nerode.automata import import_to_db
from nerode.db import apply_schema

DSN = "postgresql://nerode:nerode@localhost:5435/nerode"

# ---------------------------------------------------------------------------
# Non-minimal test fixtures
# ---------------------------------------------------------------------------

# 5-state DFA for the language (aaaa)* — same language as cycle_4.
# q0 and q4 are both accepting with identical futures; minimize → 4 states.
NON_MINIMAL_CYCLE4 = {
    "type": "DFA",
    "name": "cycle4_nonmin",
    "alphabet": ["a"],
    "states": [
        {"id": 0, "is_initial": True,  "is_accepting": True,  "label": "q0"},
        {"id": 1, "is_initial": False, "is_accepting": False, "label": "q1"},
        {"id": 2, "is_initial": False, "is_accepting": False, "label": "q2"},
        {"id": 3, "is_initial": False, "is_accepting": False, "label": "q3"},
        {"id": 4, "is_initial": False, "is_accepting": True,  "label": "q4"},
    ],
    "transitions": [
        {"from": 0, "symbol": "a", "to": 1},
        {"from": 1, "symbol": "a", "to": 2},
        {"from": 2, "symbol": "a", "to": 3},
        {"from": 3, "symbol": "a", "to": 4},
        {"from": 4, "symbol": "a", "to": 1},  # q4 ≡ q0: both accept, both goto q1 on 'a'
    ],
}

# 7-state DFA for the language (aaaaaa)* — same language as cycle_6.
# q0 and q6 are both accepting with identical futures; minimize → 6 states.
NON_MINIMAL_CYCLE6 = {
    "type": "DFA",
    "name": "cycle6_nonmin",
    "alphabet": ["a"],
    "states": [
        {"id": 0, "is_initial": True,  "is_accepting": True,  "label": "q0"},
        {"id": 1, "is_initial": False, "is_accepting": False, "label": "q1"},
        {"id": 2, "is_initial": False, "is_accepting": False, "label": "q2"},
        {"id": 3, "is_initial": False, "is_accepting": False, "label": "q3"},
        {"id": 4, "is_initial": False, "is_accepting": False, "label": "q4"},
        {"id": 5, "is_initial": False, "is_accepting": False, "label": "q5"},
        {"id": 6, "is_initial": False, "is_accepting": True,  "label": "q6"},
    ],
    "transitions": [
        {"from": 0, "symbol": "a", "to": 1},
        {"from": 1, "symbol": "a", "to": 2},
        {"from": 2, "symbol": "a", "to": 3},
        {"from": 3, "symbol": "a", "to": 4},
        {"from": 4, "symbol": "a", "to": 5},
        {"from": 5, "symbol": "a", "to": 6},
        {"from": 6, "symbol": "a", "to": 1},  # q6 ≡ q0: both accept, both goto q1 on 'a'
    ],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

import pytest


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

def _corpus_aid(conn, slug: str) -> int:
    """Return the committed automaton_id for a corpus slug."""
    row = conn.execute(
        "SELECT automaton_id FROM nerode.corpus WHERE slug = %s", (slug,)
    ).fetchone()
    assert row is not None, f"corpus slug {slug!r} not found"
    assert row[0] is not None, f"corpus slug {slug!r} has NULL automaton_id"
    return row[0]


def _state_count(conn, aid: int) -> int:
    return conn.execute(
        "SELECT state_count FROM nerode.automata WHERE id = %s", (aid,)
    ).fetchone()[0]


def _calx_facts(conn, aid: int) -> dict:
    return conn.execute(
        "SELECT nerode.calx_state_facts(%s)", (aid,)
    ).fetchone()[0]


def _certify_prime(conn, aid: int):
    """Returns (is_prime, state_count, claim_id, calx_facts)."""
    return conn.execute(
        "SELECT is_prime, state_count, claim_id, calx_facts "
        "FROM nerode.certify_prime_dfa(%s)",
        (aid,),
    ).fetchone()


def _certify_eigenform(conn, aid: int):
    """Returns (is_minimal, eigenform_id, original_states, minimal_states, claim_id)."""
    return conn.execute(
        "SELECT is_minimal, eigenform_id, original_states, minimal_states, claim_id "
        "FROM nerode.certify_eigenform(%s)",
        (aid,),
    ).fetchone()


# ===========================================================================
# TestCorpusBuilt
# Verify the corpus table is populated with the expected DFAs.
# ===========================================================================

class TestCorpusBuilt:

    def test_all_slugs_present(self, conn):
        slugs = {
            r[0]
            for r in conn.execute("SELECT slug FROM nerode.corpus").fetchall()
        }
        assert {"cycle_4", "cycle_6", "cycle_9", "cycle_10"}.issubset(slugs)

    def test_all_automaton_ids_populated(self, conn):
        null_count = conn.execute(
            "SELECT count(*) FROM nerode.corpus WHERE automaton_id IS NULL"
        ).fetchone()[0]
        assert null_count == 0, "some corpus entries have no automaton_id"

    def test_cycle_4_state_count(self, conn):
        aid = _corpus_aid(conn, "cycle_4")
        assert _state_count(conn, aid) == 4

    def test_cycle_6_state_count(self, conn):
        aid = _corpus_aid(conn, "cycle_6")
        assert _state_count(conn, aid) == 6

    def test_cycle_9_and_10_state_counts(self, conn):
        assert _state_count(conn, _corpus_aid(conn, "cycle_9"))  == 9
        assert _state_count(conn, _corpus_aid(conn, "cycle_10")) == 10


# ===========================================================================
# TestCompositeArithmetic
# calx_state_facts() must return correct factorizations for all corpus DFAs.
# ===========================================================================

class TestCompositeArithmetic:

    def test_cycle_4_is_composite(self, conn):
        facts = _calx_facts(conn, _corpus_aid(conn, "cycle_4"))
        assert facts["is_prime"] is False

    def test_cycle_4_factorization(self, conn):
        facts = _calx_facts(conn, _corpus_aid(conn, "cycle_4"))
        assert facts["factorization"] == [2, 2]

    def test_cycle_6_is_composite(self, conn):
        facts = _calx_facts(conn, _corpus_aid(conn, "cycle_6"))
        assert facts["is_prime"] is False

    def test_cycle_6_factorization(self, conn):
        facts = _calx_facts(conn, _corpus_aid(conn, "cycle_6"))
        assert facts["factorization"] == [2, 3]

    def test_cycle_9_is_composite(self, conn):
        facts = _calx_facts(conn, _corpus_aid(conn, "cycle_9"))
        assert facts["is_prime"] is False

    def test_cycle_9_factorization(self, conn):
        facts = _calx_facts(conn, _corpus_aid(conn, "cycle_9"))
        assert facts["factorization"] == [3, 3]

    def test_cycle_10_is_composite(self, conn):
        facts = _calx_facts(conn, _corpus_aid(conn, "cycle_10"))
        assert facts["is_prime"] is False

    def test_cycle_10_factorization(self, conn):
        facts = _calx_facts(conn, _corpus_aid(conn, "cycle_10"))
        assert facts["factorization"] == [2, 5]


# ===========================================================================
# TestCertifyPrimeDFAComposite
# certify_prime_dfa() must issue is_prime=False claims for all corpus DFAs.
# ===========================================================================

class TestCertifyPrimeDFAComposite:

    def test_certify_cycle_4_composite(self, conn):
        row = _certify_prime(conn, _corpus_aid(conn, "cycle_4"))
        is_prime, sc, claim_id, calx = row
        assert is_prime is False
        assert sc == 4
        assert claim_id > 0
        assert calx["factorization"] == [2, 2]

    def test_certify_cycle_6_composite(self, conn):
        row = _certify_prime(conn, _corpus_aid(conn, "cycle_6"))
        is_prime, sc, claim_id, calx = row
        assert is_prime is False
        assert sc == 6
        assert calx["factorization"] == [2, 3]

    def test_certify_cycle_9_composite(self, conn):
        row = _certify_prime(conn, _corpus_aid(conn, "cycle_9"))
        is_prime, sc, claim_id, calx = row
        assert is_prime is False
        assert sc == 9
        assert calx["factorization"] == [3, 3]

    def test_certify_cycle_10_composite(self, conn):
        row = _certify_prime(conn, _corpus_aid(conn, "cycle_10"))
        is_prime, sc, claim_id, calx = row
        assert is_prime is False
        assert sc == 10
        assert calx["factorization"] == [2, 5]


# ===========================================================================
# TestEigenformFastPathComposite
# corpus DFAs are from_regex outputs → fast path → eigenform is self.
# Exercises the fast path with non-trivial (composite) state counts.
# ===========================================================================

class TestEigenformFastPathComposite:

    def test_cycle_4_eigenform_is_self(self, conn):
        aid = _corpus_aid(conn, "cycle_4")
        is_min, ef_id, orig, mins, claim_id = _certify_eigenform(conn, aid)
        assert is_min is True
        assert ef_id == aid      # fast path: eigenform = self
        assert orig == mins == 4
        assert claim_id > 0

    def test_cycle_6_eigenform_is_self(self, conn):
        aid = _corpus_aid(conn, "cycle_6")
        is_min, ef_id, orig, mins, claim_id = _certify_eigenform(conn, aid)
        assert is_min is True
        assert ef_id == aid
        assert orig == mins == 6

    def test_cycle_9_eigenform_is_self(self, conn):
        aid = _corpus_aid(conn, "cycle_9")
        is_min, ef_id, orig, mins, claim_id = _certify_eigenform(conn, aid)
        assert is_min is True
        assert ef_id == aid
        assert orig == mins == 9

    def test_cycle_10_eigenform_is_self(self, conn):
        aid = _corpus_aid(conn, "cycle_10")
        is_min, ef_id, orig, mins, claim_id = _certify_eigenform(conn, aid)
        assert is_min is True
        assert ef_id == aid
        assert orig == mins == 10


# ===========================================================================
# TestNonMinimalComposite
# Import non-minimal DFAs whose eigenforms have composite state counts.
# This exercises the slow path in certify_eigenform AND connects the result
# to certify_prime_dfa to show the full arithmetic annotation pipeline.
# ===========================================================================

class TestNonMinimalComposite:

    # --- cycle_4 non-minimal (5 → 4) ---

    def test_cycle4_nonmin_identified(self, conn):
        aid = import_to_db(conn, dict(NON_MINIMAL_CYCLE4))
        is_min, ef_id, orig, mins, claim_id = _certify_eigenform(conn, aid)
        assert is_min is False
        assert orig == 5
        assert mins == 4
        assert ef_id != aid
        assert claim_id > 0

    def test_cycle4_nonmin_eigenform_state_count(self, conn):
        aid = import_to_db(conn, dict(NON_MINIMAL_CYCLE4))
        _, ef_id, *_ = _certify_eigenform(conn, aid)
        assert _state_count(conn, ef_id) == 4

    def test_cycle4_nonmin_eigenform_is_composite(self, conn):
        aid = import_to_db(conn, dict(NON_MINIMAL_CYCLE4))
        _, ef_id, *_ = _certify_eigenform(conn, aid)
        prime_row = _certify_prime(conn, ef_id)
        is_prime, sc, claim_id, calx = prime_row
        assert is_prime is False
        assert sc == 4
        assert calx["factorization"] == [2, 2]

    # --- cycle_6 non-minimal (7 → 6) ---

    def test_cycle6_nonmin_identified(self, conn):
        aid = import_to_db(conn, dict(NON_MINIMAL_CYCLE6))
        is_min, ef_id, orig, mins, claim_id = _certify_eigenform(conn, aid)
        assert is_min is False
        assert orig == 7
        assert mins == 6
        assert ef_id != aid

    def test_cycle6_nonmin_eigenform_state_count(self, conn):
        aid = import_to_db(conn, dict(NON_MINIMAL_CYCLE6))
        _, ef_id, *_ = _certify_eigenform(conn, aid)
        assert _state_count(conn, ef_id) == 6

    def test_cycle6_nonmin_eigenform_is_composite(self, conn):
        aid = import_to_db(conn, dict(NON_MINIMAL_CYCLE6))
        _, ef_id, *_ = _certify_eigenform(conn, aid)
        prime_row = _certify_prime(conn, ef_id)
        is_prime, sc, claim_id, calx = prime_row
        assert is_prime is False
        assert sc == 6
        assert calx["factorization"] == [2, 3]

    # --- language equivalence: non-minimal DFA ≡ its eigenform ---

    def test_cycle4_nonmin_language_preserved_in_eigenform(self, conn):
        """minimize() must produce a language-equivalent eigenform."""
        aid = import_to_db(conn, dict(NON_MINIMAL_CYCLE4))
        _, ef_id, *_ = _certify_eigenform(conn, aid)
        row = conn.execute(
            "SELECT equivalent FROM nerode.equivalent(%s, %s)",
            (aid, ef_id),
        ).fetchone()
        assert row[0] is True

    def test_cycle6_nonmin_language_preserved_in_eigenform(self, conn):
        """minimize() must produce a language-equivalent eigenform."""
        aid = import_to_db(conn, dict(NON_MINIMAL_CYCLE6))
        _, ef_id, *_ = _certify_eigenform(conn, aid)
        row = conn.execute(
            "SELECT equivalent FROM nerode.equivalent(%s, %s)",
            (aid, ef_id),
        ).fetchone()
        assert row[0] is True
