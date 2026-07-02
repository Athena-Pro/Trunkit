"""calx.kernel — independent, dependency-free proof-object checkers.

This is the *consumer side* of the cert_kernel tier. It is a faithful mirror of
the in-DB kernel (src/calx/sql/94_cert_kernel.sql): given a proof object (the
``witness`` carried in an exported bundle), it re-checks the object with logic
that is provably simpler than the producer that generated it.

Crucially this module has **no dependencies** (no psycopg, no DB) — a consumer
who receives a ``cert.export_bundle`` JSON can verify the kernel-backed claims
in it with nothing but a Python interpreter. That is proof-carrying code: the
proof travels with the result and the checker is small and independent.

Verdict convention mirrors the three-valued ledger:
    True  -> valid       (kernel accepted the object)
    False -> refuted     (kernel ran and rejected the object)
    None  -> unverified  (object malformed / unknown schema / not checkable)
"""
from __future__ import annotations

from fractions import Fraction
from math import gcd, isqrt
from typing import Any

__all__ = [
    "check_factorization",
    "check_crt",
    "check_unit_fraction",
    "check_matrix_word",
    "check_dfa_betti",
    "verify_witness",
    "verify_bundle",
]


def _is_prime(n: int) -> bool:
    if n < 2:
        return False
    if n % 2 == 0:
        return n == 2
    return all(n % i != 0 for i in range(3, isqrt(n) + 1, 2))


def check_factorization(w: dict[str, Any]) -> tuple[bool | None, dict[str, Any]]:
    """Verify a prime-factorization certificate.

    Recomputes n = prod(p**e) and sigma(n) = prod((p**(e+1)-1)//(p-1)) from the
    asserted factors and checks each base is prime. Optionally checks the
    ``asserts`` block (``sigma``, ``perfect``).
    """
    try:
        factors = w["factors"]
        n = int(w["n"])
        if not isinstance(factors, list) or not factors:
            return None, {"error": "empty/!array factors"}

        prod, sigma, all_prime = 1, 1, True
        for p, e in ((int(p), int(e)) for p, e in factors):
            if p < 2 or e < 1 or not _is_prime(p):
                all_prime = False
            prod *= p**e
            sigma *= (p ** (e + 1) - 1) // (p - 1)

        ok = (prod == n) and all_prime
        asserts = w.get("asserts", {}) or {}
        if "sigma" in asserts:
            ok = ok and sigma == int(asserts["sigma"])
        if "perfect" in asserts:
            ok = ok and (sigma - n == n) == bool(asserts["perfect"])

        return ok, {
            "kernel": "factorization",
            "n": n,
            "recomputed_product": prod,
            "recomputed_sigma": sigma,
            "aliquot_sum": sigma - n,
            "all_bases_prime": all_prime,
            "num_factors": len(factors),
        }
    except (KeyError, ValueError, TypeError, ZeroDivisionError) as exc:
        return None, {"error": f"{type(exc).__name__}: {exc}"}


def check_crt(w: dict[str, Any]) -> tuple[bool | None, dict[str, Any]]:
    """Verify a CRT certificate: x = r (mod m) for all, moduli pairwise coprime."""
    try:
        x = int(w["x"])
        congruences = w["congruences"]
        if not isinstance(congruences, list) or not congruences:
            return None, {"error": "empty/!array congruences"}

        pairs = [(int(r), int(m)) for r, m in congruences]
        hold = all(m > 0 and x % m == r % m for r, m in pairs)
        moduli = [m for _, m in pairs]
        coprime = all(
            gcd(moduli[i], moduli[j]) == 1
            for i in range(len(moduli))
            for j in range(i + 1, len(moduli))
        )
        return (hold and coprime), {
            "kernel": "crt",
            "x": x,
            "congruences_hold": hold,
            "moduli_pairwise_coprime": coprime,
        }
    except (KeyError, ValueError, TypeError) as exc:
        return None, {"error": f"{type(exc).__name__}: {exc}"}


def _matmul(a: list[list[int]], b: list[list[int]]) -> list[list[int]]:
    if not a or not b or len(a[0]) != len(b):
        raise ValueError("non-conformable matrices")
    return [[sum(a[i][k] * b[k][j] for k in range(len(b)))
             for j in range(len(b[0]))] for i in range(len(a))]


