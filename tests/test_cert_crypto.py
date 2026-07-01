"""
tests/test_cert_crypto.py
=========================
Cryptographic-tier (crypto_succinct) arithmetisation tests — DB-free.

Exercises calx.arith (the in-repo basis for src/calx/sql/97_cert_crypto.sql,
driven by tools/cert_crypto.py), implementing Gabbay arXiv:2606.23768:

  * faithfulness  : the polynomial semantics reproduces the paper's examples
  * security      : witness-carrying negation/≤/divisibility resist forgery, and
                    the non-negativity invariant is load-bearing
  * mixed char.   : CRT certification on calx primitives + cross-cert with kernel
  * composition   : the bundle-admission invariant (I1 register disjointness,
                    I2 classification consistency)

No database required: calx.arith and calx.kernel are dependency-free.
"""
from __future__ import annotations

from fractions import Fraction

from calx.arith import (
    Add,
    And,
    Bool,
    Const,
    Divides,
    Eq,
    Exists,
    Forall,
    Implies,
    Interp,
    LeqZ,
    Lookup,
    Mul,
    Not,
    Or,
    Var,
    bundle_admits,
    evaluate,
    four_squares,
    modular_certify,
    prime_basis,
    soundness_error_primes,
    unit_mod,
    verdict,
)
from calx.kernel import check_factorization


# --- faithfulness (Theorem 2.9 / Figure 3) --------------------------------- #
def test_truth_and_falsehood():
    assert evaluate(Eq(Const(0), Const(0))) == 0          # true
    assert evaluate(Eq(Const(0), Const(1))) == 1          # false
    assert evaluate(Eq(Const(3), Const(5))) == 4          # (3-5)^2


def test_connectives_are_sum_and_product():
    T, F = Eq(Const(0), Const(0)), Eq(Const(0), Const(1))
    assert verdict(And(T, T)) == "VALID"
    assert verdict(And(T, F)) == "REFUTED"                # sum
    assert verdict(Or(T, F)) == "VALID"
    assert verdict(Or(F, F)) == "REFUTED"                 # product


def test_chi_pow_proof_carrying_certificate():
    # rows: 1=a, 2=b, 3=a^b, 4=recursive pointer (reconstructed from prose).
    X = Var()
    def L(i, t=X): return Lookup("pow", i, t)
    rec = L(4)
    BC = And(Eq(L(2), Const(0)), Eq(L(3), Const(1)))
    IC = And(Eq(Lookup("pow", 1, rec), L(1)),
         And(Eq(Lookup("pow", 2, rec), Add(L(2), Const(-1))),
             Eq(L(3), Mul(L(1), Lookup("pow", 3, rec)))))
    from calx.arith import Gt, Len, Lt
    range_ok = And(Lt(Const(0), "pow", 4), Gt("pow", 4, Add(Len("pow"), Const(1))))
    chi = And(range_ok, Forall("pow", Or(BC, IC)))
    good = Interp({"pow": [[2, 2, 2], [0, 1, 2], [1, 2, 4], [1, 1, 2]]})
    bad = Interp({"pow": [[2], [2], [4], [2]]})           # pointer out of range
    assert verdict(chi, good) == "VALID"
    assert verdict(chi, bad) == "REFUTED"


# --- security of witness-carrying extensions ------------------------------- #
def test_secure_negation_complete_and_sound():
    false_phi = Eq(Const(2), Const(5))                    # [phi]=9
    assert verdict(Not(false_phi, Const(Fraction(1, 9)))) == "VALID"   # ¬false
    true_phi = Eq(Const(2), Const(2))                     # [phi]=0
    # no inverse can forge ¬(true): (0*w-1)^2 = 1 for every w
    assert all(evaluate(Not(true_phi, Const(Fraction(w, 7)))) == 1
               for w in range(-20, 21))


