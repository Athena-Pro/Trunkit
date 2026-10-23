"""Portable, bounded certificates for finite combinatorics (``comb.v1``).

The instance digest commits a normalized labelled instance, not an isomorphism
class. Kernels never run a solver or a command supplied by a producer. Failed
existential certificates leave the proposition unverified; total property
checks can refute it. A small RUP checker supplies independently checked
negative colouring proofs. All arithmetic and dependencies are stdlib-only.

Wire format::

    {"schema": "comb.v1", "instance": {...}, "instance_digest": "...",
     "predicate": {"name": "chromatic_exact", "k": 3},
     "certificate": {"colouring": [0,1,0,1,2],
                     "lower_bound": {"kind":"odd_cycle","vertices":[0,1,2,3,4]}}}

The typed ledger subject is returned by :func:`claim_subject_ref`. Consumers
must compare it to the witness; a content hash alone does not establish that
an arbitrary natural-language statement means the checked predicate.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, deque
from dataclasses import dataclass
from itertools import combinations
from math import comb, isqrt
from pathlib import Path
from typing import Any

from . import numberfield as nf
from .ledger import canonical_json

SCHEMA = "comb.v1"
INSTANCE_DIGEST_KIND = "trunkit-comb-instance-v1"
CNF_ENCODING = "trunkit.graph-colouring.cnf.v1"
MAX_VERTICES = 10_000
MAX_BLOCKS = 200_000
MAX_WORK = 2_000_000
MAX_DEPTH = 24
MAX_JSON_BYTES = 16_000_000

__all__ = [
    "SCHEMA",
    "normalize_instance",
    "instance_digest",
    "make_witness",
    "claim_subject_ref",
    "import_instance",
    "export_witness",
    "check_combinatorial",
    "graph_colouring_cnf",
    "check_rup",
    "relabel_witness",
    "disjoint_union_witness",
    "c5_chromatic_example",
]


class _Unknown(ValueError):
    """Unsupported, malformed, or over-budget producer input."""


@dataclass
class _Budget:
    remaining: int = MAX_WORK

    def take(self, count: int = 1) -> None:
        self.remaining -= count
        if self.remaining < 0:
            raise _Unknown("verification work budget exceeded")


def _int(value: Any, label: str, minimum: int | None = None) -> int:
    if type(value) is not int or value.bit_length() > 4096:
        raise _Unknown(f"{label} must be an integer of at most 4096 bits")
    if minimum is not None and value < minimum:
        raise _Unknown(f"{label} must be >= {minimum}")
    return value


def _array(value: Any, label: str, limit: int = MAX_BLOCKS) -> list:
    if not isinstance(value, list) or len(value) > limit:
        raise _Unknown(f"{label} must be an array of at most {limit} entries")
    return value


def _hash(kind: str, value: Any) -> str:
    return hashlib.sha256((kind + "\x1f" + canonical_json(value)).encode("utf-8")).hexdigest()


def _json_copy(value: Any, depth: int = 0) -> Any:
    """Copy portable exact JSON; reject coercions, nonfinite numbers and cycles."""
    if depth > MAX_DEPTH:
        raise _Unknown("JSON nesting limit exceeded")
    if value is None or type(value) in (bool, str):
        return value
    if type(value) is int:
        return _int(value, "JSON number")
    if isinstance(value, list):
        return [_json_copy(x, depth + 1) for x in _array(value, "JSON array")]
    if isinstance(value, dict):
        if any(type(k) is not str for k in value):
            raise _Unknown("JSON object keys must be text")
        return {k: _json_copy(v, depth + 1) for k, v in value.items()}
    raise _Unknown("portable combinatorial data requires exact JSON integers, never floats")


def normalize_instance(instance: dict[str, Any]) -> dict[str, Any]:
    """Return a fresh deterministic snapshot, omitting display/provenance fields.

    A structure contains ``kind``, ``ground_n``, ``elements`` and ``blocks``.
    Elements and blocks are sorted by their explicit indices; a missing
    ``elements`` array denotes the full ground set. Block indices are preserved.
    Directional positions remain paired with their elements when sorting.
    Shape faults remain representable so ``wellformed`` can refute them.
    """
    if not isinstance(instance, dict):
        raise _Unknown("instance must be an object")
    kind = instance.get("kind")
    if kind == "primitive_set":
        values = []
        for value in _array(instance.get("values"), "integer values", MAX_VERTICES):
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 1234
                or not value.isascii()
                or not value.isdecimal()
            ):
                raise _Unknown("primitive-set values must be positive decimal integer strings")
            number = _int(int(value), "primitive-set integer", 1)
            values.append(number)
        return {"kind": kind, "values": [str(v) for v in sorted(values)]}
    if kind == "realisation":
        return {
            "kind": kind,
            "structure": normalize_instance(instance.get("structure")),
            "configuration": _normalize_configuration(instance.get("configuration")),
        }
    if kind not in ("graph", "digraph", "hypergraph", "set_system"):
        raise _Unknown("unsupported instance kind")
    n = _int(instance.get("ground_n"), "ground_n", 0)
    if n > MAX_VERTICES:
        raise _Unknown("vertex limit exceeded")
    elements = [
        _int(x, "element", 0)
        for x in _array(instance.get("elements", list(range(n))), "elements", MAX_VERTICES)
    ]
    if len(set(elements)) != len(elements):
        raise _Unknown("duplicate ground element")
    blocks = []
    indices = set()
    total_incidence = 0
    for i, block in enumerate(_array(instance.get("blocks"), "blocks")):
        if isinstance(block, list):
            block = {"idx": i, "elements": block}
        if not isinstance(block, dict):
            raise _Unknown("block must be an object or element array")
        idx = _int(block.get("idx", i), "block idx", 0)
        if idx in indices:
            raise _Unknown("duplicate block idx")
        indices.add(idx)
        verts = [_int(x, "incidence", 0) for x in _array(block.get("elements"), "block elements")]
        total_incidence += len(verts)
        if total_incidence > MAX_WORK:
            raise _Unknown("incidence limit exceeded")
        if len(set(verts)) != len(verts):
            raise _Unknown("duplicate incidence in a block")
        positions = block.get("positions")
        if positions is not None:
            positions = [_int(x, "position", 0) for x in _array(positions, "positions")]
            if len(positions) != len(verts):
                raise _Unknown("positions must be parallel to elements")
            pairs = sorted(zip(verts, positions, strict=True))
            verts, positions = [x for x, _ in pairs], [p for _, p in pairs]
        else:
            verts = sorted(verts)
        blocks.append({"idx": idx, "elements": verts, "positions": positions})
    return {
        "kind": kind,
        "ground_n": n,
        "elements": sorted(elements),
        "blocks": sorted(blocks, key=lambda b: b["idx"]),
    }


def _normalize_configuration(config: Any) -> dict:
    if not isinstance(config, dict):
        raise _Unknown("configuration must be an object")
    dim = _int(config.get("dim"), "dimension", 1)
    if dim > 64:
        raise _Unknown("dimension limit exceeded")
    domain = config.get("coord_domain")
    if domain not in ("rational", "algebraic", "float_heuristic"):
        raise _Unknown("unsupported coordinate domain")
    poly = [
        _int(c, "polynomial coefficient")
        for c in _array(config.get("min_poly", [0, 1]), "minimal polynomial", 17)
    ]
    if len(poly) < 2 or poly[-1] != 1:
        raise _Unknown("minimal polynomial must be monic with positive degree")
    points = []
    for point in _array(config.get("points"), "points", MAX_VERTICES):
        if not isinstance(point, dict):
            raise _Unknown("point must be an object")
        idx = _int(point.get("idx"), "point idx", 0)
        coords = []
        for axis, coord in enumerate(_array(point.get("coords"), "coordinates", 64)):
            if type(coord) is int:
                coord = {"axis": axis, "num": [coord], "den": 1}
            if not isinstance(coord, dict):
                raise _Unknown("coordinate must be an exact num/den object")
            num = [
                _int(c, "coordinate numerator") for c in _array(coord.get("num"), "numerator", 16)
            ]
            den = _int(coord.get("den", 1), "coordinate denominator", 1)
            # Keep integer representatives stable across Python and PostgreSQL;
            # the arithmetic normalizes only after the snapshot is committed.
            coords.append(
                {"axis": _int(coord.get("axis", axis), "axis", 0), "num": num, "den": den}
            )
        points.append({"idx": idx, "coords": sorted(coords, key=lambda c: c["axis"])})
    return {
        "dim": dim,
        "coord_domain": domain,
        "min_poly": poly,
        "points": sorted(points, key=lambda p: p["idx"]),
    }


def instance_digest(instance: dict[str, Any]) -> str:
    """SHA-256 of domain separator + canonical normalized instance JSON."""
    return _hash(INSTANCE_DIGEST_KIND, normalize_instance(instance))


def make_witness(instance: dict, predicate: dict | str, certificate: dict | None = None) -> dict:
    """Build a detached portable certificate. Construction does not imply validity."""
    snapshot = normalize_instance(instance)
    pred = {"name": predicate} if isinstance(predicate, str) else _json_copy(predicate)
    if not isinstance(pred, dict) or not isinstance(pred.get("name"), str):
        raise _Unknown("predicate requires a text name")
    if certificate is not None and not isinstance(certificate, dict):
        raise _Unknown("certificate must be an object")
    return {
        "schema": SCHEMA,
        "instance": snapshot,
        "predicate": pred,
        "certificate": _json_copy({} if certificate is None else certificate),
        "instance_digest": _hash(INSTANCE_DIGEST_KIND, snapshot),
    }


def claim_subject_ref(witness: dict) -> dict:
    """Typed claim content that a bundle consumer must match to its witness."""
    return {
        "witness_schema": SCHEMA,
        "instance_digest": witness["instance_digest"],
        "predicate": _json_copy(witness["predicate"]),
    }


def import_instance(source: str | Path | dict) -> dict:
    """Read a JSON object, JSON text, or a local JSON file into a snapshot."""
    if isinstance(source, dict):
        value = source
    else:
        raw = str(source)
        if not raw.lstrip().startswith("{"):
            path = Path(source)
            if path.stat().st_size > MAX_JSON_BYTES:
                raise _Unknown("JSON file size limit exceeded")
            raw = path.read_text(encoding="utf-8")
        if len(raw.encode("utf-8")) > MAX_JSON_BYTES:
            raise _Unknown("JSON size limit exceeded")
        value = json.loads(raw)
    return normalize_instance(value.get("instance", value))


def export_witness(witness: dict, path: str | Path | None = None) -> str:
    """Return portable JSON, optionally writing it to a caller-selected path."""
    value = _json_copy(witness)
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if len(text.encode("utf-8")) > MAX_JSON_BYTES:
        raise _Unknown("JSON size limit exceeded")
    if path is not None:
        Path(path).write_text(text, encoding="utf-8")
    return text


def _shape(instance: dict) -> tuple[bool, str]:
    n, kind = instance["ground_n"], instance["kind"]
    if instance["elements"] != list(range(n)):
        return False, "ground elements are not exactly 0..ground_n-1"
    seen = set()
    for block in instance["blocks"]:
        verts, pos = block["elements"], block["positions"]
        if any(v >= n for v in verts):
            return False, f"block {block['idx']} references an absent element"
        if not verts and kind in ("graph", "digraph"):
            return False, f"block {block['idx']} is empty"
        if kind in ("graph", "digraph") and len(verts) != 2:
            return False, f"block {block['idx']} is not an edge"
        if kind == "digraph":
            if pos is None or sorted(pos) != [0, 1]:
                return False, f"block {block['idx']} lacks arc positions 0,1"
            sig = tuple(v for _, v in sorted(zip(pos, verts, strict=True)))
        else:
            if pos is not None:
                return False, f"unordered block {block['idx']} carries positions"
            sig = tuple(verts)
        if kind in ("graph", "digraph") and sig in seen:
            return False, "parallel blocks in a simple structure"
        seen.add(sig)
    return True, "structure is wellformed"


def _graph(instance: dict) -> set[tuple[int, int]]:
    if instance["kind"] != "graph":
        raise _Unknown("predicate requires an undirected simple graph")
    good, why = _shape(instance)
    if not good:
        raise _Unknown(why)
    return {tuple(b["elements"]) for b in instance["blocks"]}


def _vertices(values: Any, n: int, name: str = "vertices") -> list[int]:
    out = [_int(v, name, 0) for v in _array(values, name, MAX_VERTICES)]
    if any(v >= n for v in out) or len(out) != len(set(out)):
        raise _Unknown(f"{name} must contain distinct ground vertices")
    return out


def _colouring(instance: dict, values: Any, k: int, budget: _Budget) -> bool:
    n = instance["ground_n"]
    if isinstance(values, dict):
        if set(values) != {str(v) for v in range(n)}:
            return False
        values = [values[str(v)] for v in range(n)]
    if not isinstance(values, list) or len(values) != n:
        return False
    if any(type(c) is not int or c < 0 or c >= k for c in values):
        return False
    for block in instance["blocks"]:
        budget.take(len(block["elements"]))
        if len({values[v] for v in block["elements"]}) < 2:
            return False
    return True


def _subset_ok(instance: dict, vertices: list[int], mode: str, budget: _Budget) -> bool:
    chosen = set(vertices)
    for block in instance["blocks"]:
        budget.take(len(block["elements"]))
        s = set(block["elements"])
        if mode == "independent_set" and s <= chosen:
            return False
        if mode in ("hitting_set", "vertex_cover") and not s & chosen:
            return False
    return True


def _family_probe(instance: dict, predicate: dict, budget: _Budget) -> tuple[bool, dict]:
    """Finite intersection, t-codegree and design checks, including zero counts."""
    name = predicate["name"]
    blocks = [set(block["elements"]) for block in instance["blocks"]]
    if name == "t_intersecting":
        t = _int(predicate.get("t"), "t", 0)
        for block, record in zip(blocks, instance["blocks"], strict=True):
            if len(block) < t:
                return False, {"violating_block": record["idx"], "intersection_size": len(block)}
        for i, left in enumerate(blocks):
            for j in range(i + 1, len(blocks)):
                budget.take(len(left) + len(blocks[j]) + 1)
                intersection = len(left & blocks[j])
                if intersection < t:
                    return False, {
                        "violating_blocks": [
                            instance["blocks"][i]["idx"],
                            instance["blocks"][j]["idx"],
                        ],
                        "intersection_size": intersection,
                    }
        return True, {"checked_pairs": len(blocks) * (len(blocks) - 1) // 2}
    if name == "codegree":
        subset = set(_vertices(predicate.get("vertices"), instance["ground_n"]))
        expected = _int(predicate.get("value"), "codegree value", 0)
        actual = 0
        for block in blocks:
            budget.take(len(subset) + 1)
            actual += subset <= block
        return actual == expected, {"codegree": actual}
    t = _int(predicate.get("t"), "t", 0)
    n = instance["ground_n"]
    if t > n:
        raise _Unknown("t must be at most ground_n")
    if name == "t_design":
        k = _int(predicate.get("k"), "k", 0)
        expected = _int(predicate.get("lambda"), "lambda", 0)
        if t > k or k > n:
            raise _Unknown("a design requires 0 <= t <= k <= ground_n")
        for block, record in zip(blocks, instance["blocks"], strict=True):
            if len(block) != k:
                return False, {"violating_block": record["idx"], "detail": "not k-uniform"}
    else:
        expected = _int(predicate.get("max"), "maximum codegree", 0)
    # A threshold upper bound does not require enumerating zero-codegree
    # subsets. Exact designs do, so reject an excessive universe in advance.
    universe = comb(n, t)
    if name == "t_design":
        budget.take(universe * (t + 1))
    counts = Counter()
    for block in blocks:
        for subset in combinations(sorted(block), t):
            budget.take(t + 1)
            counts[subset] += 1
            if len(counts) > MAX_BLOCKS:
                raise _Unknown("codegree table size limit exceeded")
            if name == "codegree_at_most" and counts[subset] > expected:
                return False, {"violating_subset": list(subset), "codegree": counts[subset]}
    if name == "codegree_at_most":
        return True, {"maximum_codegree": max(counts.values(), default=0)}
    for subset in combinations(range(n), t):
        actual = counts[subset]
        if actual != expected:
            return False, {"violating_subset": list(subset), "codegree": actual}
    return True, {"checked_subsets": universe, "blocks": len(blocks)}


def _matching(instance: dict, values: Any, budget: _Budget) -> list[tuple[int, int]] | None:
    edges = _graph(instance)
    chosen = []
    occupied = set()
    by_idx = {b["idx"]: tuple(b["elements"]) for b in instance["blocks"]}
    for value in _array(values, "matching"):
        budget.take()
        if type(value) is int:
            edge = by_idx.get(value)
        elif isinstance(value, list) and len(value) == 2:
            edge = tuple(sorted(_vertices(value, instance["ground_n"], "edge")))
        else:
            raise _Unknown("matching entries must be edge indices or vertex pairs")
        if edge not in edges or occupied.intersection(edge):
            return None
        occupied.update(edge)
        chosen.append(edge)
    return chosen


def _clique(instance: dict, values: Any, budget: _Budget) -> list[int] | None:
    edges = _graph(instance)
    vertices = _vertices(values, instance["ground_n"])
    for i, u in enumerate(vertices):
        for v in vertices[i + 1 :]:
            budget.take()
            if tuple(sorted((u, v))) not in edges:
                return None
    return vertices


def _odd_cycle(instance: dict, values: Any, budget: _Budget) -> list[int] | None:
    edges = _graph(instance)
    vertices = _vertices(values, instance["ground_n"])
    if len(vertices) < 3 or len(vertices) % 2 != 1:
        return None
    for u, v in zip(vertices, vertices[1:] + vertices[:1], strict=True):
        budget.take()
        if tuple(sorted((u, v))) not in edges:
            return None
    return vertices


def _block_multiset(instance: dict, mapping: list[int] | None = None) -> Counter:
    out = Counter()
    for block in instance["blocks"]:
        verts, positions = block["elements"], block["positions"]
        mapped = [mapping[v] for v in verts] if mapping is not None else verts
        if positions is None:
            sig = tuple(sorted(mapped))
        else:
            sig = tuple(v for _, v in sorted(zip(positions, mapped, strict=True)))
        out[sig] += 1
    return out


def _isomorphism(source: dict, target: dict, mapping: Any, budget: _Budget) -> bool:
    if source["kind"] != target["kind"] or source["ground_n"] != target["ground_n"]:
        return False
    n = source["ground_n"]
    mapping = _vertices(mapping, n, "mapping")
    if len(mapping) != n or not _shape(source)[0] or not _shape(target)[0]:
        return False
    budget.take(sum(len(b["elements"]) for b in source["blocks"] + target["blocks"]))
    return _block_multiset(source, mapping) == _block_multiset(target)


def graph_colouring_cnf(instance: dict, k: int, *, _budget: _Budget | None = None) -> dict:
    """Deterministic exactly-one-colour CNF, pinned to the complete graph snapshot.

    Variable ``v*k+c+1`` denotes colour c at vertex v. Clauses are emitted in
    vertex order, then pairwise colour exclusion order, then sorted edge order.
    This is an encoding function, not a SAT solver.
    """
    instance = normalize_instance(instance)
    edges = sorted(_graph(instance))
    k = _int(k, "k", 0)
    n = instance["ground_n"]
    budget = _budget or _Budget()
    if n * k > MAX_WORK or k > MAX_VERTICES:
        raise _Unknown("CNF variable limit exceeded")
    clauses = []
    for v in range(n):
        budget.take(k + 1)
        clauses.append([v * k + c + 1 for c in range(k)])
        for c in range(k):
            for d in range(c + 1, k):
                budget.take()
                clauses.append([-(v * k + c + 1), -(v * k + d + 1)])
    for u, v in edges:
        for c in range(k):
            budget.take()
            clauses.append([-(u * k + c + 1), -(v * k + c + 1)])
    result = {
        "encoding": CNF_ENCODING,
        "instance_digest": instance_digest(instance),
        "k": k,
        "num_vars": n * k,
        "clauses": clauses,
    }
    result["cnf_digest"] = _hash("trunkit-comb-cnf-v1", result)
    return result


def _clause(value: Any, num_vars: int) -> list[int]:
    clause = [_int(lit, "literal") for lit in _array(value, "clause")]
    if any(lit == 0 or abs(lit) > num_vars for lit in clause):
        raise _Unknown("CNF literal outside its pinned variable range")
    return list(dict.fromkeys(clause))


def _unit_conflict(clauses: list[list[int]], assumptions: list[int], budget: _Budget) -> bool:
    assignment = {}
    for lit in assumptions:
        if -lit in assignment:
            return True
        assignment[lit] = True
    changed = True
    while changed:
        changed = False
        for clause in clauses:
            live = []
            satisfied = False
            for lit in clause:
                budget.take()
                if lit in assignment:
                    satisfied = True
                    break
                if -lit not in assignment:
                    live.append(lit)
            if satisfied:
                continue
            if not live:
                return True
            if len(live) == 1:
                lit = live[0]
                if -lit in assignment:
                    return True
                if lit not in assignment:
                    assignment[lit] = True
                    changed = True
        budget.take()
    return False


def check_rup(
    cnf: dict, proof: dict, *, _budget: _Budget | None = None
) -> tuple[bool | None, dict]:
    """Check addition-only RUP, a deliberately small sound DRAT/LRAT subset.

    Every added clause must be justified by unit-propagation conflict after
    negating it. A checked empty clause is required. RAT steps, deletions,
    commands, and unknown formats are unsupported, never silently accepted.
    ``cnf`` must be independently regenerated by the caller for a claim.
    """
    try:
        budget = _budget or _Budget()
        if not isinstance(proof, dict) or proof.get("format") != "rup.v1":
            raise _Unknown("only addition-only rup.v1 proofs are supported")
        if set(proof) - {"format", "steps"}:
            raise _Unknown("unsupported RUP proof fields")
        num_vars = _int(cnf["num_vars"], "num_vars", 0)
        clauses = [_clause(c, num_vars) for c in _array(cnf["clauses"], "clauses")]
        for index, value in enumerate(_array(proof.get("steps"), "proof steps", 100_000)):
            budget.take()
            clause = _clause(value, num_vars)
            if not _unit_conflict(clauses, [-lit for lit in clause], budget):
                return False, {
                    "error": "RUP clause lacks a unit-propagation justification",
                    "step": index,
                }
            clauses.append(clause)
            if not clause:
                return True, {"format": "rup.v1", "checked_steps": index + 1}
        return None, {"error": "proof has no checked empty clause"}
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        return None, {"error": str(exc)}


def _uncolourable(
    instance: dict, k: int, certificate: dict, budget: _Budget
) -> tuple[bool | None, dict]:
    cnf = graph_colouring_cnf(instance, k, _budget=budget)
    if (
        certificate.get("encoding") != CNF_ENCODING
        or certificate.get("cnf_digest") != cnf["cnf_digest"]
    ):
        return None, {"error": "negative proof encoding/digest does not match regenerated CNF"}
    ok, evidence = check_rup(cnf, certificate.get("proof"), _budget=budget)
    return (True if ok is True else None), evidence


def _chromatic_lower(instance: dict, k: int, lower: dict, budget: _Budget) -> bool:
    kind = lower.get("kind")
    if kind == "clique":
        vertices = _clique(instance, lower.get("vertices"), budget)
        return vertices is not None and len(vertices) >= k
    if kind == "odd_cycle":
        return k <= 3 and _odd_cycle(instance, lower.get("vertices"), budget) is not None
    if kind == "nonempty":
        return k <= 1 and instance["ground_n"] > 0
    if kind == "rup":
        return _uncolourable(instance, max(0, k - 1), lower, budget)[0] is True
    if kind == "empty":
        return k == 0 and instance["ground_n"] == 0
    raise _Unknown("unsupported chromatic lower-bound certificate")


def _matching_upper(instance: dict, upper: dict, budget: _Budget) -> int:
    n = instance["ground_n"]
    kind = upper.get("kind")
    if kind == "vertex_count":
        return n // 2
    if kind == "vertex_cover":
        vertices = _vertices(upper.get("vertices"), n)
        if not _subset_ok(instance, vertices, "vertex_cover", budget):
            raise _Unknown("supplied upper-bound vertex cover is invalid")
        return len(vertices)
    if kind == "tutte":
        removed = set(_vertices(upper.get("removed"), n))
        edges = _graph(instance)
        adjacency = [set() for _ in range(n)]
        for u, v in edges:
            budget.take()
            if u not in removed and v not in removed:
                adjacency[u].add(v)
                adjacency[v].add(u)
        seen, odd = set(removed), 0
        for vertex in range(n):
            if vertex in seen:
                continue
            q, size = deque([vertex]), 0
            seen.add(vertex)
            while q:
                cur = q.popleft()
                size += 1
                for nxt in adjacency[cur]:
                    budget.take()
                    if nxt not in seen:
                        seen.add(nxt)
                        q.append(nxt)
            odd += size % 2
        return (n + len(removed) - odd) // 2
    raise _Unknown("unsupported matching upper-bound certificate")


def _cover_lower(instance: dict, certificate: dict, budget: _Budget) -> int:
    """Checked matching, clique, or odd-cycle obstruction to a smaller cover."""
    lower = certificate.get("lower_bound")
    if lower is None:
        lower = {"kind": "matching", "matching": certificate.get("matching")}
    if not isinstance(lower, dict):
        raise _Unknown("vertex-cover optimum needs a checked lower bound")
    kind = lower.get("kind")
    if kind == "matching":
        matching = _matching(instance, lower.get("matching"), budget)
        if matching is None:
            raise _Unknown("invalid lower-bound matching")
        return len(matching)
    if kind == "clique":
        clique = _clique(instance, lower.get("vertices"), budget)
        if clique is None:
            raise _Unknown("invalid lower-bound clique")
        return max(0, len(clique) - 1)
    if kind == "odd_cycle":
        cycle = _odd_cycle(instance, lower.get("vertices"), budget)
        if cycle is None:
            raise _Unknown("invalid lower-bound odd cycle")
        return (len(cycle) + 1) // 2
    raise _Unknown("unsupported vertex-cover lower-bound certificate")


def _realisation(instance: dict, predicate: dict, budget: _Budget) -> tuple[bool | None, dict]:
    graph, config = instance["structure"], instance["configuration"]
    edges = _graph(graph)
    if config["coord_domain"] == "float_heuristic":
        raise _Unknown("approximate coordinates cannot certify a realisation")
    poly = config["min_poly"]
    if config["coord_domain"] == "rational":
        if poly != [0, 1]:
            raise _Unknown("rational coordinates require polynomial x")
    elif len(poly) == 3:
        discriminant = poly[1] ** 2 - 4 * poly[0]
        if discriminant <= 0 or isqrt(discriminant) ** 2 == discriminant:
            raise _Unknown("algebraic realisation requires an irreducible real quadratic field")
    else:
        raise _Unknown("portable algebraic geometry currently supports real quadratic fields")
    n, dim = graph["ground_n"], config["dim"]
    if [p["idx"] for p in config["points"]] != list(range(n)):
        raise _Unknown("point indices must exactly match graph vertices")
    points = []
    for point in config["points"]:
        if [c["axis"] for c in point["coords"]] != list(range(dim)):
            raise _Unknown("each point needs every coordinate axis exactly once")
        points.append(
            [nf.Elem.of(c["num"], c["den"]).widen(len(poly) - 1) for c in point["coords"]]
        )
    if len(set(tuple(point) for point in points)) != n:
        return False, {"detail": "configuration contains coincident vertices"}
    faithful = predicate.get("faithful", True)
    if type(faithful) is not bool:
        raise _Unknown("faithful must be Boolean")
    for u in range(n):
        for v in range(u + 1, n):
            budget.take(dim * len(poly) ** 2)
            distance = nf.sq_distance(points[u], points[v], poly)
            unit = distance == nf.rational(1, degree=len(poly) - 1)
            edge = (u, v) in edges
            if (edge and not unit) or (faithful and unit != edge):
                return False, {"detail": "unit-distance incidence mismatch", "pair": [u, v]}
    return True, {"detail": "all exact squared distances checked", "faithful": faithful}


_RELABEL_INVARIANTS = {
    "wellformed",
    "uniform",
    "triangle_free",
    "intersecting_family",
    "primitive_family",
    "chromatic_exact",
    "matching_exact",
    "vertex_cover_exact",
    "t_intersecting",
    "codegree_at_most",
    "t_design",
}


def _composition(
    instance: dict, predicate: dict, cert: dict, budget: _Budget, depth: int
) -> tuple[bool | None, dict]:
    rule = cert["rule"]
    name = predicate["name"]
    if rule == "relabel":
        premise = cert.get("premise")
        ok, evidence = _verify(premise, budget, depth + 1)
        if ok is not True:
            return None, {"error": "relabel premise was not verified", "premise": evidence}
        if name not in _RELABEL_INVARIANTS or premise["predicate"] != predicate:
            raise _Unknown("predicate does not support this relabel rule")
        source = normalize_instance(premise["instance"])
        if not _isomorphism(source, instance, cert.get("mapping"), budget):
            return None, {"error": "relabel mapping does not preserve the entire instance"}
        return True, {"rule": rule, "premise_digest": premise["instance_digest"]}
    if rule == "disjoint_union":
        premises = _array(cert.get("premises"), "premises", MAX_VERTICES)
        if not premises:
            raise _Unknown("disjoint union requires at least one premise")
        parts = []
        for premise in premises:
            ok, evidence = _verify(premise, budget, depth + 1)
            if ok is not True:
                return None, {"error": "union premise was not verified", "premise": evidence}
            parts.append(normalize_instance(premise["instance"]))
        expected = _union_instances(parts)
        budget.take(sum(len(b["elements"]) for b in expected["blocks"]))
        if (
            instance["kind"] != expected["kind"]
            or instance["ground_n"] != expected["ground_n"]
            or not _shape(instance)[0]
            or _block_multiset(instance) != _block_multiset(expected)
        ):
            return None, {"error": "target is not the asserted disjoint union"}
        predicates = [p["predicate"] for p in premises]
        if name in ("wellformed", "triangle_free", "uniform"):
            if any(p != predicate for p in predicates):
                raise _Unknown("union premises must have the same predicate")
        elif name in ("chromatic_exact", "matching_exact", "vertex_cover_exact"):
            if any(p.get("name") != name for p in predicates):
                raise _Unknown("union optimum premises have different predicates")
            values = [_int(p.get("k"), "premise k", 0) for p in predicates]
            expected_k = max(values) if name == "chromatic_exact" else sum(values)
            if predicate != {"name": name, "k": expected_k}:
                raise _Unknown("union optimum is not the checked maximum/sum")
        else:
            raise _Unknown("unsupported disjoint-union predicate")
        return True, {"rule": rule, "premise_digests": [p["instance_digest"] for p in premises]}
    raise _Unknown("unsupported certificate rule")


def _union_instances(parts: list[dict]) -> dict:
    if not parts or any(p["kind"] != parts[0]["kind"] for p in parts):
        raise _Unknown("union parts must share a structure kind")
    blocks, offset = [], 0
    for part in parts:
        if not _shape(part)[0]:
            raise _Unknown("union part is malformed")
        for block in part["blocks"]:
            blocks.append(
                {
                    "idx": len(blocks),
                    "elements": [v + offset for v in block["elements"]],
                    "positions": block["positions"],
                }
            )
        offset += part["ground_n"]
    return normalize_instance({"kind": parts[0]["kind"], "ground_n": offset, "blocks": blocks})


def relabel_witness(witness: dict, mapping: list[int]) -> dict:
    """Build a checked-rule certificate for an invariant under vertex relabelling."""
    source = normalize_instance(witness["instance"])
    mapping = _vertices(mapping, source["ground_n"], "mapping")
    if len(mapping) != source["ground_n"]:
        raise _Unknown("mapping must be a total bijection")
    blocks = []
    for block in source["blocks"]:
        blocks.append(
            {
                "idx": block["idx"],
                "elements": [mapping[v] for v in block["elements"]],
                "positions": block["positions"],
            }
        )
    target = {"kind": source["kind"], "ground_n": source["ground_n"], "blocks": blocks}
    return make_witness(
        target, witness["predicate"], {"rule": "relabel", "premise": witness, "mapping": mapping}
    )


def disjoint_union_witness(witnesses: list[dict]) -> dict:
    """Build a certificate whose rule checks all parts and aggregate bounds."""
    if not witnesses:
        raise _Unknown("union needs a premise")
    predicate = _json_copy(witnesses[0]["predicate"])
    name = predicate["name"]
    if name in ("chromatic_exact", "matching_exact", "vertex_cover_exact"):
        values = [_int(w["predicate"].get("k"), "k", 0) for w in witnesses]
        predicate["k"] = max(values) if name == "chromatic_exact" else sum(values)
    target = _union_instances([normalize_instance(w["instance"]) for w in witnesses])
    return make_witness(target, predicate, {"rule": "disjoint_union", "premises": witnesses})


def _verify(witness: Any, budget: _Budget, depth: int = 0) -> tuple[bool | None, dict]:
    if depth > MAX_DEPTH:
        raise _Unknown("certificate recursion limit exceeded")
    if not isinstance(witness, dict) or witness.get("schema") != SCHEMA:
        raise _Unknown("unsupported combinatorial witness schema")
    instance = normalize_instance(witness.get("instance"))
    budget.take(len(instance.get("blocks", [])) + instance.get("ground_n", 0) + 1)
    digest = _hash(INSTANCE_DIGEST_KIND, instance)
    predicate = _json_copy(witness.get("predicate"))
    if not isinstance(predicate, dict) or not isinstance(predicate.get("name"), str):
        raise _Unknown("predicate requires a text name")
    name = predicate["name"]
    parameters = {
        "wellformed": set(),
        "uniform": {"k"},
        "triangle_free": set(),
        "intersecting_family": set(),
        "primitive_family": set(),
        "primitive_set": set(),
        "t_intersecting": {"t"},
        "codegree": {"vertices", "value"},
        "codegree_at_most": {"t", "max"},
        "t_design": {"t", "k", "lambda"},
        "colouring": {"k"},
        "independent_set": {"k"},
        "vertex_cover": {"k"},
        "hitting_set": {"k"},
        "clique": {"k"},
        "odd_cycle": set(),
        "matching": {"k"},
        "isomorphism": {"target_digest"},
        "not_k_colourable": {"k"},
        "chromatic_exact": {"k"},
        "matching_exact": {"k"},
        "vertex_cover_exact": {"k"},
        "unit_distance": {"faithful"},
    }
    if name not in parameters or set(predicate) - ({"name"} | parameters[name]):
        raise _Unknown("unsupported predicate name or parameters")
    evidence = {"schema": SCHEMA, "predicate": predicate, "instance_digest": digest}
    if witness.get("instance_digest") != digest:
        return None, {**evidence, "error": "instance digest mismatch", "integrity_ok": False}
    cert = witness.get("certificate", {})
    if not isinstance(cert, dict):
        raise _Unknown("certificate must be an object")
    if "rule" in cert:
        ok, detail = _composition(instance, predicate, cert, budget, depth)
        return ok, {**evidence, **detail}
    if instance["kind"] == "primitive_set":
        if name not in ("primitive_family", "primitive_set"):
            raise _Unknown("primitive-set instances support primitive_family")
        values = [int(v) for v in instance["values"]]
        for i, a in enumerate(values):
            for b in values[i + 1 :]:
                budget.take()
                if a != b and b % a == 0:
                    return False, {**evidence, "dividing_pair": [str(a), str(b)]}
        return True, evidence
    if instance["kind"] == "realisation":
        if name != "unit_distance":
            raise _Unknown("realisation instances support unit_distance")
        ok, detail = _realisation(instance, predicate, budget)
        return ok, {**evidence, **detail}
    good, why = _shape(instance)
    if name == "wellformed":
        return good, {**evidence, "detail": why}
    if not good:
        return None, {**evidence, "error": why}
    blocks = [set(b["elements"]) for b in instance["blocks"]]
    n = instance["ground_n"]
    if name in ("t_intersecting", "codegree", "codegree_at_most", "t_design"):
        ok, detail = _family_probe(instance, predicate, budget)
        return ok, {**evidence, **detail}
    if name == "uniform":
        k = _int(predicate.get("k"), "k", 0)
        bad = next((b["idx"] for b in instance["blocks"] if len(b["elements"]) != k), None)
        return bad is None, {**evidence, "violating_block": bad}
    if name in ("intersecting_family", "primitive_family"):
        if name == "intersecting_family":
            for block, record in zip(blocks, instance["blocks"], strict=True):
                if not block:
                    return False, {**evidence, "violating_blocks": [record["idx"]]}
        for i, a in enumerate(blocks):
            for j in range(i + 1, len(blocks)):
                budget.take(len(a) + len(blocks[j]))
                b = blocks[j]
                bad = not a & b if name == "intersecting_family" else a < b or b < a
                if bad:
                    return False, {
                        **evidence,
                        "violating_blocks": [
                            instance["blocks"][i]["idx"],
                            instance["blocks"][j]["idx"],
                        ],
                    }
        return True, evidence
    if name == "triangle_free":
        edges = _graph(instance)
        adjacency = [set() for _ in range(n)]
        for u, v in edges:
            adjacency[u].add(v)
            adjacency[v].add(u)
        for u, v in sorted(edges):
            for z in adjacency[u]:
                budget.take()
                if z in adjacency[v]:
                    return False, {**evidence, "triangle": sorted([u, v, z])}
        return True, evidence
    if name == "colouring":
        k = _int(predicate.get("k"), "k", 0)
        ok = _colouring(instance, cert.get("colouring"), k, budget)
    elif name in ("independent_set", "vertex_cover", "hitting_set"):
        if name == "vertex_cover":
            _graph(instance)
        vertices = _vertices(cert.get("vertices"), n)
        k = _int(predicate.get("k", len(vertices)), "k", 0)
        size_ok = len(vertices) >= k if name == "independent_set" else len(vertices) <= k
        ok = size_ok and _subset_ok(instance, vertices, name, budget)
    elif name == "clique":
        vertices = _clique(instance, cert.get("vertices"), budget)
        ok = vertices is not None and len(vertices) >= _int(predicate.get("k"), "k", 0)
    elif name == "odd_cycle":
        ok = _odd_cycle(instance, cert.get("vertices"), budget) is not None
    elif name == "matching":
        chosen = _matching(instance, cert.get("matching"), budget)
        ok = chosen is not None and len(chosen) >= _int(predicate.get("k"), "k", 0)
    elif name == "isomorphism":
        target = normalize_instance(cert.get("target"))
        if predicate.get("target_digest") != instance_digest(target):
            raise _Unknown("isomorphism predicate must commit the target instance digest")
        ok = _isomorphism(instance, target, cert.get("mapping"), budget)
    elif name == "not_k_colourable":
        k = _int(predicate.get("k"), "k", 0)
        ok, detail = _uncolourable(instance, k, cert, budget)
        return ok, {**evidence, **detail}
    elif name == "chromatic_exact":
        _graph(instance)
        k = _int(predicate.get("k"), "k", 0)
        lower = cert.get("lower_bound")
        if not isinstance(lower, dict):
            raise _Unknown("chromatic optimum needs a checked lower bound")
        ok = _colouring(instance, cert.get("colouring"), k, budget) and _chromatic_lower(
            instance, k, lower, budget
        )
    elif name in ("matching_exact", "vertex_cover_exact"):
        _graph(instance)
        k = _int(predicate.get("k"), "k", 0)
        if name == "vertex_cover_exact":
            vertices = _vertices(cert.get("vertices"), n)
            ok = (
                len(vertices) == k
                and _subset_ok(instance, vertices, "vertex_cover", budget)
                and _cover_lower(instance, cert, budget) == k
            )
        else:
            chosen = _matching(instance, cert.get("matching"), budget)
            ok = chosen is not None and len(chosen) == k
            upper = cert.get("upper_bound")
            if not isinstance(upper, dict):
                raise _Unknown("matching optimum needs a checked upper bound")
            ok = ok and _matching_upper(instance, upper, budget) == k
    else:
        raise _Unknown(f"unsupported combinatorial predicate {name!r}")
    return (True if ok else None), {
        **evidence,
        "certificate_ok": bool(ok),
        "detail": "certificate checked"
        if ok
        else "candidate rejected; proposition remains unverified",
    }


def check_combinatorial(witness: dict[str, Any]) -> tuple[bool | None, dict[str, Any]]:
    """Independently check a portable finite certificate within fixed budgets."""
    try:
        # Validate the entire object, including unused fields, before recursive
        # dispatch. A producer cannot smuggle floats or cyclic Python objects.
        portable = _json_copy(witness)
        raw = canonical_json(portable)
        if len(raw.encode("utf-8")) > MAX_JSON_BYTES:
            raise _Unknown("witness size limit exceeded")
        requested = witness.get("max_work", MAX_WORK)
        budget = _Budget(min(MAX_WORK, _int(requested, "max_work", 1)))
        return _verify(portable, budget)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError) as exc:
        return None, {"error": str(exc), "schema": SCHEMA}


def c5_chromatic_example() -> dict:
    """A complete, portable proof that the five-cycle has chromatic number 3."""
    graph = {"kind": "graph", "ground_n": 5, "blocks": [[0, 1], [1, 2], [2, 3], [3, 4], [0, 4]]}
    return make_witness(
        graph,
        {"name": "chromatic_exact", "k": 3},
        {
            "colouring": [0, 1, 0, 1, 2],
            "lower_bound": {"kind": "odd_cycle", "vertices": [0, 1, 2, 3, 4]},
        },
    )
