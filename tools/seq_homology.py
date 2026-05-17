"""Gap-pattern homology of sequences, at depth.

For every sequence in the unified OEIS layer (calx.sequences + kan.sequence_terms):

  * build the difference tower  delta^0 A, delta^1 A, delta^2 A
  * at each order, build the verified Erdos gap-pattern complex and compute H1
  * count 3-gap "hyperedge" commuting closures (multi-gap co-occurrence beyond
    the pairwise 2-cell squares the original construction stopped at)
  * persist (seq, order) -> (V, E, squares, hyper3, H1, d1.d2=0) into
    kan.sequence_homology

The H1 signature vector across difference orders is the deep similarity axis:
two sequences agreeing there are "similar at depth" even when their term
prefixes never coincide -- structure the original prefix scan cannot see.

Reads/writes the live unified DB. numpy only. Idempotent (upsert per (seq,order)).
"""

from __future__ import annotations

import itertools
import os
import sys

import numpy as np
import psycopg

PG_DSN = os.environ.get(
    "CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk"
)

MAX_DIFF_ORDER = 2          # delta^0, delta^1, delta^2
HYPER_GAP_CAP = 16          # top-K frequent gaps used for 3-gap enumeration


# ---- vendored Erdos gap-pattern construction (faithful to proofs/) ----------

def gap_set(A: list[int]) -> set[int]:
    A = sorted(set(A))
    return {A[i + 1] - A[i] for i in range(len(A) - 1)}


def build_complex(A: list[int]):
    verts = sorted(set(A))
    vset = set(verts)
    gaps = gap_set(A)

    edges = []
    for i in verts:
        for g in gaps:
            j = i + g
            if j in vset:
                edges.append((i, j, g))
    eidx = {e: k for k, e in enumerate(edges)}

    squares = []
    sg = sorted(gaps)
    for a in range(len(sg)):
        for b in range(a + 1, len(sg)):
            for gh, gv in ((sg[a], sg[b]), (sg[b], sg[a])):
                for i in verts:
                    if all(v in vset for v in (i, i + gh, i + gv, i + gh + gv)):
                        squares.append((i, i + gh, i + gv, i + gh + gv, gh, gv))

    C0, C1, C2 = len(verts), len(edges), len(squares)
    vidx = {v: k for k, v in enumerate(verts)}
    d1 = np.zeros((C0, C1), dtype=int)
    for col, (s, t, _g) in enumerate(edges):
        d1[vidx[s], col] = -1
        d1[vidx[t], col] = 1
    d2 = np.zeros((C1, C2), dtype=int)
    for col, (i, j, k, ell, gh, gv) in enumerate(squares):
        for e, sign in (((i, j, gh), 1), ((k, ell, gh), -1),
                        ((j, ell, gv), 1), ((i, k, gv), -1)):
            if e in eidx:
                d2[eidx[e], col] += sign
    return C0, C1, C2, d1, d2


def h1_rank(C1, C2, d1, d2) -> int:
    if C1 == 0:
        return 0
    r1 = int(np.linalg.matrix_rank(d1))
    r2 = int(np.linalg.matrix_rank(d2)) if C2 > 0 else 0
    return max(0, (C1 - r1) - r2)


def hyper3_count(A: list[int]) -> int:
    """3-gap commuting closures: anchor i with i, i+d1, i+d1+d2, i+d1+d2+d3
    all present, over the HYPER_GAP_CAP most-frequent gaps (all 6 orderings).
    Generalises the pairwise square to a triple -- the hyperedge signal."""
    Aset = set(A)
    verts = sorted(Aset)
    # gap frequency over consecutive sorted uniques
    su = sorted(set(A))
    freq: dict[int, int] = {}
    for i in range(len(su) - 1):
        g = su[i + 1] - su[i]
        freq[g] = freq.get(g, 0) + 1
    top = [g for g, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:HYPER_GAP_CAP]]
    count = 0
    for d1, d2, d3 in itertools.permutations(top, 3) if len(top) >= 3 else []:
        for i in verts:
            if (i + d1 in Aset and i + d1 + d2 in Aset
                    and i + d1 + d2 + d3 in Aset):
                count += 1
    return count


# ---- difference tower ------------------------------------------------------

def diff_tower(terms: list[int], max_order: int) -> dict[int, list[int]]:
    tower = {0: list(terms)}
    cur = terms
    for k in range(1, max_order + 1):
        if len(cur) < 2:
            break
        cur = [cur[i + 1] - cur[i] for i in range(len(cur) - 1)]
        tower[k] = cur
    return tower


def main() -> int:
    with psycopg.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT seq_id FROM calx.sequences ORDER BY seq_id")
            seq_ids = [r[0] for r in cur.fetchall()]
            if not seq_ids:
                print("no sequences in calx.sequences; run seed_oeis_classics.py")
                return 1

            for sid in seq_ids:
                cur.execute(
                    "SELECT term FROM kan.sequence_terms WHERE seq_id=%s ORDER BY idx",
                    (sid,),
                )
                terms = [int(r[0]) for r in cur.fetchall()]
                tower = diff_tower(terms, MAX_DIFF_ORDER)
                sig = []
                for order, A in tower.items():
                    if len(set(A)) < 2:
                        C0, C1, C2, h1, hy, ok = len(set(A)), 0, 0, 0, 0, True
                    else:
                        C0, C1, C2, d1, d2 = build_complex(A)
                        h1 = h1_rank(C1, C2, d1, d2)
                        ok = (C2 == 0) or bool(np.allclose(d1 @ d2, 0))
                        hy = hyper3_count(A)
                    cur.execute(
                        "INSERT INTO kan.sequence_homology "
                        "(seq_id,diff_order,n_vertices,n_edges,n_squares,"
                        " n_hyper3,h1,d1d2_zero) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (seq_id,diff_order) DO UPDATE SET "
                        " n_vertices=EXCLUDED.n_vertices,n_edges=EXCLUDED.n_edges,"
                        " n_squares=EXCLUDED.n_squares,n_hyper3=EXCLUDED.n_hyper3,"
                        " h1=EXCLUDED.h1,d1d2_zero=EXCLUDED.d1d2_zero,"
                        " computed_at=now()",
                        (sid, order, C0, C1, C2, hy, h1, ok),
                    )
                    sig.append(h1)
                print(f"  [OK] {sid}: H1 signature {sig}")
        conn.commit()

        with conn.cursor() as cur:
            print("\n--- kan.sequence_signature ---")
            cur.execute(
                "SELECT seq_id, name, family, h1_signature, hyper3_signature "
                "FROM kan.sequence_signature ORDER BY family, seq_id"
            )
            for sid, name, fam, h1s, hys in cur.fetchall():
                print(f"  {sid} {name:<20s} fam={fam:<14s} "
                      f"H1={h1s} hyper3={hys}")
            print("\n--- kan.homology_similarity (similar at depth) ---")
            cur.execute(
                "SELECT seq_a, seq_b, h1_signature, same_family "
                "FROM kan.homology_similarity ORDER BY seq_a, seq_b"
            )
            rows = cur.fetchall()
            if rows:
                for a, b, s, sf in rows:
                    tag = "same-family" if sf else "CROSS-FAMILY"
                    print(f"  {a} ~ {b}  H1={s}  [{tag}]")
            else:
                print("  (no two sequences share a full H1 signature vector)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
