"""OEIS prefix search for dynamical orbit traces.

Queries oeis.org/search with the leading values of an orbit, scores hits by
prefix agreement, persists ranked candidates, and optionally mirrors strong
matches into sequence_membership.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Literal

from psycopg import Connection
from psycopg.types.json import Jsonb

log = logging.getLogger(__name__)

USER_AGENT = (
    "calx OEIS orbit matcher (educational; prefix search; oeis.org/search)"
)
RATE_LIMIT_S = 1.0
MAX_RETRIES = 1
TIMEOUT_S = 30
MAX_PREFIX_VALUES = 8
MAX_ALIGN_OFFSET = 12
MIN_MATCH_TERMS = 3
# Orbits shorter than this cannot yield ``identification`` — no padding to fill prefix.
MIN_IDENTIFICATION_QUERY_LEN = 4
MIN_STORE_CONFIDENCE = 0.35
IDENTIFICATION_CONFIDENCE = 0.9
SUGGESTIVE_CONFIDENCE = 0.6
MEMBERSHIP_SYNC_CONFIDENCE = IDENTIFICATION_CONFIDENCE
MAX_CANDIDATES = 10
TAUTOLOGY_CONFIDENCE_CAP = 0.55

# Ultra-generic sequences that match many arbitrary prefixes by chance.
GENERIC_OEIS_IDS = frozenset({"A000001", "A000027"})

MatchKind = Literal["identification", "suggestive", "coincidence", "tautology"]

ALIQUOT_START_RE = re.compile(
    r"(?:aliquot|aliqout)\s+sequence\s+starting\s+at\s+(\d+)",
    re.IGNORECASE,
)
TRAJECTORY_OF_RE = re.compile(r"trajectory\s+of\s+(\d+)\s+under", re.IGNORECASE)
# Constant/digit streams, not integer trajectories — high false-positive rate on short prefixes.
NON_SEQUENCE_RE = re.compile(
    r"decimal expansion|digits of|continued fraction expansion|"
    r"expansion of (?:log|ln)_",
    re.IGNORECASE,
)

_last_fetch_at: float = 0.0
_prefix_cache: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True, slots=True)
class Match:
    orbit_id: int
    candidate_id: int
    oeis_id: str | None
    oeis_name: str
    prefix_len: int
    confidence: float
    match_kind: str = "coincidence"


@dataclass(frozen=True, slots=True)
class AlignmentScore:
    """How well an OEIS ``data`` preview aligns with the orbit prefix."""

    matched: int
    offset: int
    query_coverage: float
    confidence: float
    match_kind: MatchKind
    is_tautology: bool
    preview_truncated: bool


def prefix_hash(values: list[int]) -> str:
    joined = ",".join(str(v) for v in values)
    return hashlib.sha256(joined.encode()).hexdigest()[:32]


def oeis_id_from_hit(hit: dict[str, Any]) -> str:
    num = hit.get("number")
    if num is not None:
        return f"A{int(num):06d}"
    raise ValueError(f"OEIS hit missing number field: {hit!r}")


def parse_oeis_data(data: str) -> list[int]:
    out: list[int] = []
    for part in data.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            break
    return out


def parse_oeis_offset(hit: dict[str, Any]) -> int:
    """First index listed in OEIS ``offset`` (e.g. ``'1'`` or ``'0,1'``)."""
    raw = hit.get("offset")
    if raw is None:
        return 1
    text = str(raw).strip()
    if not text:
        return 1
    head = text.split(",", 1)[0].strip()
    try:
        return int(head)
    except ValueError:
        return 1


def indexed_trajectory_start(name: str) -> int | None:
    """If the OEIS name defines a trajectory by its starting value, return that N."""
    for pat in (ALIQUOT_START_RE, TRAJECTORY_OF_RE):
        m = pat.search(name)
        if m:
            return int(m.group(1))
    return None


def is_tautological_aliquot(name: str, orbit_start: int | None) -> bool:
    """Indexed trajectory whose catalogued start equals this orbit's start."""
    indexed = indexed_trajectory_start(name)
    return indexed is not None and orbit_start is not None and indexed == orbit_start


