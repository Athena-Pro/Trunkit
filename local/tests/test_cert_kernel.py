"""Tests for the untrusted-certificate kernel (calx.kernel).

These run with no database — they exercise the consumer-side checkers that
mirror src/calx/sql/94_cert_kernel.sql. The whole point of the cert_kernel
tier is that this independent checker is far simpler than the producer, so it
deserves direct, dependency-free tests.
"""
from __future__ import annotations

from fractions import Fraction

from calx.kernel import (
    check_crt,
    check_factorization,
    check_matrix_word,
    check_unit_fraction,
    verify_bundle,
    verify_witness,
)

# Standard SL(2,Z) generators (cf. arXiv:2604.15386 word/embedding setting).
_SL2 = {"a": [[1, 1], [0, 1]], "b": [[1, 0], [1, 1]]}

# ---- factorization -------------------------------------------------------

def test_factorization_28_is_perfect():
    w = {"schema": "factorization", "n": 28, "factors": [[2, 2], [7, 1]],
         "asserts": {"perfect": True, "sigma": 56}}
    ok, ev = check_factorization(w)
    assert ok is True
    assert ev["recomputed_sigma"] == 56
    assert ev["aliquot_sum"] == 28


def test_factorization_rejects_composite_base():
    # 4 is not prime; 28 = 4 * 7 would fool a naive sigma formula.
    w = {"schema": "factorization", "n": 28, "factors": [[4, 1], [7, 1]]}
    ok, ev = check_factorization(w)
    assert ok is False
    assert ev["all_bases_prime"] is False


def test_factorization_rejects_wrong_product():
    w = {"schema": "factorization", "n": 28, "factors": [[2, 2], [7, 2]]}  # = 196
    ok, _ = check_factorization(w)
    assert ok is False


def test_factorization_rejects_wrong_sigma_assert():
    w = {"schema": "factorization", "n": 28, "factors": [[2, 2], [7, 1]],
         "asserts": {"sigma": 99}}
    ok, _ = check_factorization(w)
    assert ok is False


def test_factorization_12_not_perfect():
    w = {"schema": "factorization", "n": 12, "factors": [[2, 2], [3, 1]],
         "asserts": {"perfect": False}}
    ok, ev = check_factorization(w)
    assert ok is True              # the claim "12 is not perfect" is correct
    assert ev["aliquot_sum"] == 16


def test_factorization_malformed_is_unverified():
    ok, _ = check_factorization({"schema": "factorization", "n": 28, "factors": []})
    assert ok is None


# ---- crt -----------------------------------------------------------------

def test_crt_valid():
    ok, ev = check_crt({"schema": "crt", "x": 8, "congruences": [[2, 3], [3, 5]]})
    assert ok is True
    assert ev["moduli_pairwise_coprime"] is True


def test_crt_wrong_x():
    ok, _ = check_crt({"schema": "crt", "x": 9, "congruences": [[2, 3], [3, 5]]})
    assert ok is False


def test_crt_non_coprime_moduli():
    ok, ev = check_crt({"schema": "crt", "x": 1, "congruences": [[1, 4], [1, 6]]})
    assert ok is False
    assert ev["moduli_pairwise_coprime"] is False


# ---- unit fraction (Egyptian fractions, arXiv:1606.02117) ----------------

# Sanity-anchor the literature decomposition with an independent exact sum, so
# the kernel test cannot silently pass on a wrong identity.
_ODD_NINE = [3, 5, 7, 9, 11, 15, 35, 45, 231]


def test_unit_fraction_literature_identity_actually_sums_to_one():
    assert sum((Fraction(1, d) for d in _ODD_NINE), Fraction(0)) == 1


def test_unit_fraction_accepts_distinct_odd_decomposition_of_one():
    ok, ev = check_unit_fraction({
        "schema": "unit_fraction", "target": 1, "denominators": _ODD_NINE,
        "constraints": {"distinct": True, "odd": True},
    })
    assert ok is True
    assert ev["all_odd"] and ev["all_distinct"]


def test_unit_fraction_rejects_wrong_sum():
    ok, _ = check_unit_fraction({"schema": "unit_fraction", "target": 1,
                                 "denominators": [2, 3, 7]})  # = 41/42
    assert ok is False


