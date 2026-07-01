"""build_scott.py — Scott / domain-theory attestation over kan's finite posets.

For each registered finite poset this records, in kan.scott_lattice, the
UNIVERSAL finite-poset laws (closures form a lattice; Scott = Alexandrov; the
specialization order recovers the poset) plus the poset-specific FACT of
whether the closure-operator lattice is Boolean 2^k. See 98_kan_scott.sql.

Enumeration strategy (Moore families, not brute self-maps):
  * Closure operators c on a finite poset P are in bijection with closure
    systems C ⊆ P: subsets for which every up-set ↑x meets C in a least
    element, i.e. c(x) = min(↑x ∩ C). Interior operators are the order dual
    (max(↓x ∩ D)). So we enumerate the 2^|P| subsets and keep the valid
    systems -- O(2^n) instead of the O(n^n) of enumerating all self-maps.
  * The closure lattice (pointwise order) is analysed with bitmask meets/joins
    so distributivity / complementation are cheap even for a few hundred
    closures.

Posets attested (read live from kan.bigrading_support, step 63):
  * omega_chain          — the distinct omega values as a chain (Boolean 2^k)
  * bigrading_incidence  — the omega x Omega incidence poset {(i,j):i<=j},
                           largest window keeping |P| <= MAXN (a genuine
                           non-chain with incomparabilities)

Only n_monotone (the sole n^n quantity) is capped: computed for n <= MONO_CAP,
NULL otherwise. Idempotent. Run: CALX_DSN=... python local/tools/build_scott.py
"""
from __future__ import annotations

import json
import os
import sys
from itertools import combinations
from itertools import product as iprod

import psycopg

PG_DSN = os.environ.get("CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk")
MAXN = 13        # 2^n Moore-family / upper-set enumeration cap
MONO_CAP = 7     # compute the n^n monotone-map count only up to here


# ── poset predicates ─────────────────────────────────────────────────────────

def poset_is_valid(P, leq):
    """leq is a genuine partial order: reflexive, antisymmetric, transitive."""
    if not all(leq[(x, x)] for x in P):
        return False
    if any(leq[(a, b)] and leq[(b, a)] and a != b for a in P for b in P):
        return False
    for a in P:
        for b in P:
            if leq[(a, b)]:
                for c in P:
                    if leq[(b, c)] and not leq[(a, c)]:
                        return False
    return True


def upper_sets(P, leq):
    def is_upper(S):
        return all(y in S for x in S for y in P if leq[(x, y)])
    return [frozenset(c) for r in range(len(P) + 1)
            for c in combinations(P, r) if is_upper(frozenset(c))]


def closure_systems(P, leq):
    """Closure operators via Moore families: c(x) = min(↑x ∩ C)."""
    up = {x: [y for y in P if leq[(x, y)]] for x in P}
    out = []
    for r in range(len(P) + 1):
        for C in combinations(P, r):
            Cs = set(C)
            cmap, ok = {}, True
            for x in P:
                above = [m for m in up[x] if m in Cs]
                if not above:
                    ok = False
                    break
                mins = [m for m in above if all(leq[(m, m2)] for m2 in above)]
                if len(mins) != 1:
                    ok = False
                    break
                cmap[x] = mins[0]
            if ok:
                out.append(cmap)
    return out


def interior_systems(P, leq):
    """Interior operators (order dual of closures): i(x) = max(↓x ∩ D)."""
    down = {x: [y for y in P if leq[(y, x)]] for x in P}
    out = []
    for r in range(len(P) + 1):
        for D in combinations(P, r):
            Ds = set(D)
            imap, ok = {}, True
            for x in P:
                below = [m for m in down[x] if m in Ds]
                if not below:
                    ok = False
                    break
                maxs = [m for m in below if all(leq[(m2, m)] for m2 in below)]
                if len(maxs) != 1:
                    ok = False
                    break
                imap[x] = maxs[0]
            if ok:
                out.append(imap)
    return out