def indexed_trajectory_kind(name: str, orbit_start: int | None) -> MatchKind | None:
    """Indexed trajectory entries never qualify as structural identification."""
    indexed = indexed_trajectory_start(name)
    if indexed is None:
        return None
    if orbit_start is not None and indexed == orbit_start:
        return "tautology"
    return "coincidence"


def is_metadata_sequence(name: str) -> bool:
    """OEIS entries whose ``data`` is not an integer sequence of values."""
    return bool(NON_SEQUENCE_RE.search(name))


def apply_query_length_floor(kind: MatchKind, conf: float, query_len: int) -> MatchKind:
    """Short orbit prefixes cannot support structural identification."""
    if query_len >= MIN_IDENTIFICATION_QUERY_LEN:
        return kind
    if kind == "identification":
        return "suggestive" if conf >= SUGGESTIVE_CONFIDENCE else "coincidence"
    return kind


def classify_confidence(conf: float, *, tautology: bool) -> MatchKind:
    if tautology:
        return "tautology"
    if conf >= IDENTIFICATION_CONFIDENCE:
        return "identification"
    if conf >= SUGGESTIVE_CONFIDENCE:
        return "suggestive"
    return "coincidence"


def score_alignment(
    query: list[int],
    seq: list[int],
    *,
    orbit_start: int | None,
    oeis_id: str,
    oeis_name: str,
    oeis_offset: int = 1,
) -> AlignmentScore:
    """Best contiguous alignment of *query* inside the OEIS data preview."""
    if not query or not seq:
        return AlignmentScore(0, 0, 0.0, 0.0, "coincidence", False, False)

    best_matched = 0
    best_offset = 0
    # OEIS ``data`` is usually anchored at ``offset``; try a small window around it.
    anchor = max(0, oeis_offset)
    max_off = min(len(seq), MAX_ALIGN_OFFSET)
    offsets = set(range(max_off + 1))
    if anchor <= len(seq):
        offsets.add(min(anchor, len(seq) - 1) if seq else 0)

    for off in sorted(offsets):
        matched = 0
        for i, q in enumerate(query):
            j = off + i
            if j >= len(seq) or seq[j] != q:
                break
            matched += 1
        if matched > best_matched or (matched == best_matched and off < best_offset):
            best_matched = matched
            best_offset = off

    query_cov = best_matched / len(query)
    preview_truncated = best_matched >= len(seq) and len(seq) < len(query)

    # Require a minimum run of agreeing terms; scale up softly below the bar.
    length_factor = min(1.0, best_matched / MIN_MATCH_TERMS) if MIN_MATCH_TERMS else 1.0
    conf = query_cov * length_factor

    # Penalize only alignments that start mid-preview without anchoring at query[0].
    if best_offset > 0 and (best_offset >= len(seq) or seq[best_offset] != query[0]):
        conf *= 1.0 / (1.0 + 0.15 * best_offset)

    if preview_truncated:
        conf *= len(seq) / len(query)

    if oeis_id in GENERIC_OEIS_IDS:
        conf *= 0.5

    if is_metadata_sequence(oeis_name):
        conf = min(conf, 0.45)

    indexed_kind = indexed_trajectory_kind(oeis_name, orbit_start)
    tautology = indexed_kind == "tautology"
    if indexed_kind == "tautology":
        conf = min(conf, TAUTOLOGY_CONFIDENCE_CAP)
    elif indexed_kind == "coincidence":
        # Same prefix as another start's indexed trajectory — not structural.
        conf = min(conf, 0.45)

    kind = indexed_kind if indexed_kind is not None else classify_confidence(conf, tautology=tautology)
    if is_metadata_sequence(oeis_name) and kind == "identification":
        kind = "coincidence"
    kind = apply_query_length_floor(kind, conf, len(query))
    return AlignmentScore(
        matched=best_matched,
        offset=best_offset,
        query_coverage=query_cov,
        confidence=conf,
        match_kind=kind,
        is_tautology=tautology,
        preview_truncated=preview_truncated,
    )