def test_unit_fraction_rejects_even_under_odd_constraint():
    ok, ev = check_unit_fraction({"schema": "unit_fraction", "target": 1,
                                  "denominators": [2, 3, 6],  # sums to 1 but 2,6 even
                                  "constraints": {"odd": True}})
    assert ok is False
    assert ev["sum_equals_target"] is True and ev["all_odd"] is False


def test_unit_fraction_rejects_repeat_under_distinct_constraint():
    ok, ev = check_unit_fraction({"schema": "unit_fraction", "target": 1,
                                  "denominators": [2, 2],  # sums to 1 but not distinct
                                  "constraints": {"distinct": True}})
    assert ok is False
    assert ev["all_distinct"] is False


def test_unit_fraction_malformed_is_unverified():
    ok, _ = check_unit_fraction({"schema": "unit_fraction", "target": 1, "denominators": []})
    assert ok is None


# ---- matrix word (matrix-semigroup membership, arXiv:2604.15386) ----------

def test_matrix_word_accepts_correct_product():
    # a*b*a over SL(2,Z): [[1,1],[0,1]]·[[1,0],[1,1]]·[[1,1],[0,1]] = [[2,3],[1,2]]
    ok, ev = check_matrix_word({"schema": "matrix_word", "generators": _SL2,
                                "word": ["a", "b", "a"], "target": [[2, 3], [1, 2]]})
    assert ok is True
    assert ev["recomputed_product"] == [[2, 3], [1, 2]]
    assert ev["word_length"] == 3


def test_matrix_word_rejects_wrong_target():
    ok, _ = check_matrix_word({"schema": "matrix_word", "generators": _SL2,
                               "word": ["a", "b"], "target": [[1, 0], [0, 1]]})
    assert ok is False


def test_matrix_word_order_matters():
    # a*b = [[2,1],[1,1]] but b*a = [[1,1],[1,2]] — non-commutative
    ab, _ = check_matrix_word({"schema": "matrix_word", "generators": _SL2,
                               "word": ["a", "b"], "target": [[2, 1], [1, 1]]})
    ba, _ = check_matrix_word({"schema": "matrix_word", "generators": _SL2,
                               "word": ["b", "a"], "target": [[2, 1], [1, 1]]})
    assert ab is True and ba is False


def test_matrix_word_undefined_generator_is_unverified():
    ok, _ = check_matrix_word({"schema": "matrix_word", "generators": _SL2,
                               "word": ["a", "z"], "target": [[1, 0], [0, 1]]})
    assert ok is None


def test_matrix_word_empty_word_is_unverified():
    ok, _ = check_matrix_word({"schema": "matrix_word", "generators": _SL2,
                               "word": [], "target": [[1, 0], [0, 1]]})
    assert ok is None


# ---- dispatch + bundle ---------------------------------------------------

def test_unknown_schema_is_unverified():
    ok, _ = verify_witness({"schema": "no_such_kernel"})
    assert ok is None


def test_verify_bundle_flags_agreement_and_mismatch():
    bundle = {
        "claims": [
            {  # ledger and kernel agree
                "claim": {"id": 1, "statement": "28 perfect"},
                "certificate": {"status": "valid"},
                "witness": {"kind": "term", "body": {
                    "schema": "factorization", "n": 28, "factors": [[2, 2], [7, 1]],
                    "asserts": {"perfect": True}}},
            },
            {  # ledger LIES: says valid, kernel refutes
                "claim": {"id": 2, "statement": "bogus"},
                "certificate": {"status": "valid"},
                "witness": {"kind": "term", "body": {
                    "schema": "factorization", "n": 28, "factors": [[3, 3]]}},
            },
            {  # comp_sql claim: not checkable offline
                "claim": {"id": 3, "statement": "needs db"},
                "certificate": {"status": "valid"},
                "witness": None,
            },
        ]
    }
    results = verify_bundle(bundle)
    by_id = {r["claim_id"]: r for r in results}
    assert by_id[1]["independent_verdict"] == "valid"
    assert by_id[1]["agrees_with_ledger"] is True
    assert by_id[2]["independent_verdict"] == "refuted"
    assert by_id[2]["agrees_with_ledger"] is False   # caught a lying ledger
    assert by_id[3]["checkable"] is False
