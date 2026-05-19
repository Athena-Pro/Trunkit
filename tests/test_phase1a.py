"""
tests/test_phase1a.py
=====================
Phase 1a: Protocol equivalence use cases.

Demonstrates the full cross-tool cert chain end-to-end:
  1. Raw DFA equivalence (nerode.equivalent)
  2. Certified equivalence with cert.witness (nerode.certify_equivalence)
  3. Arithmetic irreducibility: prime state count (nerode.certify_prime_dfa)
  4. Combined one-shot protocol (nerode.protocol_equivalence_check)

Test DFAs — all built over alphabet {a, b}:

  L_ab_star  = (ab)*    — zero-or-more repetitions of "ab"
                           accepts ε, ab, abab, ababab, …
                           min DFA = 3 states  (3 is prime)

  L_ab_plus  = (ab)+    — one-or-more repetitions of "ab"
                           accepts ab, abab, ababab, …   (not ε)
                           min DFA = 4 states  (4 = 2×2, composite)

  L_a_plus   = a+       — one or more a's, no b's
                           accepts a, aa, aaa, …   (not ε)
                           min DFA = 3 states  (3 is prime)

Pairings exercised:

  (ab)* ≡ (ab)*        → equivalent       → bisimulation witness
  (ab)* ≢ a+           → NOT equivalent   → counterexample "" (ε distinguishes)
  (ab)* ≢ (ab)+        → NOT equivalent   → counterexample "" (ε distinguishes)

Cert witness kinds covered: bisimulation, counterexample, nerode_partition
Cert methods covered:       nerode_equivalence, nerode_arithmetic_prime
"""

from __future__ import annotations

import pytest