def prefix_confidence(query: list[int], seq: list[int]) -> tuple[int, float]:
    """Backward-compatible wrapper: alignment at offset 0 only."""
    scored = score_alignment(
        query,
        seq,
        orbit_start=query[0] if query else None,
        oeis_id="",
        oeis_name="",
        oeis_offset=0,
    )
    return scored.matched, scored.confidence


def score_hits(
    query: list[int],
    hits: list[dict[str, Any]],
    *,
    orbit_start: int | None = None,
) -> list[dict[str, Any]]:
    start = orbit_start if orbit_start is not None else (query[0] if query else None)
    scored: list[dict[str, Any]] = []
    for hit in hits:
        seq = parse_oeis_data(hit.get("data") or "")
        if not seq:
            continue
        oeis_id = oeis_id_from_hit(hit)
        oeis_name = (hit.get("name") or "").strip()
        align = score_alignment(
            query,
            seq,
            orbit_start=start,
            oeis_id=oeis_id,
            oeis_name=oeis_name,
            oeis_offset=parse_oeis_offset(hit),
        )
        if align.matched < 2 or align.confidence < MIN_STORE_CONFIDENCE:
            continue
        scored.append(
            {
                "oeis_id": oeis_id,
                "oeis_name": oeis_name,
                "prefix_len": align.matched,
                "confidence": align.confidence,
                "match_kind": align.match_kind,
                "alignment_offset": align.offset,
                "query_coverage": align.query_coverage,
                "is_tautology": align.is_tautology,
                "preview_truncated": align.preview_truncated,
                "query_len": len(query),
                "raw_hit": hit,
            }
        )

    def sort_key(r: dict[str, Any]) -> tuple:
        taut = 1 if r["is_tautology"] else 0
        return (-r["confidence"], taut, -r["prefix_len"], r["alignment_offset"], r["oeis_id"])

    scored.sort(key=sort_key)
    return scored[:MAX_CANDIDATES]


def _rate_limit() -> None:
    global _last_fetch_at
    elapsed = time.monotonic() - _last_fetch_at
    if elapsed < RATE_LIMIT_S:
        time.sleep(RATE_LIMIT_S - elapsed)
    _last_fetch_at = time.monotonic()


def fetch_oeis_search(values: list[int]) -> dict[str, Any]:
    """GET oeis.org/search JSON for comma-separated prefix. One retry on failure."""
    q = ",".join(str(v) for v in values)
    url = f"https://oeis.org/search?q={urllib.parse.quote(q)}&fmt=json&start=0"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    _rate_limit()

    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(body)
            if isinstance(parsed, list):
                return {"results": parsed, "prefix": values, "prefix_hash": prefix_hash(values)}
            if isinstance(parsed, dict):
                parsed.setdefault("prefix", values)
                parsed.setdefault("prefix_hash", prefix_hash(values))
                return parsed
            return {
                "results": [],
                "prefix": values,
                "prefix_hash": prefix_hash(values),
                "parse_error": f"unexpected JSON type {type(parsed).__name__}",
            }
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            if attempt < MAX_RETRIES:
                time.sleep(5)
                continue
    return {
        "results": [],
        "prefix": values,
        "prefix_hash": prefix_hash(values),
        "fetch_error": str(last_err),
    }


def _cache_get(phash: str) -> dict[str, Any] | None:
    if phash in _prefix_cache:
        return _prefix_cache[phash]
    return None


def _cache_put(phash: str, payload: dict[str, Any]) -> None:
    _prefix_cache[phash] = payload


def load_cached_payload(conn: Connection, phash: str) -> dict[str, Any] | None:
    mem = _cache_get(phash)
    if mem is not None:
        return mem
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT raw_payload
            FROM oeis_match_candidates
            WHERE raw_payload->>'prefix_hash' = %s
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            (phash,),
        )
        row = cur.fetchone()
    if row and row[0]:
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        _cache_put(phash, payload)
        return payload
    return None