def check_matrix_word(w: dict[str, Any]) -> tuple[bool | None, dict[str, Any]]:
    """Verify a matrix-semigroup word certificate (cf. arXiv:2604.15386).

    Multiplies the generator matrices in the order given by ``word`` and checks
    the product equals ``target``. *Finding* a word that reaches a target
    (semigroup membership / the word problem) is hard, even undecidable in
    general; *checking* one is a single matrix-chain product — the canonical
    untrusted-certificate split.
    """
    try:
        gens = w["generators"]
        word = w["word"]
        target = [[int(x) for x in row] for row in w["target"]]
        if not isinstance(word, list) or not word:
            return None, {"error": "empty/!array word"}
        if any(g not in gens for g in word):
            return None, {"error": "word references an undefined generator"}

        gmats = {k: [[int(x) for x in row] for row in m] for k, m in gens.items()}
        prod = gmats[word[0]]
        for sym in word[1:]:
            prod = _matmul(prod, gmats[sym])

        ok = prod == target
        return ok, {
            "kernel": "matrix_word",
            "word_length": len(word),
            "num_generators": len(gens),
            "recomputed_product": prod,
            "matches_target": ok,
        }
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        return None, {"error": f"{type(exc).__name__}: {exc}"}


def check_dfa_betti(w: dict[str, Any]) -> tuple[bool | None, dict[str, Any]]:
    """Verify a DFA/graph Betti certificate (the LQLE topological bridge).

    A DFA transition graph is a 1-complex: states are vertices, transitions are
    edges (self-loops and parallel edges count). Its homology is fully determined
    by ``beta0`` (connected components, undirected), ``beta1 = E - V + beta0``
    (circuit rank), and ``chi = V - E`` (with ``beta_n = 0`` for n >= 2).
    *Building/minimizing* the automaton is the work; *checking* its Betti
    signature is one union-find pass over the edge list.
    """
    try:
        V = int(w["V"])
        edges = w["edges"]
        if V < 1:
            return None, {"error": "V must be >= 1"}
        if not isinstance(edges, list):
            return None, {"error": "edges must be an array"}

        parent = list(range(V))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        n_edges = 0
        for e in edges:
            u, x = int(e[0]), int(e[1])
            if not (0 <= u < V and 0 <= x < V):
                return None, {"error": f"edge endpoint out of range: [{u},{x}]"}
            n_edges += 1
            ru, rx = find(u), find(x)
            if ru != rx:
                parent[ru] = rx

        beta0 = sum(1 for i in range(V) if find(i) == i)
        beta1 = n_edges - V + beta0
        chi = V - n_edges

        asserts = w.get("asserts", {}) or {}
        ok = True
        if "beta0" in asserts:
            ok = ok and beta0 == int(asserts["beta0"])
        if "beta1" in asserts:
            ok = ok and beta1 == int(asserts["beta1"])
        if "chi" in asserts:
            ok = ok and chi == int(asserts["chi"])

        return ok, {
            "kernel": "dfa_betti",
            "V": V,
            "E": n_edges,
            "beta0": beta0,
            "beta1": beta1,
            "euler_char": chi,
        }
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        return None, {"error": f"{type(exc).__name__}: {exc}"}


def check_unit_fraction(w: dict[str, Any]) -> tuple[bool | None, dict[str, Any]]:
    """Verify an Egyptian-fraction certificate (cf. arXiv:1606.02117).

    Sums 1/d_i as an exact rational and checks it equals ``target``, plus the
    optional ``constraints`` (distinct, odd). Checking is trivial; *finding* such
    a decomposition is the hard problem the paper studies.
    """
    try:
        target = Fraction(str(w["target"]))
        dens = [int(d) for d in w["denominators"]]
        if not dens:
            return None, {"error": "empty/!array denominators"}

        all_pos = all(d > 0 for d in dens)
        all_odd = all(d % 2 == 1 for d in dens)
        distinct = len(set(dens)) == len(dens)
        total = sum((Fraction(1, d) for d in dens), Fraction(0)) if all(dens) else None

        cons = w.get("constraints", {}) or {}
        ok = (
            total == target
            and all_pos
            and (not cons.get("distinct") or distinct)
            and (not cons.get("odd") or all_odd)
        )
        return ok, {
            "kernel": "unit_fraction",
            "target": str(target),
            "sum": str(total),
            "sum_equals_target": total == target,
            "all_distinct": distinct,
            "all_odd": all_odd,
            "all_positive": all_pos,
            "num_terms": len(dens),
        }
    except (KeyError, ValueError, TypeError, ZeroDivisionError) as exc:
        return None, {"error": f"{type(exc).__name__}: {exc}"}


# --- Laurent polynomials over Z (dict exponent -> coeff), for the knot kernel ----

def _lp_norm(p: dict[int, int]) -> dict[int, int]:
    return {e: c for e, c in p.items() if c != 0}


def _lp_add(a: dict[int, int], b: dict[int, int]) -> dict[int, int]:
    out = dict(a)
    for e, c in b.items():
        out[e] = out.get(e, 0) + c
    return _lp_norm(out)


def _lp_mul(a: dict[int, int], b: dict[int, int]) -> dict[int, int]:
    out: dict[int, int] = {}
    for ea, ca in a.items():
        for eb, cb in b.items():
            out[ea + eb] = out.get(ea + eb, 0) + ca * cb
    return _lp_norm(out)


