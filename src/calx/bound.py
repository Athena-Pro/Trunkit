"""Numeric-bound certificates (cert bound layer).

Pure-Python mirror of the order logic in `105_cert_bound.sql`, so a consumer
holding an exported bound set can re-derive its structure with no database,
and so the two statements of the order can be tested against each other. No
third-party dependency — the calx core stays psycopg-only.

A bound is a value TOGETHER WITH the hypotheses it holds under, and
`dominates` is the product order over those two coordinates: at least as
tight, and assuming no more. That is the whole reason the order is partial —
a sharper conditional bound supersedes nothing, and both it and the weaker
unconditional bound stay on the frontier.

WHAT A CONSUMER CAN AND CANNOT SETTLE OFFLINE. The relational facts —
consistency, domination, frontier, enclosure — are re-derived here exactly,
in Decimal, from the carried data alone. Whether a bound is TRUE is not: that
lives in a separate claim in the ledger, and whether that claim still stands
is a database question. So `optimal` can return False offline (something on
record beats it, which needs no trust) but reaches True only when the caller
carries the truth-claim's standing alongside the bound; otherwise it returns
None. Reporting "optimal" from a bound set alone would be asserting a
theorem, which this module is not in a position to do.

Values are Decimal, never float. In binary floating point an enclosure width
comes out narrower than it is, and a bound reported tighter than it is, is
the one error this tier exists to make impossible. Decode with
``json.loads(text, parse_float=Decimal)`` so the literal text survives.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

__all__ = [
    "Bound",
    "consistent",
    "dominates",
    "enclosure",
    "frontier",
    "from_json",
    "optimal",
    "parse",
    "tighter_eq",
]

DIRECTIONS = ("upper", "lower")


@dataclass(frozen=True)
class Bound:
    """One bound on one quantity. Mirrors a row of cert.bound.

    ``claim_standing`` is the effective_status of the claim carrying this
    bound's TRUTH, as exported from cert.standing. It is carried, not
    re-derived — a consumer who needs it re-checked runs bundle_verify.
    """

    id: int
    direction: str
    value: Decimal
    is_strict: bool = False
    hypotheses: frozenset[str] = field(default_factory=frozenset)
    source: str = ""
    claim_id: int | None = None
    attained_by: Any = None
    claim_standing: str | None = None


def _decimal(raw: Any) -> Decimal:
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, bool) or raw is None:
        raise ValueError(f"bound value must be numeric, got {raw!r}")
    if isinstance(raw, float):
        # A float already lost the literal; str() recovers the shortest
        # round-tripping decimal, which is the best available and still not
        # exact. Callers should decode with parse_float=Decimal instead.
        return Decimal(str(raw))
    try:
        return Decimal(raw)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"bound value must be numeric, got {raw!r}") from exc


def from_json(obj: Any) -> Bound:
    """Build one Bound from a decoded JSON object. Raises ValueError."""
    if not isinstance(obj, dict):
        raise ValueError(f"each bound must be an object, got {type(obj).__name__}")
    direction = obj.get("direction")
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be 'upper' or 'lower', got {direction!r}")
    if "id" not in obj:
        raise ValueError("each bound needs an id")
    hypotheses = obj.get("hypotheses") or []
    if isinstance(hypotheses, str) or not isinstance(hypotheses, Iterable):
        raise ValueError("hypotheses must be a list of labels")
    hyp = frozenset(str(h) for h in hypotheses)
    return Bound(
        id=obj["id"],
        direction=direction,
        value=_decimal(obj.get("value")),
        is_strict=bool(obj.get("is_strict", False)),
        hypotheses=hyp,
        source=str(obj.get("source") or ""),
        claim_id=obj.get("claim_id"),
        attained_by=obj.get("attained_by"),
        claim_standing=obj.get("claim_standing"),
    )


def parse(objs: Any) -> list[Bound]:
    """Build a bound set from a decoded JSON list. Raises ValueError.

    Duplicate ids are rejected: every function below reports by id, and two
    bounds sharing one would make the evidence unreadable.
    """
    if not isinstance(objs, list):
        raise ValueError("bounds must be a JSON list")
    bounds = [from_json(o) for o in objs]
    seen: set[Any] = set()
    for b in bounds:
        if b.id in seen:
            raise ValueError(f"duplicate bound id {b.id!r}")
        seen.add(b.id)
    return bounds


def tighter_eq(a: Bound, b: Bound) -> bool:
    """Is `a` at least as tight as `b`, ignoring hypotheses?

    Smaller is tighter for upper bounds, larger for lower ones; at equal
    values a strict inequality is tighter than a non-strict one.
    """
    if a.direction != b.direction:
        return False
    closer = a.value < b.value if a.direction == "upper" else a.value > b.value
    return closer or (a.value == b.value and (a.is_strict or not b.is_strict))


def dominates(a: Bound, b: Bound) -> bool:
    """The order: `a` is at least as tight AND assumes no more, strictly
    better in at least one of the two. Irreflexive, antisymmetric, transitive.

    Callers pass bounds on ONE quantity; cert.bound_dominates gets that check
    for free from quantity_id, which a carried set has no equivalent of.
    """
    if a.id == b.id or a.direction != b.direction:
        return False
    if not (tighter_eq(a, b) and a.hypotheses <= b.hypotheses):
        return False
    return (a.value != b.value or a.is_strict != b.is_strict) or not (
        b.hypotheses <= a.hypotheses
    )


def frontier(bounds: Sequence[Bound]) -> list[dict[str, Any]]:
    """The undominated bounds, each with the count of what it beats.

    A frontier of size > 1 in one direction means genuinely incomparable
    hypotheses, not an unfinished computation.
    """
    rows = [
        {
            "bound_id": b.id,
            "direction": b.direction,
            "value": str(b.value),
            "is_strict": b.is_strict,
            "hypotheses": sorted(b.hypotheses),
            "source": b.source,
            "dominates_n": sum(1 for o in bounds if dominates(b, o)),
        }
        for b in bounds
        if not any(dominates(o, b) for o in bounds)
    ]
    rows.sort(key=lambda r: (r["direction"], Decimal(r["value"])))
    return rows


def consistent(bounds: Sequence[Bound]) -> tuple[bool, dict[str, Any]]:
    """Do the bounds admit any value at all?

    A lower bound above an upper one is a contradiction: under the union of
    their hypotheses no value exists, so one of the two is wrong. Both are
    named, with their truth-claims, so a consumer sees which pair collides
    rather than a bare false. Mirrors cert.bound_consistent.
    """
    conflicts = [
        {
            "lower_bound_id": lo.id, "lower_value": str(lo.value),
            "lower_strict": lo.is_strict, "lower_source": lo.source,
            "lower_claim_id": lo.claim_id,
            "upper_bound_id": up.id, "upper_value": str(up.value),
            "upper_strict": up.is_strict, "upper_source": up.source,
            "upper_claim_id": up.claim_id,
            "under_hypotheses": sorted(lo.hypotheses | up.hypotheses),
        }
        for lo in bounds if lo.direction == "lower"
        for up in bounds if up.direction == "upper"
        if lo.value > up.value
        or (lo.value == up.value and (lo.is_strict or up.is_strict))
    ]
    if conflicts:
        return False, {
            "reason": "lower bound exceeds upper bound -- empty interval",
            "conflicts": conflicts,
            "n": len(conflicts),
        }
    return True, {"consistent": True, "bounds": len(bounds)}


def enclosure(
    bounds: Sequence[Bound], assume: Iterable[str] = ()
) -> dict[str, Any] | None:
    """Tightest interval assuming no more than `assume`. None if unbounded
    on both sides; width is None if bounded on only one.
    """
    budget = frozenset(assume)
    usable = [b for b in bounds if b.hypotheses <= budget]
    lows = [b for b in usable if b.direction == "lower"]
    ups = [b for b in usable if b.direction == "upper"]
    if not lows and not ups:
        return None
    lo = max(lows, key=lambda b: (b.value, b.is_strict), default=None)
    up = min(ups, key=lambda b: (b.value, not b.is_strict), default=None)
    return {
        "lower_value": None if lo is None else str(lo.value),
        "lower_is_strict": None if lo is None else lo.is_strict,
        "lower_source": None if lo is None else lo.source,
        "upper_value": None if up is None else str(up.value),
        "upper_is_strict": None if up is None else up.is_strict,
        "upper_source": None if up is None else up.source,
        "width": None if (lo is None or up is None) else str(up.value - lo.value),
        "assumed": sorted(budget),
    }


def optimal(
    bound_id: Any, bounds: Sequence[Bound]
) -> tuple[bool | None, dict[str, Any]]:
    """Three-valued optimality, mirroring cert.bound_optimal.

    False  something on record is strictly better — a real refutation, and
           the one verdict a consumer can reach with no trust at all.
    None   undominated, but not established as optimal: no attaining
           witness, no truth-claim, or a truth-claim whose standing is not
           carried or does not stand. "Best known on record" is a statement
           about the ledger, not about mathematics.
    True   undominated, attained, and the carried standing of its
           truth-claim is valid.
    """
    target = next((b for b in bounds if b.id == bound_id), None)
    if target is None:
        return False, {"reason": "no such bound", "id": bound_id}

    beaten = [
        {"bound_id": o.id, "value": str(o.value), "strict": o.is_strict,
         "hypotheses": sorted(o.hypotheses), "source": o.source}
        for o in bounds if dominates(o, target)
    ]
    if beaten:
        return False, {
            "reason": "a registered bound is strictly better",
            "bound_id": target.id,
            "dominated_by": beaten,
        }

    if target.attained_by is None or target.claim_id is None:
        if target.attained_by is None and target.claim_id is None:
            reason = "undominated, but no attaining witness and no truth-claim"
        elif target.attained_by is None:
            reason = "undominated, but no attaining witness"
        else:
            reason = "undominated and attained, but the bound's truth is not certified"
        return None, {
            "reason": reason,
            "bound_id": target.id,
            "status": "best known on record",
        }

    if target.claim_standing != "valid":
        return None, {
            "reason": "undominated and attained, but the standing of the bound's "
                      "truth-claim is not carried or does not stand",
            "bound_id": target.id,
            "truth_claim": target.claim_id,
            "truth_claim_status": target.claim_standing or "uncarried",
            "status": "best known on record",
        }

    return True, {
        "bound_id": target.id,
        "value": str(target.value),
        "hypotheses": sorted(target.hypotheses),
        "attained_by": target.attained_by,
        "truth_claim": target.claim_id,
        "truth_claim_status": target.claim_standing,
        "undominated_among": len(bounds),
    }