def orbit_prefix(conn: Connection, orbit_id: int, prefix_len: int) -> list[int]:
    cap = min(prefix_len, MAX_PREFIX_VALUES)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT n FROM orbits
            WHERE orbit_id = %s
            ORDER BY step
            LIMIT %s
            """,
            (orbit_id, cap),
        )
        return [r[0] for r in cur.fetchall()]


def _delete_candidates(cur, orbit_id: int) -> None:
    cur.execute("DELETE FROM oeis_match_candidates WHERE orbit_id = %s", (orbit_id,))


def _persist_candidates(
    cur,
    orbit_id: int,
    prefix: list[int],
    payload: dict[str, Any],
    ranked: list[dict[str, Any]],
) -> list[Match]:
    _delete_candidates(cur, orbit_id)
    matches: list[Match] = []
    if not ranked:
        cur.execute(
            """
            INSERT INTO oeis_match_candidates
                (orbit_id, candidate_id, oeis_id, oeis_name, prefix_len, confidence, raw_payload)
            VALUES (%s, 1, NULL, '', %s, 0, %s)
            """,
            (orbit_id, len(prefix), Jsonb(payload)),
        )
        return matches

    for cid, row in enumerate(ranked, start=1):
        cur.execute(
            """
            INSERT INTO oeis_match_candidates
                (orbit_id, candidate_id, oeis_id, oeis_name, prefix_len, confidence, raw_payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                orbit_id,
                cid,
                row["oeis_id"],
                row["oeis_name"],
                row["prefix_len"],
                row["confidence"],
                Jsonb(
                    {
                        **payload,
                        "top_hit": row["raw_hit"],
                        "scoring": {
                            "match_kind": row["match_kind"],
                            "alignment_offset": row["alignment_offset"],
                            "query_coverage": row["query_coverage"],
                            "query_len": len(prefix),
                            "is_tautology": row["is_tautology"],
                            "preview_truncated": row["preview_truncated"],
                        },
                    }
                ),
            ),
        )
        matches.append(
            Match(
                orbit_id=orbit_id,
                candidate_id=cid,
                oeis_id=row["oeis_id"],
                oeis_name=row["oeis_name"],
                prefix_len=row["prefix_len"],
                confidence=row["confidence"],
                match_kind=row["match_kind"],
            )
        )
    return matches


def sync_sequence_membership(conn: Connection, orbit_id: int, oeis_id: str, oeis_name: str) -> int:
    """Mirror orbit integers into sequence_membership for a strong OEIS hit."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sequences (seq_id, name, seq_type, formula, family)
            VALUES (%s, %s, 'recursive', %s, 'orbit')
            ON CONFLICT (seq_id) DO UPDATE
              SET name = EXCLUDED.name,
                  formula = COALESCE(sequences.formula, EXCLUDED.formula),
                  family = 'orbit'
            """,
            (oeis_id, oeis_name[:500], f"OEIS orbit trace match for orbit_id={orbit_id}"),
        )
        cur.execute(
            """
            SELECT step, n FROM orbits
            WHERE orbit_id = %s
            ORDER BY step
            """,
            (orbit_id,),
        )
        rows = cur.fetchall()
        if not rows:
            return 0
        cur.execute(
            "DELETE FROM sequence_membership WHERE seq_id = %s AND n = ANY(%s)",
            (oeis_id, [r[1] for r in rows]),
        )
        with cur.copy("COPY sequence_membership (seq_id, n, idx) FROM STDIN") as copy:
            for step, n in rows:
                copy.write_row((oeis_id, n, step + 1))
        return len(rows)


