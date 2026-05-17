"""Factorial homology of sequences.

For every sequence in the unified OEIS layer, build four factorization-derived
structures and take their gap-pattern H1 (verified Erdos construction):

  parity       : stream of (t mod 2)
  omega        : stream of omega(t)   = # distinct prime factors
  bigomega     : stream of Omega(t)   = # prime factors with multiplicity
  shared_prime : graph t_i ~ t_j iff they share a prime factor; H1 = E-V+C

Factoring: calx.factorizations for 2<=t<=100 (authoritative); else trial
division up to TRIAL_LIMIT with a Miller-Rabin check on the residual. Terms
whose cofactor stays composite beyond budget are 'unfactored' and excluded
from the omega/bigomega streams and the shared-prime graph (parity is exact).

Reads/writes the live unified DB. numpy only. Idempotent (upsert per axis).
"""

from __future__ import annotations

import os
import sys

import numpy as np
import psycopg

PG_DSN = os.environ.get(
    "CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk"
)
TRIAL_LIMIT = 100_000


# ---- factorization ---------------------------------------------------------

def _is_prime(n: int) -> bool:
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
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


def factor(t: int, bedrock: dict[int, set[int]]):
    """Return (primes:set, omega:int, bigomega:int, ok:bool)."""
    if t in bedrock:                       # calx authoritative (2..100)
        ps = bedrock[t]
        # bigomega from calx exponents handled by caller-supplied bedrock_big
        return ps, len(ps), None, True
    if t <= 1:
        return set(), 0, 0, True
    primes, big, m = set(), 0, t
    d = 2
    while d * d <= m and d <= TRIAL_LIMIT:
        while m % d == 0:
            primes.add(d)
            big += 1
            m //= d
        d += 1 if d == 2 else 2
    if m == 1:
        return primes, len(primes), big, True
    if _is_prime(m):
        primes.add(m)
        big += 1
        return primes, len(primes), big, True
    return primes, len(primes), big, False   # composite cofactor > budget


# ---- vendored Erdos gap-pattern complex (faithful) -------------------------

def _h1_stream(A: list[int]) -> tuple[int, int, int, int]:
    vals = sorted(set(A))
    if len(vals) < 2:
        return len(vals), 0, 0, 0
    vset = set(vals)
    gaps = {vals[i + 1] - vals[i] for i in range(len(vals) - 1)}
    edges = [(i, i + g) for i in vals for g in gaps if i + g in vset]
    eidx = {e: k for k, e in enumerate(edges)}
    sq = []
    sg = sorted(gaps)
    for a in range(len(sg)):
        for b in range(a + 1, len(sg)):
            for gh, gv in ((sg[a], sg[b]), (sg[b], sg[a])):
                for i in vals:
                    if all(v in vset for v in (i, i + gh, i + gv, i + gh + gv)):
                        sq.append((i, gh, gv))
    C0, C1, C2 = len(vals), len(edges), len(sq)
    vidx = {v: k for k, v in enumerate(vals)}
    d1 = np.zeros((C0, C1), dtype=int)
    for col, (s, tt) in enumerate(edges):
        d1[vidx[s], col] = -1
        d1[vidx[tt], col] = 1
    d2 = np.zeros((C1, C2), dtype=int)
    for col, (i, gh, gv) in enumerate(sq):
        for e, sgn in (((i, i + gh), 1), ((i + gv, i + gv + gh), -1),
                       ((i + gh, i + gh + gv), 1), ((i, i + gv), -1)):
            if e in eidx:
                d2[eidx[e], col] += sgn
    r1 = int(np.linalg.matrix_rank(d1)) if C1 else 0
    r2 = int(np.linalg.matrix_rank(d2)) if C2 else 0
    return C0, C1, C2, max(0, (C1 - r1) - r2)


def _h1_graph(verts: list[int], edges: list[tuple[int, int]]) -> tuple[int, int, int]:
    """Betti-1 of an undirected graph: E - V + C (connected components)."""
    V = len(verts)
    idx = {v: i for i, v in enumerate(verts)}
    parent = list(range(V))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    uniq = set()
    for a, b in edges:
        ia, ib = idx[a], idx[b]
        uniq.add((min(ia, ib), max(ia, ib)))
        ra, rb = find(ia), find(ib)
        if ra != rb:
            parent[ra] = rb
    E = len(uniq)
    C = len({find(i) for i in range(V)}) if V else 0
    return V, E, max(0, E - V + C)