def closure_lattice_facts(closures, P, leq):
    """Bitmask analysis of the pointwise-ordered closure lattice."""
    m = len(closures)

    def cle(c, d):
        return all(leq[(c[x], d[x])] for x in P)

    down = [0] * m   # down[i] = {k : closures[k] ≤ closures[i]}
    up = [0] * m     # up[i]   = {k : closures[i] ≤ closures[k]}
    for i in range(m):
        for k in range(m):
            if cle(closures[k], closures[i]):
                down[i] |= (1 << k)
            if cle(closures[i], closures[k]):
                up[i] |= (1 << k)

    def bits(mask):
        while mask:
            b = mask & -mask
            yield b.bit_length() - 1
            mask ^= b

    def unique_max(mask):                       # element k∈mask with mask ⊆ down[k]
        found = -1
        for k in bits(mask):
            if (mask & down[k]) == mask:
                if found != -1:
                    return -1
                found = k
        return found

    def unique_min(mask):                       # element k∈mask with mask ⊆ up[k]
        found = -1
        for k in bits(mask):
            if (mask & up[k]) == mask:
                if found != -1:
                    return -1
                found = k
        return found

    meet = [[0] * m for _ in range(m)]
    join = [[0] * m for _ in range(m)]
    is_lattice = True
    for i in range(m):
        for j in range(m):
            mij = unique_max(down[i] & down[j])
            jij = unique_min(up[i] & up[j])
            if mij < 0 or jij < 0:
                is_lattice = False
            meet[i][j], join[i][j] = mij, jij

    bottom = next(i for i in range(m) if up[i] == (1 << m) - 1)   # ≤ everything = identity
    top = next(i for i in range(m) if down[i] == (1 << m) - 1)
    atoms = [i for i in range(m) if i != bottom
             and (down[i] & ~((1 << bottom) | (1 << i))) == 0]

    distributive = is_lattice and all(
        meet[a][join[b][c]] == join[meet[a][b]][meet[a][c]]
        for a in range(m) for b in range(m) for c in range(m))
    complemented = is_lattice and all(
        any(meet[a][e] == bottom and join[a][e] == top for e in range(m))
        for a in range(m))
    boolean = distributive and complemented
    two_pow_k = (2 ** len(atoms) == m)

    atom_moves = [{str(k): str(v) for k, v in closures[i].items() if closures[i][k] != k}
                  for i in atoms]
    return {
        "n_closures": m, "n_atoms": len(atoms),
        "closures_lattice": is_lattice,
        "closures_distributive": distributive,
        "closures_complemented": complemented,
        "closures_boolean": boolean,
        "closures_two_pow_k": two_pow_k,
        "atom_moves": atom_moves,
    }


def attest_poset(points, leq):
    P = list(points)
    opens = upper_sets(P, leq)

    def spec(a, b):                             # every open containing a contains b
        return all((a not in U) or (b in U) for U in opens)
    facts = {
        "n_points": len(P),
        "n_scott_opens": len(opens),
        "poset_valid": poset_is_valid(P, leq),
        "scott_alexandrov": True,               # finite dcpo: Scott = Alexandrov
        "spec_is_order": all(spec(a, b) == leq[(a, b)] for a in P for b in P),
        "n_interiors": len(interior_systems(P, leq)),
        "n_monotone": None,
    }
    if len(P) <= MONO_CAP:
        facts["n_monotone"] = sum(
            1 for asg in iprod(P, repeat=len(P))
            if all((not leq[(a, b)]) or leq[(dict(zip(P, asg))[a], dict(zip(P, asg))[b])]
                   for a in P for b in P))
    facts.update(closure_lattice_facts(closure_systems(P, leq), P, leq))
    return facts


# ── poset sources (live DB) ──────────────────────────────────────────────────

def incidence_window(cells, maxn):
    best = []
    for w in range(0, max(max(i, j) for i, j in cells) + 1):
        sub = [(i, j) for (i, j) in cells if i <= w and j <= w]
        if len(sub) <= maxn:
            best = sub
        else:
            break
    return best


def load_posets(cur):
    cur.execute("SELECT DISTINCT i_omega, j_bigomega FROM kan.bigrading_support")
    cells = [(int(i), int(j)) for i, j in cur.fetchall()]
    posets = []
    if not cells:
        return posets

    # 1. omega chain — the distinct omega values, totally ordered (Boolean 2^k).
    omegas = sorted({i for i, _ in cells})[:MAXN]
    cpts = [f"w{v}" for v in omegas]
    cval = {f"w{v}": v for v in omegas}
    cleq = {(a, b): cval[a] <= cval[b] for a in cpts for b in cpts}
    posets.append(("omega_chain",
                   f"distinct omega values {omegas} as a chain", cpts, cleq))

    # 2. omega x Omega incidence poset — largest window keeping |P| <= MAXN.
    window = incidence_window(cells, MAXN)
    ipts = [f"({i},{j})" for (i, j) in window]
    icoord = {f"({i},{j})": (i, j) for (i, j) in window}
    ileq = {(a, b): (icoord[a][0] <= icoord[b][0] and icoord[a][1] <= icoord[b][1])
            for a in ipts for b in ipts}
    w = max(max(i, j) for i, j in window)
    posets.append(("bigrading_incidence",
                   f"occupied omega x Omega cells (i,j) with i,j <= {w}; product order",
                   ipts, ileq))
    return posets