def search_orbit(
    conn: Connection,
    orbit_id: int,
    prefix_len: int = 8,
    *,
    sync_membership: bool = True,
) -> list[Match]:
    """Take orbit's first prefix_len values, search OEIS, persist + return ranked candidates."""
    prefix = orbit_prefix(conn, orbit_id, prefix_len)
    if len(prefix) < 2:
        log.warning("orbit %s too short (%s values); skipping", orbit_id, len(prefix))
        return []
    if len(prefix) < MIN_IDENTIFICATION_QUERY_LEN:
        log.debug(
            "orbit %s: %s-term prefix (identification requires %s+)",
            orbit_id,
            len(prefix),
            MIN_IDENTIFICATION_QUERY_LEN,
        )

    phash = prefix_hash(prefix)
    payload = load_cached_payload(conn, phash)
    if payload is None:
        payload = fetch_oeis_search(prefix)
        _cache_put(phash, payload)

    hits = payload.get("results") or []
    ranked = score_hits(
        prefix,
        hits if isinstance(hits, list) else [],
        orbit_start=prefix[0],
    )

    with conn.cursor() as cur:
        matches = _persist_candidates(cur, orbit_id, prefix, payload, ranked)
        top = ranked[0] if ranked else None
        if (
            sync_membership
            and top
            and top["match_kind"] == "identification"
            and top["confidence"] >= MEMBERSHIP_SYNC_CONFIDENCE
        ):
            sync_sequence_membership(conn, orbit_id, top["oeis_id"], top["oeis_name"])

    return matches


def search_all_orbits(
    conn: Connection,
    min_length: int = 4,
    prefix_len: int = 8,
    *,
    sync_membership: bool = True,
) -> int:
    """Search every orbit with at least min_length steps. Returns count searched."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT orbit_id
            FROM orbits
            GROUP BY orbit_id
            HAVING COUNT(*) >= %s
            ORDER BY orbit_id
            """,
            (min_length,),
        )
        orbit_ids = [r[0] for r in cur.fetchall()]

    for oid in orbit_ids:
        search_orbit(conn, oid, prefix_len, sync_membership=sync_membership)
    return len(orbit_ids)


def matches_for(conn: Connection, n: int) -> list[tuple[int, str, float]]:
    """Orbits containing n that have OEIS match rows (best confidence per orbit)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (o.orbit_id)
                o.orbit_id, m.oeis_id, m.confidence
            FROM orbits o
            JOIN oeis_match_candidates m USING (orbit_id)
            WHERE o.n = %s
              AND m.oeis_id IS NOT NULL
            ORDER BY o.orbit_id, m.confidence DESC, m.candidate_id
            """,
            (n,),
        )
        return [(r[0], r[1], float(r[2])) for r in cur.fetchall()]


def main() -> None:
    import argparse
    import os

    from calx import db

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    p = argparse.ArgumentParser(prog="oeis_match")
    p.add_argument("--dsn", default=os.environ.get("CALX_DSN") or os.environ.get("ARITHMETIC_DB_DSN"))
    p.add_argument("--orbit-id", type=int, help="search one orbit")
    p.add_argument("--all", action="store_true", help="search all qualifying orbits")
    p.add_argument("--min-length", type=int, default=4)
    p.add_argument("--prefix", type=int, default=8)
    p.add_argument(
        "--no-sync-membership",
        action="store_true",
        help="do not mirror strong matches into sequence_membership",
    )
    args = p.parse_args()

    if not args.orbit_id and not args.all:
        p.error("specify --orbit-id ID or --all")

    with db.connect(args.dsn) as conn:
        db.apply_schema(conn)
        sync = not args.no_sync_membership
        if args.orbit_id:
            hits = search_orbit(conn, args.orbit_id, args.prefix, sync_membership=sync)
            for m in hits:
                print(
                    f"  #{m.candidate_id} {m.oeis_id} [{m.match_kind}] "
                    f"conf={m.confidence:.3f} prefix={m.prefix_len} — {m.oeis_name[:72]}"
                )
            if not hits:
                print(f"  orbit {args.orbit_id}: no OEIS candidates above threshold")
        else:
            n = search_all_orbits(
                conn, args.min_length, args.prefix, sync_membership=sync
            )
            print(f"searched {n} orbits (min_length={args.min_length}, prefix={args.prefix})")


if __name__ == "__main__":
    main()
