"""Tier 3: compose_index(B, C) and OEIS prefix matching on composed streams.

Single-pass against a frozen catalog snapshot. Multiset output in composition_membership.
"""

from __future__ import annotations

# Reuse Tier-2 OEIS client + scorer.
import importlib.util
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from calx import db

_OEIS_PATH = Path(__file__).resolve().parent / "oeis_match.py"


def _oeis():
    if "oeis_match" not in sys.modules:
        spec = importlib.util.spec_from_file_location("oeis_match", _OEIS_PATH)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["oeis_match"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["oeis_match"]


log = logging.getLogger(__name__)

MIN_SELECTOR_LEN = 5
COMPOSE_TERMS = 24
OEIS_PREFIX_LEN = 8
COMPOSE_DEPTH = 1
MAX_STATIC_PAIRS = 2500
MAX_ORBIT_BASE_PAIRS = 500


@dataclass(frozen=True, slots=True)
class ComposeSpec:
    composite_id: str
    base_seq_id: str
    selector_kind: str
    selector_ref: str
    selector_start: int | None
    stream: list[int]


def composite_id_index(base: str, selector_kind: str, selector_ref: str) -> str:
    return f"idx|{base}|{selector_kind}|{selector_ref}"


def ordered_membership(rows: list[tuple[int, int]]) -> list[int]:
    """Rows as (idx, n); return values in idx order (1-based)."""
    by_idx = {i: n for i, n in rows}
    if not by_idx:
        return []
    return [by_idx[i] for i in range(1, max(by_idx) + 1) if i in by_idx]


def membership_index_map(rows: list[tuple[int, int]]) -> dict[int, int]:
    return {i: n for i, n in rows}


def is_identity_indices(indices: list[int]) -> bool:
    return bool(indices) and indices == list(range(1, len(indices) + 1))


def viable_selector_indices(base_idx: dict[int, int], selector_indices: list[int]) -> list[int]:
    """Truncate C when indices exceed |B| — OEIS often names compositions with shorter prefixes."""
    if not base_idx:
        return []
    max_base = max(base_idx)
    out: list[int] = []
    for c in selector_indices[:COMPOSE_TERMS]:
        if c < 1 or c > max_base or c not in base_idx:
            break
        out.append(c)
    return out


def compose_index_stream(base_idx: dict[int, int], selector_indices: list[int]) -> list[int] | None:
    """a_k = B[selector_k]; selector_k is an index into B."""
    viable = viable_selector_indices(base_idx, selector_indices)
    if len(viable) < MIN_SELECTOR_LEN:
        return None
    return [base_idx[c] for c in viable]


def stream_equals_prefix(stream: list[int], other: list[int]) -> bool:
    return stream == other[: len(stream)]


def catalog_stream_exists(
    stream: list[int],
    catalog: dict[str, list[int]],
    *,
    exclude_seq: str | None = None,
) -> str | None:
    for seq_id, ordered in catalog.items():
        if seq_id == exclude_seq:
            continue
        if len(ordered) >= len(stream) and ordered[: len(stream)] == stream:
            return seq_id
    return None


def freeze_catalog(conn: Connection) -> tuple[list[str], dict[str, dict[int, int]], dict[str, list[int]]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT seq_id FROM sequences
            WHERE seq_id IN (SELECT DISTINCT seq_id FROM sequence_membership)
            ORDER BY seq_id
            """
        )
        seq_ids = [r[0] for r in cur.fetchall()]
        maps: dict[str, dict[int, int]] = {}
        ordered: dict[str, list[int]] = {}
        for sid in seq_ids:
            cur.execute(
                """
                SELECT idx, n FROM sequence_membership
                WHERE seq_id = %s
                ORDER BY idx
                """,
                (sid,),
            )
            rows = cur.fetchall()
            maps[sid] = membership_index_map(rows)
            ordered[sid] = ordered_membership(rows)
    return seq_ids, maps, ordered


def freeze_orbit_selectors(
    conn: Connection, min_steps: int = MIN_SELECTOR_LEN
) -> list[tuple[int, str, int, list[int]]]:
    """orbit_id, rel_type, start_n, selector indices (orbit n values)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT o.orbit_id, o.rel_type,
                   (SELECT n FROM orbits x WHERE x.orbit_id = o.orbit_id ORDER BY step LIMIT 1),
                   array_agg(o.n ORDER BY o.step) AS vals
            FROM orbits o
            GROUP BY o.orbit_id, o.rel_type
            HAVING COUNT(*) >= %s
            ORDER BY o.orbit_id
            """,
            (min_steps,),
        )
        out = []
        for oid, rel, start_n, vals in cur.fetchall():
            if vals and len(vals) >= MIN_SELECTOR_LEN:
                out.append((oid, rel, start_n, list(vals)))
        return out


def should_skip_pair(
    base_id: str,
    base_idx: dict[int, int],
    selector_indices: list[int],
    *,
    selector_kind: str,
    selector_ref: str,
    catalog_ordered: dict[str, list[int]],
) -> str | None:
    """Return skip reason or None if composition should proceed."""
    viable = viable_selector_indices(base_idx, selector_indices)
    if len(viable) < MIN_SELECTOR_LEN:
        return "selector_too_short"
    if is_identity_indices(viable):
        return "identity_indices"
    stream = compose_index_stream(base_idx, selector_indices)
    if not stream:
        return "compose_failed"
    if stream_equals_prefix(stream, catalog_ordered.get(base_id, [])):
        return "A_equals_B"
    if selector_kind == "sequence" and stream_equals_prefix(
        stream, catalog_ordered.get(selector_ref, [])
    ):
        return "A_equals_C"
    if selector_kind == "orbit" and stream_equals_prefix(stream, viable):
        return "A_equals_C"
    hit = catalog_stream_exists(
        stream,
        catalog_ordered,
        exclude_seq=base_id,
    )
    if hit:
        return f"catalog_tautology:{hit}"
    return None


def persist_composition(
    cur,
    run_id: int,
    spec: ComposeSpec,
    *,
    formula: str | None = None,
) -> None:
    cur.execute(
        """
        INSERT INTO sequence_compositions (
            composite_id, run_id, compose_kind, base_seq_id,
            selector_kind, selector_ref, selector_start, compose_depth, formula
        ) VALUES (%s, %s, 'compose_index', %s, %s, %s, %s, %s, %s)
        ON CONFLICT (composite_id) DO UPDATE
          SET formula = COALESCE(EXCLUDED.formula, sequence_compositions.formula)
        """,
        (
            spec.composite_id,
            run_id,
            spec.base_seq_id,
            spec.selector_kind,
            spec.selector_ref,
            spec.selector_start,
            COMPOSE_DEPTH,
            formula,
        ),
    )
    cur.execute("DELETE FROM composition_membership WHERE composite_id = %s", (spec.composite_id,))
    with cur.copy("COPY composition_membership (composite_id, idx, n) FROM STDIN") as copy:
        for i, val in enumerate(spec.stream, start=1):
            copy.write_row((spec.composite_id, i, val))


def search_composite(
    conn: Connection,
    spec: ComposeSpec,
    *,
    prefix_len: int = OEIS_PREFIX_LEN,
) -> list[Any]:
    om = _oeis()
    prefix = spec.stream[: min(prefix_len, om.MAX_PREFIX_VALUES)]
    if len(prefix) < 2:
        return []

    phash = om.prefix_hash(prefix)
    payload = om.load_cached_payload(conn, phash)
    if payload is None:
        payload = om.fetch_oeis_search(prefix)
        om._cache_put(phash, payload)

    orbit_start = spec.selector_start if spec.selector_kind == "orbit" else spec.stream[0]
    ranked = om.score_hits(prefix, payload.get("results") or [], orbit_start=orbit_start)

    with conn.cursor() as cur:
        cur.execute("DELETE FROM oeis_compose_candidates WHERE composite_id = %s", (spec.composite_id,))
        if not ranked:
            cur.execute(
                """
                INSERT INTO oeis_compose_candidates
                    (composite_id, candidate_id, oeis_id, oeis_name, prefix_len, confidence, raw_payload)
                VALUES (%s, 1, NULL, '', %s, 0, %s)
                """,
                (spec.composite_id, len(prefix), Jsonb({**payload, "prefix": prefix})),
            )
            return []

        for cid, row in enumerate(ranked, start=1):
            cur.execute(
                """
                INSERT INTO oeis_compose_candidates
                    (composite_id, candidate_id, oeis_id, oeis_name, prefix_len, confidence, raw_payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    spec.composite_id,
                    cid,
                    row["oeis_id"],
                    row["oeis_name"],
                    row["prefix_len"],
                    row["confidence"],
                    Jsonb(
                        {
                            **payload,
                            "prefix": prefix,
                            "compose": {
                                "base_seq_id": spec.base_seq_id,
                                "selector_kind": spec.selector_kind,
                                "selector_ref": spec.selector_ref,
                            },
                            "scoring": {
                                "match_kind": row["match_kind"],
                                "alignment_offset": row["alignment_offset"],
                                "query_coverage": row["query_coverage"],
                                "query_len": len(prefix),
                                "is_tautology": row["is_tautology"],
                            },
                            "top_hit": row["raw_hit"],
                        }
                    ),
                ),
            )
    return ranked  # list[dict] from oeis_match.score_hits


def generate_static_pairs(
    seq_ids: list[str],
    maps: dict[str, dict[int, int]],
    ordered: dict[str, list[int]],
) -> list[ComposeSpec]:
    specs: list[ComposeSpec] = []
    for base_id in seq_ids:
        base_idx = maps[base_id]
        if not base_idx:
            continue
        for sel_id in seq_ids:
            if sel_id == base_id:
                continue
            if len(specs) >= MAX_STATIC_PAIRS:
                return specs
            sel_indices = ordered.get(sel_id, [])
            reason = should_skip_pair(
                base_id,
                base_idx,
                sel_indices,
                selector_kind="sequence",
                selector_ref=sel_id,
                catalog_ordered=ordered,
            )
            if reason:
                continue
            stream = compose_index_stream(base_idx, sel_indices[:COMPOSE_TERMS])
            assert stream
            cid = composite_id_index(base_id, "sequence", sel_id)
            specs.append(
                ComposeSpec(
                    composite_id=cid,
                    base_seq_id=base_id,
                    selector_kind="sequence",
                    selector_ref=sel_id,
                    selector_start=None,
                    stream=stream,
                )
            )
    return specs


def generate_orbit_pairs(
    seq_ids: list[str],
    maps: dict[str, dict[int, int]],
    ordered: dict[str, list[int]],
    orbits: list[tuple[int, str, int, list[int]]],
) -> list[ComposeSpec]:
    specs: list[ComposeSpec] = []
    for base_id in seq_ids:
        base_idx = maps[base_id]
        if not base_idx:
            continue
        for orbit_id, rel, start_n, indices in orbits:
            if len(specs) >= MAX_ORBIT_BASE_PAIRS:
                return specs
            reason = should_skip_pair(
                base_id,
                base_idx,
                indices,
                selector_kind="orbit",
                selector_ref=str(orbit_id),
                catalog_ordered=ordered,
            )
            if reason:
                continue
            stream = compose_index_stream(base_idx, indices)
            assert stream
            cid = composite_id_index(base_id, "orbit", str(orbit_id))
            specs.append(
                ComposeSpec(
                    composite_id=cid,
                    base_seq_id=base_id,
                    selector_kind="orbit",
                    selector_ref=str(orbit_id),
                    selector_start=start_n,
                    stream=stream,
                )
            )
    return specs


def run_compose_pass(
    conn: Connection,
    *,
    static: bool = True,
    orbits: bool = True,
    search_oeis: bool = True,
    prefix_len: int = OEIS_PREFIX_LEN,
) -> dict[str, Any]:
    db.apply_schema(conn)
    seq_ids, maps, ordered = freeze_catalog(conn)
    if len(seq_ids) < 2:
        raise RuntimeError("need sequence_membership catalog (run seed-sequences / oeis-load)")

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO composition_runs (catalog_seq_ids, compose_kind, notes)
            VALUES (%s, 'compose_index', 'single-pass frozen catalog')
            RETURNING run_id
            """,
            (seq_ids,),
        )
        run_id = cur.fetchone()[0]

    specs: list[ComposeSpec] = []
    if static:
        specs.extend(generate_static_pairs(seq_ids, maps, ordered))
    if orbits:
        orbit_list = freeze_orbit_selectors(conn)
        specs.extend(generate_orbit_pairs(seq_ids, maps, ordered, orbit_list))

    stats = {
        "run_id": run_id,
        "catalog_size": len(seq_ids),
        "composed": 0,
        "searched": 0,
        "identifications": 0,
        "orbit_specs": sum(1 for s in specs if s.selector_kind == "orbit"),
        "static_specs": sum(1 for s in specs if s.selector_kind == "sequence"),
    }

    with conn.cursor() as cur:
        for spec in specs:
            formula = None
            if spec.selector_kind == "orbit":
                formula = f"compose_index({spec.base_seq_id}, orbit {spec.selector_ref})"
            persist_composition(cur, run_id, spec, formula=formula)
            stats["composed"] += 1
            if search_oeis:
                ranked = search_composite(conn, spec, prefix_len=prefix_len)
                stats["searched"] += 1
                if ranked and ranked[0].get("match_kind") == "identification":
                    stats["identifications"] += 1

    return stats


def main() -> None:
    import argparse
    import os

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="compose_match")
    p.add_argument("--dsn", default=os.environ.get("CALX_DSN") or os.environ.get("ARITHMETIC_DB_DSN"))
    p.add_argument("--static-only", action="store_true")
    p.add_argument("--orbit-only", action="store_true")
    p.add_argument("--no-oeis", action="store_true", help="materialize only, skip OEIS search")
    p.add_argument("--prefix", type=int, default=OEIS_PREFIX_LEN)
    args = p.parse_args()

    static = not args.orbit_only
    orbits = not args.static_only

    with db.connect(args.dsn) as conn:
        stats = run_compose_pass(
            conn,
            static=static,
            orbits=orbits,
            search_oeis=not args.no_oeis,
            prefix_len=args.prefix,
        )
    print(
        f"run {stats['run_id']}: catalog={stats['catalog_size']} "
        f"composed={stats['composed']} (static={stats['static_specs']} orbit={stats['orbit_specs']}) "
        f"searched={stats['searched']} identifications={stats['identifications']}"
    )


if __name__ == "__main__":
    main()
