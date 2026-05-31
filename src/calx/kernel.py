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


_KERNELS = {
    "factorization": check_factorization,
    "crt": check_crt,
    "unit_fraction": check_unit_fraction,
    "matrix_word": check_matrix_word,
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
