"""Higher homology of the shared-prime structure.

For every sequence, build the simplicial FLAG (clique) complex of the
shared-prime graph and compute Betti numbers via simplicial boundary ranks:

  b0      = #connected components
  b1_flag = (E - rank d1) - rank d2     (cycle rank, triangles filled)
  b2      = (T - rank d2) - rank d3     (the new higher-homology content)

cycle_rank = E - V + C is also recorded; it must equal the prior factorial
shared_prime H1 (consistency anchor). Dense graphs explode the 3-/4-clique
enumeration -> those are marked over_budget and only b0/cycle_rank stored.

Reads/writes the live unified DB. numpy only. Idempotent (upsert per seq).
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
TRIAL_LIMIT = 100_000
CAP_TRI = 6_000      # max triangles before declaring over-budget
CAP_TETRA = 15_000   # max tetrahedra before declaring over-budget


def _is_prime(n):
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2; r += 1
    for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def prime_set(t, bedrock):
    if t in bedrock:
        return bedrock[t], True
    if t <= 1:
        return set(), True
    ps, m, d = set(), t, 2
    while d * d <= m and d <= TRIAL_LIMIT:
        while m % d == 0:
            ps.add(d); m //= d
        d += 1 if d == 2 else 2
    if m == 1:
        return ps, True
    if _is_prime(m):
        ps.add(m)
        return ps, True
    return ps, False


def _rank(M):
    return int(np.linalg.matrix_rank(M)) if M.size else 0


def betti(verts, adj):
    """Simplicial Betti b0,b1,b2 of the flag complex; graceful over-budget.

    Returns (n_tri, n_tetra, b0, cycle_rank, b1_flag, b2, over_budget).
    """
    V = len(verts)
    vidx = {v: i for i, v in enumerate(verts)}
    edges = []
    for a in range(V):
        for b in range(a + 1, V):
            if verts[b] in adj[verts[a]]:
                edges.append((a, b))
    E = len(edges)

    # b0 + cycle_rank via union-find (always cheap)
    par = list(range(V))

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]; x = par[x]
        return x

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            par[ra] = rb
    comps = len({find(i) for i in range(V)}) if V else 0
    cycle_rank = max(0, E - V + comps)

    # enumerate triangles (3-cliques)
    nbr = {i: set() for i in range(V)}
    for a, b in edges:
        nbr[a].add(b); nbr[b].add(a)
    tris = []
    for a, b in edges:
        for c in nbr[a] & nbr[b]:
            if c > b:
                tris.append((a, b, c))
            if len(tris) > CAP_TRI:
                return (-1, -1, comps, cycle_rank, -1, -1, True)
    T = len(tris)

    tetra = []
    for (a, b, c) in tris:
        for d in nbr[a] & nbr[b] & nbr[c]:
            if d > c:
                tetra.append((a, b, c, d))
            if len(tetra) > CAP_TETRA:
                return (T, -1, comps, cycle_rank, -1, -1, True)
    Q = len(tetra)

    eidx = {e: k for k, e in enumerate(edges)}
    tidx = {t: k for k, t in enumerate(tris)}

    d1 = np.zeros((V, E), dtype=np.int8)
    for col, (a, b) in enumerate(edges):
        d1[a, col] = -1
        d1[b, col] = 1
    d2 = np.zeros((E, T), dtype=np.int8)
    for col, (a, b, c) in enumerate(tris):
        d2[eidx[(b, c)], col] += 1
        d2[eidx[(a, c)], col] += -1
        d2[eidx[(a, b)], col] += 1
    d3 = np.zeros((T, Q), dtype=np.int8)
    for col, (a, b, c, d) in enumerate(tetra):
        for sgn, tri in ((1, (b, c, d)), (-1, (a, c, d)),
                         (1, (a, b, d)), (-1, (a, b, c))):
            d3[tidx[tri], col] += sgn

    r1, r2, r3 = _rank(d1), _rank(d2), _rank(d3)
    b0 = V - r1
    b1 = (E - r1) - r2
    b2 = (T - r2) - r3
    return (T, Q, b0, cycle_rank, max(0, b1), max(0, b2), False)


def main() -> int:
    with psycopg.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT n, prime FROM calx.factorizations")
            bedrock: dict[int, set[int]] = {}
            for n, p in cur.fetchall():
                bedrock.setdefault(n, set()).add(p)

            cur.execute("SELECT seq_id FROM calx.sequences ORDER BY seq_id")
            seq_ids = [r[0] for r in cur.fetchall()]

            for sid in seq_ids:
                cur.execute(
                    "SELECT term FROM kan.sequence_terms WHERE seq_id=%s ORDER BY idx",
                    (sid,),
                )
                terms = [int(r[0]) for r in cur.fetchall()]
                pof, verts = {}, []
                for t in terms:
                    ps, ok = prime_set(t, bedrock)
                    if ok and ps:
                        pof[t] = ps
                        verts.append(t)
                verts = sorted(set(verts))
                adj = {v: set() for v in verts}
                for i in range(len(verts)):
                    for j in range(i + 1, len(verts)):
                        if pof[verts[i]] & pof[verts[j]]:
                            adj[verts[i]].add(verts[j])
                            adj[verts[j]].add(verts[i])
                ntri, ntet, b0, cyc, b1f, b2, ob = betti(verts, adj)
                E = sum(len(a) for a in adj.values()) // 2
                cur.execute(
                    "INSERT INTO kan.shared_prime_betti "
                    "(seq_id,n_vertices,n_edges,n_triangles,n_tetra,b0,"
                    " cycle_rank,b1_flag,b2,over_budget) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (seq_id) DO UPDATE SET "
                    " n_vertices=EXCLUDED.n_vertices,n_edges=EXCLUDED.n_edges,"
                    " n_triangles=EXCLUDED.n_triangles,n_tetra=EXCLUDED.n_tetra,"
                    " b0=EXCLUDED.b0,cycle_rank=EXCLUDED.cycle_rank,"
                    " b1_flag=EXCLUDED.b1_flag,b2=EXCLUDED.b2,"
                    " over_budget=EXCLUDED.over_budget,computed_at=now()",
                    (sid, len(verts), E, ntri, ntet, b0, cyc, b1f, b2, ob),
                )
                tag = "OVER-BUDGET" if ob else f"b2={b2}"
                print(f"  [OK] {sid}: V={len(verts)} E={E} cyc={cyc} "
                      f"b0={b0} b1f={b1f} {tag}")
        conn.commit()

        with conn.cursor() as cur:
            print("\n--- anchor check (cycle_rank == factorial shared_prime H1) ---")
            cur.execute(
                "SELECT count(*) FILTER (WHERE anchor_ok), count(*) "
                "FROM kan.higher_homology_summary"
            )
            ok, tot = cur.fetchone()
            print(f"  {ok}/{tot} sequences: cycle_rank matches factorial anchor")
            print("\n--- kan.b2_nontrivial (genuine 2-cycles) ---")
            cur.execute("SELECT seq_id,name,family,b2,n_triangles,n_tetra "
                        "FROM kan.b2_nontrivial")
            rows = cur.fetchall()
            if rows:
                for sid, nm, fam, b2, nt, nq in rows:
                    print(f"  {sid} {nm:<22s} fam={fam:<12s} "
                          f"b2={b2} (T={nt} Q={nq})")
            else:
                print("  (no sequence has b2>0 within budget)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
