"""
tests/test_phase2.py — Phase 2: Eigenform / Fixed-Point Scanner

Tests for nerode.certify_eigenform() and nerode.scan_eigenforms().

An automaton M is its own eigenform iff it is already minimal:
  minimize(M) produces a DFA with the same state count as M.

DFA reference:
  from_regex("(ab)*")  — 3 states, prime, already minimal (fast path)
  from_regex("a+")     — 3 states, prime, already minimal (fast path)
  NON_MINIMAL_DFA      — 3 states, NOT minimal (imports to slow path → 2-state eigenform)
  MINIMAL_DFA          — 2 states, already minimal (imports to slow path → is_minimal=True)
"""

from __future__ import annotations

import pytest
import psycopg

from nerode.automata import import_to_db
from nerode.db import apply_schema, connect

DSN = "postgresql://nerode:nerode@localhost:5435/nerode"

# ---------------------------------------------------------------------------
# DFA fixtures
# ---------------------------------------------------------------------------

# 3-state non-minimal DFA for the language "strings over {a,b} containing ≥1 'a'".
# States q1 and q2 are Nerode-equivalent (both accepting, same future behaviour).
# Minimal DFA for this language has 2 states.
NON_MINIMAL_DFA = {
    "type": "DFA",
    "name": "contains_a_nonmin",
    "alphabet": ["a", "b"],
    "states": [
        {"id": 0, "is_initial": True,  "is_accepting": False, "label": "q0"},
        {"id": 1, "is_initial": False, "is_accepting": True,  "label": "q1"},
        {"id": 2, "is_initial": False, "is_accepting": True,  "label": "q2"},
    ],
    "transitions": [
        {"from": 0, "symbol": "a", "to": 1},
        {"from": 0, "symbol": "b", "to": 0},
        {"from": 1, "symbol": "a", "to": 2},
        {"from": 1, "symbol": "b", "to": 2},
        {"from": 2, "symbol": "a", "to": 2},
        {"from": 2, "symbol": "b", "to": 2},
    ],
}

