"""Bipartite BFS over the (numbers ↔ sequences) membership graph.

Picks random candidates from the Collatz, Recaman, and aliquot families,
then traces relationships up to depth 10 — alternating number→sequence
and sequence→number edges — and emits a markdown report.

Pruning rules (otherwise the squarefree sequence alone would flood layer 2):
  - At number→sequence expansion: take all sequences containing the number.
  - At sequence→number expansion: only "bridge" numbers (n with in_count ≥ 2,
    where in_count = number of distinct sequences containing n).
  - Per-layer cap of CAP_PER_LAYER (default 8) keeps the report human-scale.
    Ranking: at NUMBER layers, by in_count desc; at SEQUENCE layers, by
    membership_size asc (smaller, more selective sequences first).
"""

from __future__ import annotations

import datetime as dt
import os
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from calx import db

REPORT_PATH = Path(__file__).resolve().parents[1] / "reports" / "relationship_report.md"
RAW_PATH    = Path(__file__).resolve().parents[1] / "reports" / "relationship_raw.json"

CAP_PER_LAYER = 8
MAX_DEPTH     = 10
SEED          = 20260515  # reproducibility


# ─── DB access ──────────────────────────────────────────────────────────────

@dataclass
class Graph:
    seq_meta: dict[str, dict]            # seq_id -> {name, seq_type, formula, size}
    seq_members: dict[str, set[int]]     # seq_id -> {n, ...}
    num_in: dict[int, set[str]]          # n -> {seq_id, ...}


def load_graph(conn) -> Graph:
    seq_meta: dict[str, dict] = {}
    seq_members: dict[str, set[int]] = defaultdict(set)
    num_in: dict[int, set[str]] = defaultdict(set)

    with conn.cursor() as cur:
        cur.execute("SELECT seq_id, name, seq_type, formula FROM sequences ORDER BY seq_id")
        for sid, name, kind, formula in cur.fetchall():
            seq_meta[sid] = {"name": name, "seq_type": kind, "formula": formula, "size": 0}

        cur.execute("SELECT seq_id, n FROM sequence_membership")
        for sid, n in cur.fetchall():
            seq_members[sid].add(n)
            num_in[n].add(sid)

        for sid in seq_meta:
            seq_meta[sid]["size"] = len(seq_members.get(sid, ()))

    return Graph(seq_meta=seq_meta, seq_members=dict(seq_members), num_in=dict(num_in))


def fact_of(conn, n: int) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT signature FROM prime_signatures WHERE n = %s", (n,))
        row = cur.fetchone()
    return row[0] if row else f"{n}"


def integer_facts(conn, n: int) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT is_prime, omega, big_omega, is_squarefree FROM integers WHERE n = %s",
            (n,),
        )
        row = cur.fetchone()
    if not row:
        return {}
    is_prime, omega, big_omega, sqf = row
    sig = fact_of(conn, n)
    return {
        "n": n,
        "factorization": sig,
        "is_prime": is_prime,
        "omega": omega,
        "big_omega": big_omega,
        "is_squarefree": sqf,
    }


# ─── BFS ────────────────────────────────────────────────────────────────────

def bfs(graph: Graph, start: int, max_depth: int = MAX_DEPTH, cap: int = CAP_PER_LAYER):
    """Bipartite BFS. Layer 0=numbers, alternating. Returns:
        layers: list of (kind, [items]) where kind ∈ {'NUM','SEQ'}
        edges:  list of (kind, src, dst, layer)
    """
    seen_num: set[int] = {start}
    seen_seq: set[str] = set()
    layers: list[tuple[str, list]] = [("NUM", [start])]
    edges: list[tuple[str, object, object, int]] = []

    current_kind = "NUM"
    current_items: list = [start]

    for depth in range(1, max_depth + 1):
        next_kind = "SEQ" if current_kind == "NUM" else "NUM"
        cand: dict[object, int] = {}            # candidate -> rank score
        contributors: dict[object, list] = {}    # candidate -> [sources that found it]

        if current_kind == "NUM":
            # NUM -> SEQ: expand each number to its sequences (minus seen)
            for n in current_items:
                for sid in graph.num_in.get(n, ()):
                    if sid in seen_seq:
                        continue
                    size = graph.seq_meta[sid]["size"]
                    if size < cand.get(sid, 10**12):
                        cand[sid] = size
                    contributors.setdefault(sid, []).append(n)
        else:
            # SEQ -> NUM: bridge numbers only (in_count ≥ 2)
            for sid in current_items:
                for n in graph.seq_members.get(sid, ()):
                    if n in seen_num:
                        continue
                    in_count = len(graph.num_in.get(n, ()))
                    if in_count < 2:
                        continue
                    score = -in_count
                    if score < cand.get(n, 10**12):
                        cand[n] = score
                    contributors.setdefault(n, []).append(sid)

        if not cand:
            break

        ordered = sorted(cand.items(), key=lambda kv: (kv[1], _key(kv[0])))
        chosen = [k for k, _ in ordered[:cap]]
        if next_kind == "SEQ":
            seen_seq.update(chosen)
        else:
            seen_num.update(chosen)

        # Record only surviving edges (one per chosen node, from its first source).
        for c in chosen:
            src = contributors[c][0]
            kind = "NUM->SEQ" if current_kind == "NUM" else "SEQ->NUM"
            edges.append((kind, src, c, depth))

        layers.append((next_kind, chosen))
        current_kind = next_kind
        current_items = chosen

    return layers, edges


