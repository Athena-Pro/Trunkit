"""Exact sequence-morphism verification — consumer-side re-check.

Pure-Python mirror of the SQL in ``95_cert_morphism.sql`` (cert.morphism_apply /
cert.morphism_matches), so the two agree and a consumer can re-verify a
morphism certificate without a database. Exact arithmetic only: values are
Fractions; JSON floats are converted through their decimal string so ``2.5``
means 5/2 (NUMERIC semantics), never a binary float.

Map kinds (params dict):
  * ``affine``      {a, b} -> y_n = a*x_n + b
  * ``scale``       {c}    -> y_n = c*x_n        (special affine)
  * ``index_shift`` {s}    -> y_n = x_{n+s}      (s >= 0)
"""

from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction
from typing import Any

__all__ = ["exact", "apply", "matches"]

KINDS = ("affine", "scale", "index_shift")


def exact(v: Any) -> Fraction:
    """Exact numeric conversion: int/str direct; float via its decimal string."""
    if isinstance(v, bool):
        raise ValueError(f"not a number: {v!r}")
    if isinstance(v, int):
        return Fraction(v)
    if isinstance(v, float):
        return Fraction(str(v))
    if isinstance(v, str):
        return Fraction(v)
    if isinstance(v, Fraction):
        return v
    raise ValueError(f"not a number: {v!r}")


def apply(kind: str, params: dict[str, Any], src: Sequence[Any]) -> list[Fraction]:
    """Mirror of cert.morphism_apply; raises ValueError on bad kind/params."""
    xs = [exact(v) for v in src]
    if kind in ("affine", "scale"):
        raw_a = params.get("a", params.get("c", 1))
        a = exact(1 if raw_a is None else raw_a)
        b = exact(params.get("b", 0) or 0)
        return [a * x + b for x in xs]
    if kind == "index_shift":
        s = params.get("s")
        if not isinstance(s, int) or s < 0:
            raise ValueError("index_shift requires integer s >= 0")
        return xs[s:]
    raise ValueError(f"unknown morphism kind {kind!r}")


def matches(
    kind: str,
    params: dict[str, Any],
    src_terms: Sequence[Any],
    dst_terms: Sequence[Any],
) -> tuple[bool, dict[str, Any]]:
    """Mirror of cert.morphism_matches: exact equality over the common prefix.

    Same three outcomes as the SQL: (False, apply failed), (False, empty
    overlap / mismatch at i), or (True, verified_terms=n). Verification only
    ever covers the overlap — the certificate claims nothing beyond it.
    """
    try:
        mapped = apply(kind, params, src_terms)
        dst = [exact(v) for v in dst_terms]
    except (ValueError, ZeroDivisionError) as exc:
        return False, {"reason": "apply failed", "detail": str(exc)}
    n = min(len(mapped), len(dst))
    if n == 0:
        return False, {"reason": "empty overlap"}
    for i in range(n):
        if mapped[i] != dst[i]:
            return False, {
                "reason": "morphism mismatch",
                "at": i + 1,                       # 1-based, like the SQL
                "expected": str(dst[i]),
                "got": str(mapped[i]),
            }
    return True, {"kind": kind, "params": params, "verified_terms": n, "exact": True}