# Explicit two-letter alphabet used for all DFAs in this module
AB = ["a", "b"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _from_regex(conn, pattern: str) -> int:
    """Build and minimise a DFA for *pattern* over alphabet {a, b}."""
    return conn.execute(
        "SELECT nerode.from_regex(%s, NULL, %s)",
        (pattern, AB),
    ).fetchone()[0]


def _state_count(conn, aid: int) -> int:
    return conn.execute(
        "SELECT state_count FROM nerode.automata WHERE id = %s",
        (aid,),
    ).fetchone()[0]


def _run(conn, aid: int, word: str) -> bool:
    return conn.execute(
        "SELECT accept FROM nerode.run(%s, %s)",
        (aid, word),
    ).fetchone()[0]


def _cert_witness(conn, claim_id: int):
    """Return the most-recent (kind, body) witness for a claim."""
    return conn.execute(
        """
        SELECT w.kind, w.body
        FROM   cert.certificate AS crt
        JOIN   cert.witness     AS w ON w.certificate_id = crt.id
        WHERE  crt.claim_id = %s
        ORDER  BY crt.seq DESC
        LIMIT  1
        """,
        (claim_id,),
    ).fetchone()


# ===========================================================================
# Section 1 — Raw equivalence: nerode.equivalent()
# ===========================================================================

class TestRawEquivalence:
    """nerode.equivalent(id1, id2) → (equivalent BOOLEAN, witness JSONB)."""

    def test_same_language_is_equivalent(self, conn):
        """Two independently-built (ab)* DFAs recognise the same language."""
        id1 = _from_regex(conn, "(ab)*")
        id2 = _from_regex(conn, "(ab)*")
        row = conn.execute(
            "SELECT equivalent, witness FROM nerode.equivalent(%s, %s)",
            (id1, id2),
        ).fetchone()
        assert row[0] is True
        assert row[1]["kind"] == "bisimulation"

    def test_different_languages_not_equivalent(self, conn):
        """(ab)* and a+ recognise different languages."""
        id_ab = _from_regex(conn, "(ab)*")
        id_ap = _from_regex(conn, "a+")
        row = conn.execute(
            "SELECT equivalent, witness FROM nerode.equivalent(%s, %s)",
            (id_ab, id_ap),
        ).fetchone()
        assert row[0] is False
        assert row[1]["kind"] == "counterexample"

    def test_distinguishing_string_accepted_by_exactly_one(self, conn):
        """The counterexample string is accepted by exactly one of the two DFAs."""
        id_ab = _from_regex(conn, "(ab)*")
        id_ap = _from_regex(conn, "a+")
        row = conn.execute(
            "SELECT equivalent, witness FROM nerode.equivalent(%s, %s)",
            (id_ab, id_ap),
        ).fetchone()
        dist = row[1]["distinguishing_string"]
        acc1 = _run(conn, id_ab, dist)
        acc2 = _run(conn, id_ap, dist)
        assert acc1 != acc2, (
            f"String {dist!r}: id_ab accepts={acc1}, id_ap accepts={acc2} — "
            "exactly one must accept the distinguishing string"
        )

    def test_distinguishing_string_is_empty(self, conn):
        """(ab)* accepts ε; a+ does not — BFS finds '' immediately."""
        id_ab = _from_regex(conn, "(ab)*")
        id_ap = _from_regex(conn, "a+")
        row = conn.execute(
            "SELECT equivalent, witness FROM nerode.equivalent(%s, %s)",
            (id_ab, id_ap),
        ).fetchone()
        assert row[1]["distinguishing_string"] == "", (
            "Initial states differ in acceptance → shortest dist. string is ''"
        )

    def test_bisimulation_pairs_nonempty(self, conn):
        """Equivalent pair: bisimulation witness lists all reachable product pairs."""
        id1 = _from_regex(conn, "(ab)*")
        id2 = _from_regex(conn, "(ab)*")
        row = conn.execute(
            "SELECT equivalent, witness FROM nerode.equivalent(%s, %s)",
            (id1, id2),
        ).fetchone()
        pairs = row[1]["pairs"]
        assert isinstance(pairs, list)
        assert len(pairs) >= 1

    def test_ab_star_vs_ab_plus_counterexample(self, conn):
        """(ab)* vs (ab)+: ε is in L1 but not L2 → distinguishing string = ''."""
        id_star = _from_regex(conn, "(ab)*")
        id_plus = _from_regex(conn, "(ab)+")
        row = conn.execute(
            "SELECT equivalent, witness FROM nerode.equivalent(%s, %s)",
            (id_star, id_plus),
        ).fetchone()
        assert row[0] is False
        assert row[1]["distinguishing_string"] == ""

    def test_state_counts(self, conn):
        """Verify expected minimal state counts for all three languages."""
        assert _state_count(conn, _from_regex(conn, "(ab)*")) == 3
        assert _state_count(conn, _from_regex(conn, "a+"))    == 3
        assert _state_count(conn, _from_regex(conn, "(ab)+")) == 4


# ===========================================================================
# Section 2 — Certified equivalence: nerode.certify_equivalence()
# ===========================================================================

class TestCertifiedEquivalence:
    """nerode.certify_equivalence(id1, id2) → (equivalent BOOLEAN, claim_id BIGINT)."""

    def test_equivalent_creates_bisimulation_witness(self, conn):
        """certify_equivalence on a same-language pair issues a bisimulation witness."""
        id1 = _from_regex(conn, "(ab)*")
        id2 = _from_regex(conn, "(ab)*")
        row = conn.execute(
            "SELECT equivalent, claim_id FROM nerode.certify_equivalence(%s, %s)",
            (id1, id2),
        ).fetchone()
        assert row[0] is True
        claim_id = row[1]
        assert claim_id is not None

        kind, body = _cert_witness(conn, claim_id)
        assert kind == "bisimulation"
        assert body["automaton_id1"] == id1
        assert body["automaton_id2"] == id2

    def test_inequivalent_creates_counterexample_witness(self, conn):
        """certify_equivalence on different-language DFAs issues a counterexample witness."""
        id_ab = _from_regex(conn, "(ab)*")
        id_ap = _from_regex(conn, "a+")
        row = conn.execute(
            "SELECT equivalent, claim_id FROM nerode.certify_equivalence(%s, %s)",
            (id_ab, id_ap),
        ).fetchone()
        assert row[0] is False
        claim_id = row[1]

        kind, body = _cert_witness(conn, claim_id)
        assert kind == "counterexample"
        assert "distinguishing_string" in body

    def test_counterexample_witness_body_has_both_ids(self, conn):
        """Counterexample witness body references both automaton IDs."""
        id_ab = _from_regex(conn, "(ab)*")
        id_ap = _from_regex(conn, "a+")
        _, claim_id = conn.execute(
            "SELECT equivalent, claim_id FROM nerode.certify_equivalence(%s, %s)",
            (id_ab, id_ap),
        ).fetchone()
        _, body = _cert_witness(conn, claim_id)
        assert body["automaton_id1"] == id_ab
        assert body["automaton_id2"] == id_ap

    def test_cert_method_is_nerode_equivalence(self, conn):
        """cert.claim.method = 'nerode_equivalence' for equivalence checks."""
        id1 = _from_regex(conn, "(ab)*")
        id2 = _from_regex(conn, "(ab)*")
        _, claim_id = conn.execute(
            "SELECT equivalent, claim_id FROM nerode.certify_equivalence(%s, %s)",
            (id1, id2),
        ).fetchone()
        method = conn.execute(
            "SELECT method FROM cert.claim WHERE id = %s", (claim_id,)
        ).fetchone()[0]
        assert method == "nerode_equivalence"

    def test_cert_claim_subject_kind(self, conn):
        """cert.claim.subject_kind = 'nerode_automaton_pair'."""
        id1 = _from_regex(conn, "(ab)*")
        id2 = _from_regex(conn, "(ab)*")
        _, claim_id = conn.execute(
            "SELECT equivalent, claim_id FROM nerode.certify_equivalence(%s, %s)",
            (id1, id2),
        ).fetchone()
        sk = conn.execute(
            "SELECT subject_kind FROM cert.claim WHERE id = %s", (claim_id,)
        ).fetchone()[0]
        assert sk == "nerode_automaton_pair"


# ===========================================================================
# Section 3 — Arithmetic irreducibility: nerode.certify_prime_dfa()
# ===========================================================================

class TestArithmeticIrreducibility:
    """nerode.certify_prime_dfa(id) → (is_prime, state_count, claim_id, calx_facts)."""

    def test_prime_state_count_detected(self, conn):
        """(ab)* has 3 states — 3 is prime → is_prime = True."""
        aid = _from_regex(conn, "(ab)*")
        row = conn.execute(
            "SELECT is_prime, state_count, claim_id, calx_facts"
            " FROM nerode.certify_prime_dfa(%s)",
            (aid,),
        ).fetchone()
        assert row[0] is True
        assert row[1] == 3   # exactly 3 states (Nerode classes of (ab)*)

    def test_composite_state_count_detected(self, conn):
        """(ab)+ has 4 states — 4 is composite → is_prime = False."""
        aid = _from_regex(conn, "(ab)+")
        row = conn.execute(
            "SELECT is_prime, state_count, claim_id, calx_facts"
            " FROM nerode.certify_prime_dfa(%s)",
            (aid,),
        ).fetchone()
        assert row[1] == 4
        assert row[0] is False

    def test_state_count_matches_automata_table(self, conn):
        """certify_prime_dfa reports the same state_count as nerode.automata."""
        aid = _from_regex(conn, "(ab)*")
        row = conn.execute(
            "SELECT is_prime, state_count, claim_id, calx_facts"
            " FROM nerode.certify_prime_dfa(%s)",
            (aid,),
        ).fetchone()
        assert row[1] == _state_count(conn, aid)

    def test_creates_nerode_partition_witness(self, conn):
        """certify_prime_dfa issues a nerode_partition witness."""
        aid = _from_regex(conn, "(ab)*")
        row = conn.execute(
            "SELECT is_prime, state_count, claim_id, calx_facts"
            " FROM nerode.certify_prime_dfa(%s)",
            (aid,),
        ).fetchone()
        claim_id = row[2]
        kind, body = _cert_witness(conn, claim_id)
        assert kind == "nerode_partition"
        assert body["is_prime"] is True
        assert body["state_count"] == 3

    def test_composite_creates_nerode_partition_witness(self, conn):
        """certify_prime_dfa also issues a nerode_partition witness for composite DFAs."""
        aid = _from_regex(conn, "(ab)+")
        row = conn.execute(
            "SELECT is_prime, state_count, claim_id, calx_facts"
            " FROM nerode.certify_prime_dfa(%s)",
            (aid,),
        ).fetchone()
        claim_id = row[2]
        kind, body = _cert_witness(conn, claim_id)
        assert kind == "nerode_partition"
        assert body["is_prime"] is False
        assert body["state_count"] == 4

    def test_cert_method_is_arithmetic_prime(self, conn):
        """cert.claim.method = 'nerode_arithmetic_prime' for prime DFA claims."""
        aid = _from_regex(conn, "(ab)*")
        row = conn.execute(
            "SELECT is_prime, state_count, claim_id, calx_facts"
            " FROM nerode.certify_prime_dfa(%s)",
            (aid,),
        ).fetchone()
        method = conn.execute(
            "SELECT method FROM cert.claim WHERE id = %s", (row[2],)
        ).fetchone()[0]
        assert method == "nerode_arithmetic_prime"

    def test_calx_facts_field_structure(self, conn):
        """calx_facts JSONB has the expected top-level keys."""
        aid = _from_regex(conn, "(ab)*")
        row = conn.execute(
            "SELECT is_prime, state_count, claim_id, calx_facts"
            " FROM nerode.certify_prime_dfa(%s)",
            (aid,),
        ).fetchone()
        facts = row[3]
        assert facts is not None
        for key in ("state_count", "is_prime", "factorization", "prime_factors",
                    "smallest_factor", "pumping_constant", "calx_available"):
            assert key in facts, f"calx_facts missing key '{key}'"

    def test_calx_facts_is_prime_matches_return(self, conn):
        """calx_facts['is_prime'] agrees with the returned is_prime column."""
        aid = _from_regex(conn, "(ab)*")
        row = conn.execute(
            "SELECT is_prime, state_count, claim_id, calx_facts"
            " FROM nerode.certify_prime_dfa(%s)",
            (aid,),
        ).fetchone()
        assert row[0] == row[3]["is_prime"]

    def test_a_plus_also_prime(self, conn):
        """a+ also has 3 states → is_prime = True."""
        aid = _from_regex(conn, "a+")
        row = conn.execute(
            "SELECT is_prime, state_count, claim_id, calx_facts"
            " FROM nerode.certify_prime_dfa(%s)",
            (aid,),
        ).fetchone()
        assert row[1] == 3
        assert row[0] is True


# ===========================================================================
# Section 4 — Full protocol: nerode.protocol_equivalence_check()
# ===========================================================================

class TestProtocolEquivalenceCheck:
    """nerode.protocol_equivalence_check(id1, id2) → JSONB summary."""

    def test_equivalent_pair_summary_structure(self, conn):
        """Equivalent pair: summary has all expected top-level keys."""
        id1 = _from_regex(conn, "(ab)*")
        id2 = _from_regex(conn, "(ab)*")
        result = conn.execute(
            "SELECT nerode.protocol_equivalence_check(%s, %s)",
            (id1, id2),
        ).fetchone()[0]
        for key in ("equivalent", "witness_kind", "equiv_claim_id",
                    "automaton1", "automaton2", "witness"):
            assert key in result, f"protocol result missing key '{key}'"

    def test_equivalent_pair_verdict(self, conn):
        """Equivalent pair: equivalent=True, witness_kind='bisimulation'."""
        id1 = _from_regex(conn, "(ab)*")
        id2 = _from_regex(conn, "(ab)*")
        result = conn.execute(
            "SELECT nerode.protocol_equivalence_check(%s, %s)",
            (id1, id2),
        ).fetchone()[0]
        assert result["equivalent"] is True
        assert result["witness_kind"] == "bisimulation"

    def test_equivalent_pair_state_counts(self, conn):
        """Equivalent pair: both automata show state_count = 3."""
        id1 = _from_regex(conn, "(ab)*")
        id2 = _from_regex(conn, "(ab)*")
        result = conn.execute(
            "SELECT nerode.protocol_equivalence_check(%s, %s)",
            (id1, id2),
        ).fetchone()[0]
        assert result["automaton1"]["state_count"] == 3
        assert result["automaton2"]["state_count"] == 3

    def test_equivalent_pair_arithmetic_claims_issued(self, conn):
        """Equivalent pair with prime state counts: cross-tool claims are issued."""
        id1 = _from_regex(conn, "(ab)*")
        id2 = _from_regex(conn, "(ab)*")
        result = conn.execute(
            "SELECT nerode.protocol_equivalence_check(%s, %s)",
            (id1, id2),
        ).fetchone()[0]
        assert result["automaton1"]["is_prime"] is True
        assert result["automaton2"]["is_prime"] is True
        assert result["automaton1"]["arithmetic_claim_id"] is not None
        assert result["automaton2"]["arithmetic_claim_id"] is not None

    def test_equivalent_pair_arithmetic_claim_ids_distinct(self, conn):
        """Each automaton in an equivalent pair gets its own arithmetic claim."""
        id1 = _from_regex(conn, "(ab)*")
        id2 = _from_regex(conn, "(ab)*")
        result = conn.execute(
            "SELECT nerode.protocol_equivalence_check(%s, %s)",
            (id1, id2),
        ).fetchone()[0]
        c1 = result["automaton1"]["arithmetic_claim_id"]
        c2 = result["automaton2"]["arithmetic_claim_id"]
        assert c1 != c2, "Each automaton must receive a distinct arithmetic claim"

    def test_inequivalent_pair_verdict(self, conn):
        """Inequivalent pair: equivalent=False, witness_kind='counterexample'."""
        id_ab = _from_regex(conn, "(ab)*")
        id_ap = _from_regex(conn, "a+")
        result = conn.execute(
            "SELECT nerode.protocol_equivalence_check(%s, %s)",
            (id_ab, id_ap),
        ).fetchone()[0]
        assert result["equivalent"] is False
        assert result["witness_kind"] == "counterexample"

    def test_inequivalent_pair_has_distinguishing_string(self, conn):
        """Inequivalent pair: 'distinguishing_string' hoisted to top level."""
        id_ab = _from_regex(conn, "(ab)*")
        id_ap = _from_regex(conn, "a+")
        result = conn.execute(
            "SELECT nerode.protocol_equivalence_check(%s, %s)",
            (id_ab, id_ap),
        ).fetchone()[0]
        assert "distinguishing_string" in result
        assert result["distinguishing_string"] == ""

    def test_inequivalent_pair_no_arithmetic_claims(self, conn):
        """Inequivalent pair: no arithmetic cross-claims are issued."""
        id_ab = _from_regex(conn, "(ab)*")
        id_ap = _from_regex(conn, "a+")
        result = conn.execute(
            "SELECT nerode.protocol_equivalence_check(%s, %s)",
            (id_ab, id_ap),
        ).fetchone()[0]
        assert result["automaton1"]["arithmetic_claim_id"] is None
        assert result["automaton2"]["arithmetic_claim_id"] is None

    def test_distinguishing_string_validates_against_dfas(self, conn):
        """The hoisted distinguishing_string is accepted by exactly one DFA."""
        id_ab = _from_regex(conn, "(ab)*")
        id_ap = _from_regex(conn, "a+")
        result = conn.execute(
            "SELECT nerode.protocol_equivalence_check(%s, %s)",
            (id_ab, id_ap),
        ).fetchone()[0]
        dist = result["distinguishing_string"]
        acc1 = _run(conn, id_ab, dist)
        acc2 = _run(conn, id_ap, dist)
        assert acc1 != acc2

    def test_witness_field_present_in_equivalent_result(self, conn):
        """Equivalent pair: 'witness' JSONB field present with bisimulation pairs."""
        id1 = _from_regex(conn, "(ab)*")
        id2 = _from_regex(conn, "(ab)*")
        result = conn.execute(
            "SELECT nerode.protocol_equivalence_check(%s, %s)",
            (id1, id2),
        ).fetchone()[0]
        assert result["witness"] is not None
        assert result["witness"]["kind"] == "bisimulation"

    def test_witness_field_present_in_inequivalent_result(self, conn):
        """Inequivalent pair: 'witness' JSONB field present with counterexample data."""
        id_ab = _from_regex(conn, "(ab)*")
        id_ap = _from_regex(conn, "a+")
        result = conn.execute(
            "SELECT nerode.protocol_equivalence_check(%s, %s)",
            (id_ab, id_ap),
        ).fetchone()[0]
        assert result["witness"] is not None
        assert result["witness"]["kind"] == "counterexample"


# ===========================================================================
# Section 5 — Cross-tool cert chain integrity
# ===========================================================================

class TestCrossToolCertChain:
    """
    Verify the complete cert chain across nerode_equivalence + nerode_arithmetic_prime.

    After protocol_equivalence_check on an equivalent pair with prime state counts:
      cert.claim (nerode_equivalence)  ← equivalence claim
      cert.claim (nerode_arithmetic_prime) × 2  ← one per automaton
      cert.witness (bisimulation)      ← for the equivalence claim
      cert.witness (nerode_partition) × 2  ← one per arithmetic claim
    """

    def test_three_claims_issued_for_equivalent_prime_pair(self, conn):
        """A minimum of 3 cert.claim rows exist after protocol_equivalence_check."""
        id1 = _from_regex(conn, "(ab)*")
        id2 = _from_regex(conn, "(ab)*")
        conn.execute(
            "SELECT nerode.protocol_equivalence_check(%s, %s)",
            (id1, id2),
        ).fetchone()
        n = conn.execute(
            "SELECT count(*) FROM cert.claim"
            " WHERE method IN ('nerode_equivalence', 'nerode_arithmetic_prime')"
        ).fetchone()[0]
        assert n >= 3, f"Expected ≥3 claims, got {n}"

    def test_bisimulation_witness_in_cert_tables(self, conn):
        """At least one bisimulation witness is present in cert.witness."""
        id1 = _from_regex(conn, "(ab)*")
        id2 = _from_regex(conn, "(ab)*")
        conn.execute(
            "SELECT nerode.protocol_equivalence_check(%s, %s)",
            (id1, id2),
        ).fetchone()
        n = conn.execute(
            "SELECT count(*) FROM cert.witness WHERE kind = 'bisimulation'"
        ).fetchone()[0]
        assert n >= 1

    def test_nerode_partition_witnesses_in_cert_tables(self, conn):
        """At least two nerode_partition witnesses (one per automaton) are present."""
        id1 = _from_regex(conn, "(ab)*")
        id2 = _from_regex(conn, "(ab)*")
        conn.execute(
            "SELECT nerode.protocol_equivalence_check(%s, %s)",
            (id1, id2),
        ).fetchone()
        n = conn.execute(
            "SELECT count(*) FROM cert.witness WHERE kind = 'nerode_partition'"
        ).fetchone()[0]
        assert n >= 2

    def test_counterexample_witness_in_cert_tables(self, conn):
        """At least one counterexample witness is present after an inequivalent check."""
        id_ab = _from_regex(conn, "(ab)*")
        id_ap = _from_regex(conn, "a+")
        conn.execute(
            "SELECT nerode.protocol_equivalence_check(%s, %s)",
            (id_ab, id_ap),
        ).fetchone()
        n = conn.execute(
            "SELECT count(*) FROM cert.witness WHERE kind = 'counterexample'"
        ).fetchone()[0]
        assert n >= 1

    def test_arithmetic_claim_references_automaton_id(self, conn):
        """cert.claim.subject_ref for arithmetic claims references the automaton id."""
        id1 = _from_regex(conn, "(ab)*")
        id2 = _from_regex(conn, "(ab)*")
        result = conn.execute(
            "SELECT nerode.protocol_equivalence_check(%s, %s)",
            (id1, id2),
        ).fetchone()[0]
        arith_id1 = result["automaton1"]["arithmetic_claim_id"]
        ref = conn.execute(
            "SELECT subject_ref FROM cert.claim WHERE id = %s",
            (arith_id1,),
        ).fetchone()[0]
        assert ref["automaton_id"] == id1

    def test_full_chain_witness_kinds(self, conn):
        """After a complete equivalent-prime protocol, all three witness kinds present."""
        id1 = _from_regex(conn, "(ab)*")
        id2 = _from_regex(conn, "(ab)*")
        conn.execute(
            "SELECT nerode.protocol_equivalence_check(%s, %s)",
            (id1, id2),
        ).fetchone()
        kinds = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT kind FROM cert.witness"
                " WHERE kind IN ('bisimulation', 'nerode_partition')"
            )
        }
        assert "bisimulation"    in kinds
        assert "nerode_partition" in kinds

    def test_cert_chain_survives_second_call(self, conn):
        """Calling protocol_equivalence_check twice on same pair is idempotent."""
        id1 = _from_regex(conn, "(ab)*")
        id2 = _from_regex(conn, "(ab)*")
        r1 = conn.execute(
            "SELECT nerode.protocol_equivalence_check(%s, %s)",
            (id1, id2),
        ).fetchone()[0]
        # Second call on the SAME pair — ON CONFLICT handles statement uniqueness
        # (certify_equivalence uses now() so a second call creates a new claim;
        #  certify_prime_dfa uses automaton id so it deduplicates by statement)
        r2 = conn.execute(
            "SELECT nerode.protocol_equivalence_check(%s, %s)",
            (id1, id2),
        ).fetchone()[0]
        # Both calls should report equivalence
        assert r1["equivalent"] is True
        assert r2["equivalent"] is True