def _key(x):
    """Tie-breaker: numbers ascending, strings lexicographic."""
    if isinstance(x, int):
        return (0, x)
    return (1, x)


# ─── Reporting ──────────────────────────────────────────────────────────────

def fmt_seq(sid: str, graph: Graph) -> str:
    meta = graph.seq_meta[sid]
    return f"`{sid}` *{meta['name']}* (|S|={meta['size']:,})"


def fmt_num(n: int, graph: Graph) -> str:
    in_count = len(graph.num_in.get(n, ()))
    return f"**{n}** (∈{in_count} seq)"


def write_candidate_section(out, conn, graph: Graph, source: str, start: int):
    facts = integer_facts(conn, start)
    sig = facts.get("factorization", str(start))
    flags = []
    if facts.get("is_prime"):
        flags.append("prime")
    elif facts.get("big_omega") == 2:
        flags.append("semiprime")
    if facts.get("is_squarefree"):
        flags.append("squarefree")
    flag_str = ", ".join(flags) if flags else "—"

    out.append(f"## {start} — drawn from {source}\n")
    out.append(f"- factorization: `{sig}`")
    out.append(f"- ω(n) = {facts.get('omega')}, Ω(n) = {facts.get('big_omega')}, flags: {flag_str}")
    out.append(f"- direct sequence memberships: **{len(graph.num_in.get(start, ()))}**\n")

    layers, edges = bfs(graph, start)

    out.append("### Layer-by-layer expansion (depth ≤ 10)\n")
    for depth, (kind, items) in enumerate(layers):
        if depth == 0:
            out.append(f"- **Depth 0** (NUM): {fmt_num(start, graph)}")
            continue
        if kind == "SEQ":
            shown = ", ".join(fmt_seq(s, graph) for s in items)
        else:
            shown = ", ".join(fmt_num(n, graph) for n in items)
        out.append(f"- **Depth {depth}** ({kind}): {shown}")

    if edges:
        out.append("\n<details><summary>Raw edges</summary>\n")
        for kind, src, dst, depth in edges:
            out.append(f"- d={depth}  {kind}  `{src}` → `{dst}`")
        out.append("</details>\n")
    out.append("")
    return layers, edges


def overall_observations(out, conn, graph: Graph, all_layers, all_edges, candidates):
    out.append("## General observations\n")

    # Sequence reach: how often each sequence appeared across all candidate BFS trees
    seq_appearance = defaultdict(int)
    num_appearance = defaultdict(int)
    for layers in all_layers:
        for kind, items in layers:
            for x in items:
                if kind == "SEQ":
                    seq_appearance[x] += 1
                else:
                    num_appearance[x] += 1

    top_seqs = sorted(seq_appearance.items(), key=lambda kv: -kv[1])[:10]
    out.append(f"**Sequences reached by the most candidates** "
               f"(of {len(candidates)} starting points):")
    for sid, cnt in top_seqs:
        out.append(f"- {cnt}×  {fmt_seq(sid, graph)}")
    out.append("")

    top_nums = sorted(
        ((n, c) for n, c in num_appearance.items() if n not in candidates),
        key=lambda kv: -kv[1],
    )[:10]
    out.append("**Most-revisited bridge numbers across all BFS trees** (excluding starts):")
    for n, cnt in top_nums:
        sig = fact_of(conn, n)
        seqs = sorted(graph.num_in.get(n, ()), key=lambda s: graph.seq_meta[s]["size"])
        out.append(f"- {cnt}×  **{n}** = `{sig}` — {len(seqs)} seq: "
                   + ", ".join(f"`{s}`" for s in seqs[:6])
                   + (" …" if len(seqs) > 6 else ""))
    out.append("")

    # Pairwise characterize_relation between starting candidates
    out.append("**Pairwise algebraic relations between the starting candidates:**")
    with conn.cursor() as cur:
        for i, a in enumerate(candidates):
            for b in candidates[i+1:]:
                cur.execute(
                    "SELECT rel_type, description FROM characterize_relation(%s, %s) "
                    "WHERE rel_type IN ('DIVISOR','MULTIPLE','SIGNATURE_TWIN',"
                    "'OMEGA_EQUAL','BIG_OMEGA_EQUAL','BOTH_SQUAREFREE','CRT_CLASS') "
                    "ORDER BY rel_type",
                    (a, b),
                )
                rels = cur.fetchall()
                if rels:
                    out.append(f"- ({a}, {b}):")
                    for rt, desc in rels:
                        out.append(f"    - {rt}: {desc}")
    out.append("")

    # Catalog summary
    out.append("**Sequence catalog summary:**\n")
    out.append("| seq_id | name | type | size |")
    out.append("|--------|------|------|------|")
    for sid in sorted(graph.seq_meta, key=lambda s: (-graph.seq_meta[s]["size"], s)):
        m = graph.seq_meta[sid]
        out.append(f"| `{sid}` | {m['name']} | {m['seq_type']} | {m['size']:,} |")