# 2-state minimal DFA for the same language (already minimal).
MINIMAL_DFA = {
    "type": "DFA",
    "name": "contains_a_min",
    "alphabet": ["a", "b"],
    "states": [
        {"id": 0, "is_initial": True,  "is_accepting": False, "label": "q0"},
        {"id": 1, "is_initial": False, "is_accepting": True,  "label": "q1"},
    ],
    "transitions": [
        {"from": 0, "symbol": "a", "to": 1},
        {"from": 0, "symbol": "b", "to": 0},
        {"from": 1, "symbol": "a", "to": 1},
        {"from": 1, "symbol": "b", "to": 1},
    ],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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

def _from_regex(conn, pattern: str) -> int:
    row = conn.execute("SELECT nerode.from_regex(%s)", (pattern,)).fetchone()
    return row[0]


def _state_count(conn, aid: int) -> int:
    row = conn.execute(
        "SELECT state_count FROM nerode.automata WHERE id = %s", (aid,)
    ).fetchone()
    return row[0]


def _certify_eigenform(conn, aid: int):
    """Returns (is_minimal, eigenform_id, original_states, minimal_states, claim_id)."""
    return conn.execute(
        "SELECT is_minimal, eigenform_id, original_states, minimal_states, claim_id "
        "FROM nerode.certify_eigenform(%s)",
        (aid,),
    ).fetchone()


def _cert_witness(conn, claim_id: int):
    """Returns (kind, body) for the most recent witness for this claim."""
    return conn.execute(
        "SELECT w.kind, w.body "
        "FROM cert.witness w "
        "JOIN cert.certificate c ON c.id = w.certificate_id "
        "WHERE c.claim_id = %s "
        "ORDER BY c.seq DESC "
        "LIMIT 1",
        (claim_id,),
    ).fetchone()


# ===========================================================================
# TestCertifyEigenformFromRegex
# from_regex DFAs are already minimal → fast path (eigenform = self)
# ===========================================================================

class TestCertifyEigenformFromRegex:

    def test_already_minimal_ab_star(self, conn):
        aid = _from_regex(conn, "(ab)*")
        row = _certify_eigenform(conn, aid)
        assert row is not None
        is_min, ef_id, orig, mins, claim_id = row
        assert is_min is True
        assert ef_id == aid       # fast path: eigenform is self
        assert orig == mins       # same state count
        assert claim_id > 0

    def test_already_minimal_a_plus(self, conn):
        aid = _from_regex(conn, "a+")
        row = _certify_eigenform(conn, aid)
        is_min, ef_id, orig, mins, claim_id = row
        assert is_min is True
        assert ef_id == aid

    def test_already_minimal_union(self, conn):
        aid = _from_regex(conn, "a|b")
        row = _certify_eigenform(conn, aid)
        is_min, ef_id, orig, mins, claim_id = row
        assert is_min is True
        assert ef_id == aid

    def test_state_counts_match(self, conn):
        aid = _from_regex(conn, "a*b*")
        row = _certify_eigenform(conn, aid)
        is_min, ef_id, orig, mins, claim_id = row
        assert orig == mins
        assert orig == _state_count(conn, aid)

    def test_witness_kind_nerode_partition(self, conn):
        aid = _from_regex(conn, "a+")
        row = _certify_eigenform(conn, aid)
        claim_id = row[4]
        kind, body = _cert_witness(conn, claim_id)
        assert kind == "nerode_partition"
        assert body["is_minimal"] is True
        assert "partition" in body

    def test_partition_all_singletons(self, conn):
        """For a minimal DFA, every Hopcroft block is a singleton state."""
        aid = _from_regex(conn, "a")   # accepts only "a"
        row = _certify_eigenform(conn, aid)
        claim_id = row[4]
        _, body = _cert_witness(conn, claim_id)
        partition = body["partition"]
        for block_id, states in partition.items():
            assert len(states) == 1, f"block {block_id} is not a singleton: {states}"

    def test_claim_method_and_subject(self, conn):
        aid = _from_regex(conn, "b*")
        row = _certify_eigenform(conn, aid)
        claim_id = row[4]
        claim = conn.execute(
            "SELECT method, subject_kind FROM cert.claim WHERE id = %s",
            (claim_id,),
        ).fetchone()
        assert claim[0] == "nerode_eigenform"
        assert claim[1] == "nerode_automaton"

    def test_subject_ref_contains_eigenform_id(self, conn):
        aid = _from_regex(conn, "(ab)*")
        row = _certify_eigenform(conn, aid)
        claim_id = row[4]
        subject_ref = conn.execute(
            "SELECT subject_ref FROM cert.claim WHERE id = %s", (claim_id,)
        ).fetchone()[0]
        assert subject_ref["automaton_id"] == aid
        assert subject_ref["eigenform_id"] == aid   # fast path: self


# ===========================================================================
# TestCertifyEigenformNonMinimal
# Imported 3-state non-minimal DFA → slow path → is_minimal=False
# ===========================================================================

class TestCertifyEigenformNonMinimal:

    def test_identified_as_non_minimal(self, conn):
        aid = import_to_db(conn, dict(NON_MINIMAL_DFA))
        row = _certify_eigenform(conn, aid)
        assert row is not None
        is_min, ef_id, orig, mins, claim_id = row
        assert is_min is False
        assert orig == 3
        assert mins == 2
        assert ef_id != aid
        assert claim_id > 0

    def test_eigenform_has_correct_state_count(self, conn):
        aid = import_to_db(conn, dict(NON_MINIMAL_DFA))
        row = _certify_eigenform(conn, aid)
        ef_id = row[1]
        assert _state_count(conn, ef_id) == 2

    def test_eigenform_accepts_same_language(self, conn):
        """The eigenform must accept the same language as the original DFA."""
        aid = import_to_db(conn, dict(NON_MINIMAL_DFA))
        row = _certify_eigenform(conn, aid)
        ef_id = row[1]
        eq_row = conn.execute(
            "SELECT equivalent FROM nerode.equivalent(%s, %s)",
            (aid, ef_id),
        ).fetchone()
        assert eq_row[0] is True

    def test_witness_reflects_non_minimal(self, conn):
        aid = import_to_db(conn, dict(NON_MINIMAL_DFA))
        row = _certify_eigenform(conn, aid)
        claim_id = row[4]
        kind, body = _cert_witness(conn, claim_id)
        assert kind == "nerode_partition"
        assert body["is_minimal"] is False
        assert body["original_states"] == 3
        assert body["minimal_states"] == 2

    def test_claim_method_correct(self, conn):
        aid = import_to_db(conn, dict(NON_MINIMAL_DFA))
        row = _certify_eigenform(conn, aid)
        claim_id = row[4]
        method = conn.execute(
            "SELECT method FROM cert.claim WHERE id = %s", (claim_id,)
        ).fetchone()[0]
        assert method == "nerode_eigenform"


# ===========================================================================
# TestCertifyEigenformImportedMinimal
# Imported 2-state minimal DFA → slow path → is_minimal=True
# (slow path still calls minimize(), producing a new DFA with different id)
# ===========================================================================

class TestCertifyEigenformImportedMinimal:

    def test_identified_as_minimal(self, conn):
        aid = import_to_db(conn, dict(MINIMAL_DFA))
        row = _certify_eigenform(conn, aid)
        assert row is not None
        is_min, ef_id, orig, mins, claim_id = row
        assert is_min is True
        assert orig == 2
        assert mins == 2
        assert claim_id > 0

    def test_eigenform_different_id_slow_path(self, conn):
        """Slow path always creates a new eigenform automaton, even for minimal input."""
        aid = import_to_db(conn, dict(MINIMAL_DFA))
        row = _certify_eigenform(conn, aid)
        is_min, ef_id = row[0], row[1]
        assert is_min is True
        assert ef_id != aid    # minimize() always yields a new automaton id

    def test_eigenform_language_correct(self, conn):
        aid = import_to_db(conn, dict(MINIMAL_DFA))
        row = _certify_eigenform(conn, aid)
        ef_id = row[1]
        eq_row = conn.execute(
            "SELECT equivalent FROM nerode.equivalent(%s, %s)",
            (aid, ef_id),
        ).fetchone()
        assert eq_row[0] is True


# ===========================================================================
# TestScanEigenforms
# nerode.scan_eigenforms() processes all DFAs in the database
# ===========================================================================

class TestScanEigenforms:

    def test_returns_rows(self, conn):
        aid1 = _from_regex(conn, "(ab)*")
        aid2 = _from_regex(conn, "a+")
        rows = conn.execute("SELECT * FROM nerode.scan_eigenforms()").fetchall()
        ids = [r[0] for r in rows]
        assert aid1 in ids
        assert aid2 in ids

    def test_from_regex_dfas_are_minimal(self, conn):
        aid1 = _from_regex(conn, "a*b*")
        aid2 = _from_regex(conn, "(a|b)*")
        rows = conn.execute("SELECT * FROM nerode.scan_eigenforms()").fetchall()
        for r in rows:
            automaton_id, eigenform_id, is_minimal, orig, mins, claim_id = r
            if automaton_id in (aid1, aid2):
                assert is_minimal is True

    def test_non_minimal_dfa_in_scan(self, conn):
        aid = import_to_db(conn, dict(NON_MINIMAL_DFA))
        rows = conn.execute("SELECT * FROM nerode.scan_eigenforms()").fetchall()
        row = next((r for r in rows if r[0] == aid), None)
        assert row is not None
        automaton_id, eigenform_id, is_minimal, orig, mins, claim_id = row
        assert is_minimal is False
        assert orig == 3
        assert mins == 2

    def test_scan_all_have_claim_ids(self, conn):
        aid1 = _from_regex(conn, "(ab)*")
        aid2 = import_to_db(conn, dict(NON_MINIMAL_DFA))
        rows = conn.execute("SELECT * FROM nerode.scan_eigenforms()").fetchall()
        for r in rows:
            automaton_id, eigenform_id, is_minimal, orig, mins, claim_id = r
            if automaton_id in (aid1, aid2):
                assert claim_id > 0

    def test_scan_cert_chain_complete(self, conn):
        """Every claim from scan_eigenforms has ≥1 cert.certificate + cert.witness."""
        _from_regex(conn, "a*")
        rows = conn.execute("SELECT * FROM nerode.scan_eigenforms()").fetchall()
        for r in rows:
            claim_id = r[5]
            cert_count = conn.execute(
                "SELECT count(*) FROM cert.certificate WHERE claim_id = %s",
                (claim_id,),
            ).fetchone()[0]
            assert cert_count >= 1

    def test_scan_idempotent(self, conn):
        """Calling scan_eigenforms twice in the same transaction must not error."""
        aid = _from_regex(conn, "(ab)*")
        conn.execute("SELECT * FROM nerode.scan_eigenforms()").fetchall()
        rows2 = conn.execute("SELECT * FROM nerode.scan_eigenforms()").fetchall()
        ids2 = [r[0] for r in rows2]
        assert aid in ids2

    def test_scan_minimal_state_count_matches(self, conn):
        """For each scanned DFA, minimal_states ≤ original_states."""
        _from_regex(conn, "a+")
        import_to_db(conn, dict(NON_MINIMAL_DFA))
        rows = conn.execute("SELECT * FROM nerode.scan_eigenforms()").fetchall()
        for r in rows:
            automaton_id, eigenform_id, is_minimal, orig, mins, claim_id = r
            assert mins <= orig, f"automaton {automaton_id}: mins={mins} > orig={orig}"


# ===========================================================================
# TestEigenformCertChain
# Full cert chain: claim → certificate → witness
# ===========================================================================

class TestEigenformCertChain:

    def test_full_chain_from_regex(self, conn):
        aid = _from_regex(conn, "(ab)+")
        row = _certify_eigenform(conn, aid)
        claim_id = row[4]

        # claim
        claim = conn.execute(
            "SELECT method, subject_kind FROM cert.claim WHERE id = %s",
            (claim_id,),
        ).fetchone()
        assert claim[0] == "nerode_eigenform"
        assert claim[1] == "nerode_automaton"

        # certificate
        cert = conn.execute(
            "SELECT id, status FROM cert.certificate WHERE claim_id = %s ORDER BY seq",
            (claim_id,),
        ).fetchone()
        assert cert[1] == "valid"

        # witness
        wit = conn.execute(
            "SELECT kind, body FROM cert.witness WHERE certificate_id = %s",
            (cert[0],),
        ).fetchone()
        assert wit[0] == "nerode_partition"
        assert "partition" in wit[1]
        assert "is_minimal" in wit[1]
        assert "eigenform_id" in wit[1]
        assert "original_states" in wit[1]
        assert "minimal_states" in wit[1]

    def test_full_chain_non_minimal(self, conn):
        aid = import_to_db(conn, dict(NON_MINIMAL_DFA))
        row = _certify_eigenform(conn, aid)
        claim_id = row[4]

        cert = conn.execute(
            "SELECT id, status FROM cert.certificate WHERE claim_id = %s",
            (claim_id,),
        ).fetchone()
        assert cert[1] == "valid"

        wit = conn.execute(
            "SELECT kind, body FROM cert.witness WHERE certificate_id = %s",
            (cert[0],),
        ).fetchone()
        assert wit[0] == "nerode_partition"
        assert wit[1]["is_minimal"] is False

    def test_certificate_status_valid(self, conn):
        aid = import_to_db(conn, dict(MINIMAL_DFA))
        row = _certify_eigenform(conn, aid)
        claim_id = row[4]
        status = conn.execute(
            "SELECT status FROM cert.certificate WHERE claim_id = %s LIMIT 1",
            (claim_id,),
        ).fetchone()[0]
        assert status == "valid"

    def test_automaton_marked_certified(self, conn):
        """certify_eigenform marks the automaton as certified in nerode.automata."""
        aid = _from_regex(conn, "a*")
        _certify_eigenform(conn, aid)
        row = conn.execute(
            "SELECT certified, cert_claim_id FROM nerode.automata WHERE id = %s",
            (aid,),
        ).fetchone()
        assert row[0] is True
        assert row[1] is not None

    def test_second_call_no_error(self, conn):
        """Calling certify_eigenform twice for the same automaton must not error."""
        aid = _from_regex(conn, "a+")
        row1 = _certify_eigenform(conn, aid)
        row2 = _certify_eigenform(conn, aid)
        # Both calls should return the same claim_id
        assert row1[4] == row2[4]