def test_non_negativity_is_load_bearing():
    # A false claim must not be cancellable inside a conjunction.
    A_false = Eq(Const(2), Const(5))                      # [A]=9
    B_false = And(Eq(Const(0), Const(3)), Eq(Const(0), Const(1)))  # [B]=10
    secure = And(A_false, Not(B_false, Const(Fraction(1, 10))))
    assert evaluate(secure) >= 9                          # ¬B is a square >=0
    assert verdict(secure) == "REFUTED"


def test_order_and_divisibility_witnesses():
    w = four_squares(5 - 3)
    leq = LeqZ(Const(3), Const(5), *[Const(v) for v in w])
    assert verdict(leq) == "VALID"
    # 5 <= 3 is unforgeable: a sum of squares can't be -2
    assert all(evaluate(LeqZ(Const(5), Const(3),
               Const(a), Const(b), Const(c), Const(d))) > 0
               for a in range(4) for b in range(4)
               for c in range(4) for d in range(4))
    assert verdict(Divides(Const(7), Const(28), Const(4))) == "VALID"
    assert all(evaluate(Divides(Const(7), Const(30), Const(q))) > 0
               for q in range(50))


def test_implication_and_bool_and_exists():
    assert verdict(Implies(Eq(Const(2), Const(2)), Eq(Const(3), Const(3)),
                           Const(1))) == "VALID"
    assert verdict(Bool(Const(1))) == "VALID"
    assert verdict(Bool(Const(2))) == "REFUTED"
    hit = Interp({"r": [[3, 7, 5]]})
    assert verdict(Exists("r", Eq(Lookup("r", 1, Var()), Const(7))), hit) == "VALID"


# --- mixed characteristic on calx primitives ------------------------------- #
def test_modular_crt_soundness_and_budget():
    true_pred = Eq(Const(56 - 28), Const(28))             # 28 perfect: sigma-n=n
    st, info = modular_certify(true_pred, None, [7, 11, 13])
    assert st == "valid" and info["crt_kernel_ok"]
    false_pred = Eq(Const(30), Const(0))                  # residual 900
    st_bad, info_bad = modular_certify(false_pred, None, [2, 3, 5])  # under budget
    assert st_bad == "valid?" and not info_bad["budget_M_gt_r"]
    assert soundness_error_primes(900, [2, 3, 5]) == [2, 3, 5]
    st_ok, _ = modular_certify(false_pred, None, [7, 11, 13])        # M>r, sound
    assert st_ok == "refuted"


def test_padic_unit_witness_existence():
    assert prime_basis(5) == [2, 3, 5, 7, 11]
    assert not unit_mod(12, 2) and not unit_mod(12, 3)   # 2,3 | 12
    assert unit_mod(12, 5) and unit_mod(12, 7)


def test_cross_certification_with_calx_kernel():
    arith_perfect = verdict(Eq(Const(56 - 28), Const(28))) == "VALID"
    calx_perfect = check_factorization(
        {"factors": [[2, 2], [7, 1]], "n": 28, "asserts": {"perfect": True}})[0]
    assert arith_perfect and calx_perfect is True


# --- composition security: bundle-admission invariant ---------------------- #
def test_bundle_admission_I1_I2():
    admit, _ = bundle_admits([
        {"name": "A", "private": {"inv_a"}, "public": {"D"}},
        {"name": "B", "private": {"s1", "s2"}, "public": {"D"}}])
    assert admit                                          # disjoint + shared public
    admit, viol = bundle_admits([
        {"name": "A", "private": {"inv"}, "public": set()},
        {"name": "B", "private": {"inv"}, "public": set()}])
    assert not admit and viol[0][0].startswith("I1")      # register aliasing
    admit, viol = bundle_admits([
        {"name": "A", "private": {"k"}, "public": set()},
        {"name": "B", "private": set(), "public": {"k"}}])
    assert not admit and viol[0][0].startswith("I2")      # classification conflict


def test_harness_imports_db_free():
    # The harness must import without a DB driver loaded at module top.
    import pathlib
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    import tools.cert_crypto as h
    assert "7 divides 28 (quotient-witnessed, crypto tier)" in h.CRYPTO_CLAIMS
    assert h.check_bundle_admission() is True