def main() -> int:
    with psycopg.connect(PG_DSN) as conn:
        with conn.cursor() as cur:
            # calx bedrock: primes per n, and bigomega per n
            cur.execute("SELECT n, prime, exponent FROM calx.factorizations")
            bed_primes: dict[int, set[int]] = {}
            bed_big: dict[int, int] = {}
            for n, p, e in cur.fetchall():
                bed_primes.setdefault(n, set()).add(p)
                bed_big[n] = bed_big.get(n, 0) + e

            cur.execute("SELECT seq_id FROM calx.sequences ORDER BY seq_id")
            seq_ids = [r[0] for r in cur.fetchall()]

            for sid in seq_ids:
                cur.execute(
                    "SELECT term FROM kan.sequence_terms WHERE seq_id=%s ORDER BY idx",
                    (sid,),
                )
                terms = [int(r[0]) for r in cur.fetchall()]

                parity = [t & 1 for t in terms]
                omega, bigomega = [], []
                sp_verts, prime_of = [], {}
                unfac = 0
                for t in terms:
                    ps, om, bg, ok = factor(t, bed_primes)
                    if t in bed_primes:
                        bg = bed_big[t]
                    if not ok:
                        unfac += 1
                        continue
                    omega.append(om)
                    bigomega.append(bg)
                    sp_verts.append(t)
                    prime_of[t] = ps

                # Shared-prime graph over DISTINCT term values (the
                # project-wide convention: the complex is built on the value
                # set, never the multiset -- duplicate terms must not inflate
                # edges). Vertices with empty prime support (e.g. t=1) are
                # kept as isolated vertices.
                uniq = sorted(set(sp_verts))
                sp_edges = []
                for a in range(len(uniq)):
                    for b in range(a + 1, len(uniq)):
                        if prime_of[uniq[a]] & prime_of[uniq[b]]:
                            sp_edges.append((uniq[a], uniq[b]))

                axes = {}
                for name, stream in (("parity", parity), ("omega", omega),
                                     ("bigomega", bigomega)):
                    C0, C1, C2, h1 = _h1_stream(stream)
                    axes[name] = (C0, C1, C2, h1, len(stream))
                Vg, Eg, h1g = _h1_graph(uniq, sp_edges)
                axes["shared_prime"] = (Vg, Eg, 0, h1g, len(uniq))

                for axis, (C0, C1, C2, h1, nterms) in axes.items():
                    cur.execute(
                        "INSERT INTO kan.sequence_factorial_homology "
                        "(seq_id,axis,n_vertices,n_edges,n_squares,h1,"
                        " n_terms,n_unfactored) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (seq_id,axis) DO UPDATE SET "
                        " n_vertices=EXCLUDED.n_vertices,n_edges=EXCLUDED.n_edges,"
                        " n_squares=EXCLUDED.n_squares,h1=EXCLUDED.h1,"
                        " n_terms=EXCLUDED.n_terms,n_unfactored=EXCLUDED.n_unfactored,"
                        " computed_at=now()",
                        (sid, axis, C0, C1, C2, h1, nterms, unfac),
                    )
                print(f"  [OK] {sid}: parity={axes['parity'][3]} "
                      f"omega={axes['omega'][3]} bigomega={axes['bigomega'][3]} "
                      f"shared_prime={axes['shared_prime'][3]} (unfactored={unfac})")
        conn.commit()

        with conn.cursor() as cur:
            print("\n--- kan.factorial_signature ---")
            cur.execute(
                "SELECT seq_id,name,family,h1_parity,h1_omega,h1_bigomega,"
                "h1_shared_prime,unfactored FROM kan.factorial_signature "
                "ORDER BY family,seq_id"
            )
            for sid, nm, fam, hp, ho, hb, hs, uf in cur.fetchall():
                print(f"  {sid} {nm:<20s} fam={fam:<14s} "
                      f"[par={hp} om={ho} big={hb} sp={hs}] unfac={uf}")
            print("\n--- kan.factorial_similarity ---")
            cur.execute(
                "SELECT seq_a,seq_b,h1_parity,h1_omega,h1_bigomega,"
                "h1_shared_prime,same_family FROM kan.factorial_similarity "
                "ORDER BY seq_a,seq_b"
            )
            rows = cur.fetchall()
            if rows:
                for a, b, hp, ho, hb, hs, sf in rows:
                    tag = "same-family" if sf else "CROSS-FAMILY"
                    print(f"  {a} ~ {b}  [par={hp} om={ho} big={hb} sp={hs}]  [{tag}]")
            else:
                print("  (no two sequences share a full factorial signature)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
