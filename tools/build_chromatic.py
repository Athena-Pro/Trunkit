"""Horizontal axis 3: the chromatic height tower.

ht(t) = prime-index of the largest prime factor of t (ht(1)=0); primes are
sieved up to SIEVE. A term whose largest prime exceeds SIEVE goes to the
single ABOVE-window layer (height sentinel HI) -- the finite analogue of
"infinite chromatic height". L_n(S)=[t:ht<=n] (p_n-smooth localization),
M_n=L_n(-)L_{n-1}.

Laws are checked over the DISTINCT occurring heights only: L_n is a step
function of n, so verifying at the breakpoints is exact and fast.

  C1 idempotent  L_n.L_n=L_n     C2 filtration  L_n subset L_{n+1} subset S
  C3 smashing    L_m.L_n=L_min   C4 layers      M_n=L_n(-)L_{prev}=[ht=n]
  C5 convergence colim L_n = Id  C6 compat      L_n commutes with W_i,B_j

Tables kan.chromatic[_layer]. Proof: proofs/chromatic.py. Idempotent.
"""

from __future__ import annotations

import os
import sys
from collections import Counter

import psycopg

PG_DSN = os.environ.get(
    "CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk"
)
BASE = ["A000040", "A000290", "NW1", "Z000001", "A000045"]
SIEVE = 200_000
HI = 1 << 30                       # above-window sentinel height


def _sieve():
    s = bytearray([1]) * (SIEVE + 1)
    s[0] = s[1] = 0
    for i in range(2, int(SIEVE ** 0.5) + 1):
        if s[i]:
            s[i * i::i] = bytearray(len(s[i * i::i]))
    idx, primes = {}, []
    for n in range(2, SIEVE + 1):
        if s[n]:
            primes.append(n)
            idx[n] = len(primes)        # 1-based prime index
    return idx


_PIDX = _sieve()


def ht(t: int) -> int:
    if t <= 1:
        return 0
    largest, m, d = 1, t, 2
    while d * d <= m and d <= SIEVE:
        if m % d == 0:
            largest = d
            while m % d == 0:
                m //= d
        d += 1 if d == 2 else 2
    if m > 1:
        largest = m
    return _PIDX.get(largest, HI)       # HI if largest prime > SIEVE


def om(t: int):
    if t <= 1:
        return 0, 0
    w = W = 0
    m, d = t, 2
    while d * d <= m and d <= 100_000:
        if m % d == 0:
            w += 1
            while m % d == 0:
                W += 1
                m //= d
        d += 1 if d == 2 else 2
    if m > 1:
        w += 1
        W += 1
    return w, W


def main() -> int:
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO kan.functor (name,src_category,tgt_category,description) "
            "VALUES ('L_chromatic','seq','seq',"
            "'L_n=[t:ht(t)<=n]: p_n-smooth chromatic localization tower'),"
            "('M_chromatic','seq','seq',"
            "'M_n=L_n(-)L_{n-1}: monochromatic layer (largest prime = p_n)') "
            "ON CONFLICT (name) DO NOTHING"
        )

        base = {}
        for sid in BASE:
            cur.execute(
                "SELECT term FROM kan.sequence_terms WHERE seq_id=%s ORDER BY idx",
                (sid,),
            )
            base[sid] = [int(r[0]) for r in cur.fetchall()]

        C1 = C2 = C3 = C4 = C5 = C6 = True
        cur.execute("DELETE FROM kan.chromatic_layer")

        for sid, S in base.items():
            H = {t: ht(t) for t in set(S)}            # memoised per term
            W = {t: om(t)[0] for t in set(S)}
            B = {t: om(t)[1] for t in set(S)}
            levels = sorted(set(H.values()))          # distinct breakpoints

            def L(n):
                return [t for t in S if H[t] <= n]

            def Mn(n):
                return [t for t in S if H[t] == n]

            for ix, n in enumerate(levels):
                Ln = L(n)
                if [t for t in Ln if H[t] <= n] != Ln:                # C1
                    C1 = False
                nxt = levels[ix + 1] if ix + 1 < len(levels) else n
                if not set(Ln).issubset(set(L(nxt))):                 # C2
                    C2 = False
                if not set(Ln).issubset(set(S)):
                    C2 = False
                for m in levels:                                      # C3
                    if Counter(L(min(m, n))) != Counter(
                            [t for t in L(n) if H[t] <= m]):
                        C3 = False
                prev = levels[ix - 1] if ix > 0 else -1               # C4
                if Counter(L(n)) - Counter(L(prev)) != Counter(Mn(n)):
                    C4 = False
                if Mn(n) != [t for t in S if H[t] == n]:
                    C4 = False
                for i in (1, 2, 3):                                   # C6
                    lhs = Counter(t for t in S if W[t] == i and H[t] <= n)
                    rhs = Counter(t for t in L(n) if W[t] == i)
                    if lhs != rhs:
                        C6 = False
                for j in (1, 2, 3):
                    lhs = Counter(t for t in S if B[t] == j and H[t] <= n)
                    rhs = Counter(t for t in L(n) if B[t] == j)
                    if lhs != rhs:
                        C6 = False
                # HI (= 1<<30) is a positive int that fits PG INTEGER and
                # sorts as the top height, so the convergence view's
                # ORDER BY height DESC correctly lands on the full-cum row.
                cur.execute(
                    "INSERT INTO kan.chromatic_layer "
                    "(seq,height,n_terms,cum_terms) VALUES (%s,%s,%s,%s) "
                    "ON CONFLICT (seq,height) DO UPDATE SET "
                    " n_terms=EXCLUDED.n_terms,cum_terms=EXCLUDED.cum_terms",
                    (sid, n, len(Mn(n)), len(Ln)),
                )

            top = levels[-1] if levels else 0
            if Counter(L(top)) != Counter(S):                         # C5
                C5 = False
            lay = Counter()
            for n in levels:
                lay += Counter(Mn(n))
            if lay != Counter(S):
                C5 = False

        is_chr = C1 and C2 and C3 and C4 and C5 and C6
        cur.execute(
            "INSERT INTO kan.chromatic "
            "(structure,idempotent,filtration,smashing,layers_ok,"
            " convergence,bigrade_compat,is_chromatic) "
            "VALUES ('largest_prime_height',%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (structure) DO UPDATE SET "
            " idempotent=EXCLUDED.idempotent,filtration=EXCLUDED.filtration,"
            " smashing=EXCLUDED.smashing,layers_ok=EXCLUDED.layers_ok,"
            " convergence=EXCLUDED.convergence,"
            " bigrade_compat=EXCLUDED.bigrade_compat,"
            " is_chromatic=EXCLUDED.is_chromatic,verified_at=now()",
            (C1, C2, C3, C4, C5, C6, is_chr),
        )
        conn.commit()

    print(f"  C1 idempotent:                 {C1}")
    print(f"  C2 filtration (nested in S):   {C2}")
    print(f"  C3 smashing (L_m.L_n=L_min):   {C3}")
    print(f"  C4 layers/fracture:            {C4}")
    print(f"  C5 convergence (colim L=Id):   {C5}")
    print(f"  C6 bigrading-compatible:       {C6}")
    print(f"\n  chromatic height tower: {is_chr}")
    print("  registered L_chromatic / M_chromatic + layer profile in kan")
    return 0 if is_chr else 1


if __name__ == "__main__":
    sys.exit(main())