def _lp_sub(a: dict[int, int], b: dict[int, int]) -> dict[int, int]:
    return _lp_add(a, {e: -c for e, c in b.items()})


def _lp_canon(p: dict[int, int]) -> dict[int, int]:
    """Canonical form up to units (+- t^k): shift min exponent to 0 and make the
    lowest-degree coefficient positive. Two Laurent polynomials are equal up to a
    unit iff their canonical forms are identical."""
    p = _lp_norm(p)
    if not p:
        return {}
    lo = min(p)
    shifted = {e - lo: c for e, c in p.items()}
    if shifted[0] < 0:
        shifted = {e: -c for e, c in shifted.items()}
    return shifted


# Reduced Burau small blocks and their inverses (each block has monomial
# determinant -t, so the inverse is exact Laurent). t -> {1:1}, 1 -> {0:1}.
_T = {1: 1}
_ONE = {0: 1}
_NEG_T = {1: -1}
_TINV = {-1: 1}
_NEG_TINV = {-1: -1}


def _burau_gen(n: int, g: int) -> list[list[dict[int, int]]]:
    """Reduced Burau matrix (size n-1) of generator g: +i = sigma_i, -i = inverse.

    Standard convention (e.g. Wikipedia 'Burau representation', reduced):
      sigma_1        -> [[-t,0],[1,1]]      block at coords (1,2)
      sigma_i (mid)  -> [[1,t,0],[0,-t,0],[0,1,1]]  block at (i-1,i,i+1)
      sigma_{n-1}    -> [[1,t],[0,-t]]      block at (n-2,n-1)
    For n=2 the representation is 1-dimensional: sigma_1 -> [-t].
    """
    m = n - 1
    M = [[dict(_ONE) if r == c else {} for c in range(m)] for r in range(m)]
    i, inv = abs(g), g < 0
    if not (1 <= i <= n - 1):
        raise ValueError(f"generator {g} out of range for B_{n}")

    def place(block: list[list[dict[int, int]]], i0: int) -> None:
        for r in range(len(block)):
            for c in range(len(block)):
                M[i0 + r][i0 + c] = dict(block[r][c])

    if m == 1:                                  # B_2: 1x1, sigma_1 = [-t]
        M[0][0] = dict(_NEG_TINV) if inv else dict(_NEG_T)  # (-t)^{-1} = -t^{-1}
        return M
    if i == 1:
        blk = ([[_NEG_TINV, {}], [_TINV, _ONE]] if inv
               else [[_NEG_T, {}], [_ONE, _ONE]])
        place(blk, 0)
    elif i == n - 1:
        blk = ([[_ONE, _ONE], [{}, _NEG_TINV]] if inv
               else [[_ONE, _T], [{}, _NEG_T]])
        place(blk, n - 3)
    else:
        blk = ([[_ONE, _ONE, {}], [{}, _NEG_TINV, {}], [{}, _TINV, _ONE]] if inv
               else [[_ONE, _T, {}], [{}, _NEG_T, {}], [{}, _ONE, _ONE]])
        place(blk, i - 2)
    return M


def _lp_sum(parts) -> dict[int, int]:
    out: dict[int, int] = {}
    for p in parts:
        out = _lp_add(out, p)
    return out


def _lp_matmul(A, B):
    n = len(A)
    return [[_lp_sum(_lp_mul(A[r][k], B[k][c]) for k in range(n)) for c in range(n)]
            for r in range(n)]


def _lp_det(M) -> dict[int, int]:
    """Determinant of a Laurent-polynomial matrix via Laplace expansion."""
    n = len(M)
    if n == 1:
        return dict(M[0][0])
    if n == 2:
        return _lp_sub(_lp_mul(M[0][0], M[1][1]), _lp_mul(M[0][1], M[1][0]))
    total: dict[int, int] = {}
    for c in range(n):
        minor = [[M[r][cc] for cc in range(n) if cc != c] for r in range(1, n)]
        term = _lp_mul(M[0][c], _lp_det(minor))
        total = _lp_add(total, term) if c % 2 == 0 else _lp_sub(total, term)
    return total