# ─── Driver ─────────────────────────────────────────────────────────────────

def pick_candidates(graph: Graph, rng: random.Random):
    """One random member each from a Collatz orbit, Recaman, and an aliquot orbit,
    repeated 3× → 9 candidates."""
    families = [
        ("Collatz",  [s for s in graph.seq_meta if s.startswith("collatz_")]),
        ("Recaman",  ["A005132"]),
        ("aliquot",  [s for s in graph.seq_meta if s.startswith("aliquot_")]),
    ]
    picks = []
    for fam_name, sids in families:
        for _ in range(3):
            sid = rng.choice(sids)
            n = rng.choice(sorted(graph.seq_members[sid]))
            picks.append((fam_name, sid, n))
    return picks


def main():
    dsn = os.environ.get(
        "CALX_DSN",
        "postgresql://trunk:trunk@localhost:5434/trunk",
    )
    rng = random.Random(SEED)

    with db.connect(dsn) as conn:
        graph = load_graph(conn)

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), MAX(n) FROM integers")
            n_count, n_max = cur.fetchone()

        picks = pick_candidates(graph, rng)

        out: list[str] = []
        out.append(f"# Cross-Sequence Relationship Report\n")
        out.append(f"_Generated {dt.datetime.now():%Y-%m-%d %H:%M}_  ")
        out.append(f"_Integers populated: 1..{n_max:,} ({n_count:,} rows)_  ")
        out.append(f"_Sequences catalog: {len(graph.seq_meta)} sequences, "
                   f"{sum(len(v) for v in graph.seq_members.values()):,} memberships_  ")
        out.append(f"_BFS depth: {MAX_DEPTH}, cap per layer: {CAP_PER_LAYER}, "
                   f"RNG seed: {SEED}_\n")
        out.append("Each candidate is a random member of a Collatz / Recaman / aliquot ")
        out.append("sequence. Layer 0 is the candidate itself; odd layers list sequences ")
        out.append("containing the prior layer's numbers; even layers list bridge numbers ")
        out.append("(n ∈ ≥2 sequences) appearing in the prior layer's sequences.\n")

        all_layers, all_edges = [], []
        for fam, sid, n in picks:
            source = f"family={fam}, source seq=`{sid}`"
            layers, edges = write_candidate_section(out, conn, graph, source, n)
            all_layers.append(layers)
            all_edges.append(edges)

        candidates = [p[2] for p in picks]
        overall_observations(out, conn, graph, all_layers, all_edges, candidates)

        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text("\n".join(out), encoding="utf-8")

        import json
        raw = {
            "n_max": n_max,
            "n_count": n_count,
            "candidates": [{"family": f, "seq_id": s, "n": n} for f, s, n in picks],
            "trees": [
                {
                    "candidate": picks[i][2],
                    "layers": [
                        {"kind": k, "items": items}
                        for k, items in all_layers[i]
                    ],
                    "edges": [
                        {"kind": k, "src": s, "dst": d, "depth": dep}
                        for k, s, d, dep in all_edges[i]
                    ],
                }
                for i in range(len(picks))
            ],
            "catalog": [
                {"seq_id": sid, **graph.seq_meta[sid]}
                for sid in sorted(graph.seq_meta)
            ],
        }
        RAW_PATH.write_text(json.dumps(raw, indent=2, default=str), encoding="utf-8")

    print(f"wrote {REPORT_PATH}")
    print(f"wrote {RAW_PATH}")


if __name__ == "__main__":
    main()