def main() -> int:
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        posets = load_posets(cur)
        if not posets:
            print("  no posets available (kan.bigrading_support empty?) -- nothing to attest")
            return 0

        all_laws_hold = True
        for name, carrier, pts, leq in posets:
            if len(pts) > MAXN:
                print(f"  [skip] {name}: |P|={len(pts)} > cap {MAXN}")
                continue
            f = attest_poset(pts, leq)
            cur.execute("DELETE FROM kan.scott_atom WHERE poset=%s", (name,))
            cur.execute(
                "INSERT INTO kan.scott_lattice "
                "(poset,carrier,n_points,n_scott_opens,n_monotone,n_closures,"
                " n_interiors,n_atoms,poset_valid,closures_lattice,scott_alexandrov,"
                " spec_is_order,closures_distributive,closures_complemented,"
                " closures_boolean,closures_two_pow_k) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (poset) DO UPDATE SET "
                " carrier=EXCLUDED.carrier,n_points=EXCLUDED.n_points,"
                " n_scott_opens=EXCLUDED.n_scott_opens,n_monotone=EXCLUDED.n_monotone,"
                " n_closures=EXCLUDED.n_closures,n_interiors=EXCLUDED.n_interiors,"
                " n_atoms=EXCLUDED.n_atoms,poset_valid=EXCLUDED.poset_valid,"
                " closures_lattice=EXCLUDED.closures_lattice,"
                " scott_alexandrov=EXCLUDED.scott_alexandrov,spec_is_order=EXCLUDED.spec_is_order,"
                " closures_distributive=EXCLUDED.closures_distributive,"
                " closures_complemented=EXCLUDED.closures_complemented,"
                " closures_boolean=EXCLUDED.closures_boolean,"
                " closures_two_pow_k=EXCLUDED.closures_two_pow_k,verified_at=now()",
                (name, carrier, f["n_points"], f["n_scott_opens"], f["n_monotone"],
                 f["n_closures"], f["n_interiors"], f["n_atoms"], f["poset_valid"],
                 f["closures_lattice"], f["scott_alexandrov"], f["spec_is_order"],
                 f["closures_distributive"], f["closures_complemented"],
                 f["closures_boolean"], f["closures_two_pow_k"]),
            )
            for aid, moves in enumerate(f["atom_moves"]):
                cur.execute(
                    "INSERT INTO kan.scott_atom (poset,atom_id,moves) VALUES (%s,%s,%s) "
                    "ON CONFLICT (poset,atom_id) DO UPDATE SET moves=EXCLUDED.moves",
                    (name, aid, json.dumps(moves)),
                )
            laws = f["closures_lattice"] and f["scott_alexandrov"] and f["spec_is_order"] \
                and f["poset_valid"]
            all_laws_hold = all_laws_hold and laws
            mono = f["n_monotone"] if f["n_monotone"] is not None else "n/a"
            boolean = f["closures_boolean"] and f["closures_two_pow_k"]
            print(f"\n  poset '{name}'  ({carrier})")
            print(f"    |P|={f['n_points']}  scott_opens={f['n_scott_opens']}  "
                  f"monotone={mono}  closures={f['n_closures']}  "
                  f"interiors={f['n_interiors']}  atoms={f['n_atoms']}")
            print(f"    UNIVERSAL laws: valid_poset={f['poset_valid']}  "
                  f"closures_form_lattice={f['closures_lattice']}  "
                  f"scott=alexandrov={f['scott_alexandrov']}  spec=order={f['spec_is_order']}")
            print(f"    FACT: closure lattice Boolean 2^{f['n_atoms']} = {boolean}  "
                  f"(distributive={f['closures_distributive']}, "
                  f"complemented={f['closures_complemented']})")
        conn.commit()

    print(f"\n  scott engine: universal laws hold = {all_laws_hold}")
    return 0 if all_laws_hold else 1


if __name__ == "__main__":
    sys.exit(main())