def check_knot_alexander(w: dict[str, Any]) -> tuple[bool | None, dict[str, Any]]:
    """Verify an Alexander-polynomial certificate for a braid closure.

    A braid beta in B_n closes to a knot/link L. The reduced Burau representation
    gives the unnormalised Alexander polynomial via the identity (up to a unit
    +- t^k):  det(reducedBurau(beta) - I_{n-1})  =  Delta_L(t) * (1 + t + ... + t^{n-1}).
    *Finding* a braid whose closure is a given knot is hard; *checking* an asserted
    Alexander polynomial is a single matrix-chain product + determinant. The checker
    recomputes the LHS and compares to the asserted Delta times the cyclotomic-like
    factor, both canonicalised up to units. Optionally checks the knot determinant
    |Delta(-1)|.
    """
    try:
        n = int(w["n"])
        braid = w["braid"]
        if n < 2 or not isinstance(braid, list):
            return None, {"error": "need n>=2 and a braid array"}

        m = n - 1
        prod = [[dict(_ONE) if r == c else {} for c in range(m)] for r in range(m)]
        for g in braid:
            prod = _lp_matmul(prod, _burau_gen(n, int(g)))

        M_minus_I = [[_lp_sub(prod[r][c], _ONE if r == c else {})
                      for c in range(m)] for r in range(m)]
        lhs = _lp_det(M_minus_I)

        cycl = {e: 1 for e in range(n)}                       # 1 + t + ... + t^{n-1}
        alex = w.get("alexander")
        if not isinstance(alex, dict):
            return None, {"error": "no asserted alexander polynomial to check against",
                          "recomputed_det": {str(k): v for k, v in sorted(lhs.items())}}
        min_exp = int(alex.get("min_exp", 0))
        coeffs = [int(c) for c in alex["coeffs"]]
        delta = _lp_norm({min_exp + j: c for j, c in enumerate(coeffs)})
        rhs = _lp_mul(delta, cycl)

        ok = _lp_canon(lhs) == _lp_canon(rhs)

        asserts = w.get("asserts", {}) or {}
        det_at_minus1 = None
        if "determinant" in asserts:
            det_at_minus1 = abs(sum(c * ((-1) ** e) for e, c in delta.items()))
            ok = ok and det_at_minus1 == int(asserts["determinant"])

        return ok, {
            "kernel": "knot_alexander",
            "n": n,
            "braid_length": len(braid),
            "recomputed_det": {str(k): v for k, v in sorted(lhs.items())},
            "rhs_delta_times_cyclotomic": {str(k): v for k, v in sorted(rhs.items())},
            "matches_up_to_units": _lp_canon(lhs) == _lp_canon(rhs),
            "knot_determinant_abs_delta_at_-1": det_at_minus1,
        }
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        return None, {"error": f"{type(exc).__name__}: {exc}"}


def _method_kernels() -> dict[str, Any]:
    # Universal method kernels (calx.methods — stdlib-only, same charter).
    # Imported lazily so a stripped-down deployment of this single module can
    # still check the six built-in schemas above.
    try:
        from calx.methods import (
            check_arith_check,
            check_csp_carry,
            check_puzzle_parity,
            check_quote_carry,
        )
    except ImportError:
        return {}
    return {
        "arith_check": check_arith_check,
        "quote_carry": check_quote_carry,
        "csp_carry": check_csp_carry,
        "puzzle_parity": check_puzzle_parity,
    }


_KERNELS = {
    "factorization": check_factorization,
    "crt": check_crt,
    "unit_fraction": check_unit_fraction,
    "matrix_word": check_matrix_word,
    "dfa_betti": check_dfa_betti,
    "knot_alexander": check_knot_alexander,
    **_method_kernels(),
}


def verify_witness(witness: dict[str, Any] | None) -> tuple[bool | None, dict[str, Any]]:
    """Dispatch a proof object to the kernel registered for its ``schema``."""
    if not isinstance(witness, dict):
        return None, {"error": "witness is not an object"}
    schema = witness.get("schema")
    if schema is None:
        return None, {"error": "witness has no schema"}
    checker = _KERNELS.get(schema)
    if checker is None:
        return None, {"error": f"no kernel for schema {schema!r}"}
    return checker(witness)


def verify_bundle(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Re-check every kernel-backed claim in an exported ``cert.export_bundle``.

    Returns one result row per claim. Claims whose witness carries no recognised
    kernel schema (e.g. comp_sql claims, which need the DB) are reported as
    ``checkable=False`` rather than silently passed.
    """
    results: list[dict[str, Any]] = []
    for entry in bundle.get("claims", []):
        claim = entry.get("claim", {})
        wrap = entry.get("witness") or {}
        witness = wrap.get("body")
        ledger_status = (entry.get("certificate") or {}).get("status")

        schema = witness.get("schema") if isinstance(witness, dict) else None
        if schema not in _KERNELS:
            results.append({
                "claim_id": claim.get("id"),
                "statement": claim.get("statement"),
                "checkable": False,
                "ledger_status": ledger_status,
                "note": "no kernel-checkable witness (needs the producer DB)",
            })
            continue

        ok, evidence = verify_witness(witness)
        independent = {True: "valid", False: "refuted", None: "unverified"}[ok]
        results.append({
            "claim_id": claim.get("id"),
            "statement": claim.get("statement"),
            "checkable": True,
            "independent_verdict": independent,
            "ledger_status": ledger_status,
            "agrees_with_ledger": (independent == ledger_status),
            "evidence": evidence,
        })
    return results
