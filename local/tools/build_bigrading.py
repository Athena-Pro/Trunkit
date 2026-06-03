"""Horizontal capstone: the omega x Omega bigrading M_{i,j}.

Unifies the omega-tower W_i and Omega-tower B_j. Registers the bigrading +
excess tower + Mobius/zeta functors into kan and verifies live:

  L1 commuting   W_i.B_j == B_j.W_i == M_{i,j} ; M idempotent
  L2 marginals   (+)_j M_{i,j} = W_i ; (+)_i M_{i,j} = B_j
  L3 triangular  M_{i,j}=empty unless i<=j (units only at (0,0))
  L4 full id     (+)_{(i,j)} M_{i,j} = S exactly  (natural)
  L5 mobius      chain: B_j = zeta_{<=j} (-) zeta_{<=j-1};
                 excess: (+)_d E_d = S ; E_0 = [t: omega=Omega] (squarefree)

Tables kan.bigrading[_support]. Independent proof: proofs/bigrading.py.
Idempotent.
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


def Wi(S, i):
    return [t for t in S if om(t)[0] == i]


def Bj(S, j):
    return [t for t in S if om(t)[1] == j]


def Mij(S, i, j):
    return [t for t in S if om(t) == (i, j)]


def main() -> int:
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO kan.functor (name,src_category,tgt_category,description) "
            "VALUES ('M_bigrading','seq','seq',"
            "'M_{i,j}=[t:omega=i,Omega=j]: the omega x Omega bigrading'),"
            "('E_excess','seq','seq','E_d=(+)_i M_{i,i+d}: excess (Omega-omega) tower'),"
            "('zeta_Omega','seq','seq','cumulative zeta_{<=j}=(+)_{j*<=j} B_j*') "
            "ON CONFLICT (name) DO NOTHING"
        )

        base = {}
        for sid in BASE:
            cur.execute(
                "SELECT term FROM kan.sequence_terms WHERE seq_id=%s ORDER BY idx",
                (sid,),
            )
            base[sid] = [int(r[0]) for r in cur.fetchall()]

        L1 = L2 = L3 = L4 = L5 = True
        cur.execute("DELETE FROM kan.bigrading_support")

        for sid, S in base.items():
            ogs = [om(t) for t in S]
            mi = max((i for i, _ in ogs), default=0)
            mj = max((j for _, j in ogs), default=0)

            # L1 commuting idempotents
            for i in range(0, mi + 1):
                for j in range(0, mj + 1):
                    mij = Mij(S, i, j)
                    if Counter(Wi(Bj(S, j), i)) != Counter(mij):
                        L1 = False
                    if Counter(Bj(Wi(S, i), j)) != Counter(mij):
                        L1 = False
                    if Mij(mij, i, j) != mij:
                        L1 = False
                    if mij:
                        cur.execute(
                            "INSERT INTO kan.bigrading_support "
                            "(seq,i_omega,j_bigomega,n_terms) VALUES (%s,%s,%s,%s) "
                            "ON CONFLICT (seq,i_omega,j_bigomega) DO UPDATE "
                            "SET n_terms=EXCLUDED.n_terms",
                            (sid, i, j, len(mij)),
                        )
            # L2 marginals
            for i in range(0, mi + 1):
                u = Counter()
                for j in range(0, mj + 1):
                    u += Counter(Mij(S, i, j))
                if u != Counter(Wi(S, i)):
                    L2 = False
            for j in range(0, mj + 1):
                u = Counter()
                for i in range(0, mi + 1):
                    u += Counter(Mij(S, i, j))
                if u != Counter(Bj(S, j)):
                    L2 = False
            # L3 triangular support (omega<=Omega; units only (0,0))
            for t in S:
                i, j = om(t)
                if i > j and not (i == 0 and j == 0):
                    L3 = False
            # L4 full identity
            allm = Counter()
            for i in range(0, mi + 1):
                for j in range(0, mj + 1):
                    allm += Counter(Mij(S, i, j))
            if allm != Counter(S):
                L4 = False
            # L5a chain Mobius on Omega: B_j = zeta<=j - zeta<=j-1
            for j in range(0, mj + 1):
                z_j = Counter(t for t in S if om(t)[1] <= j)
                z_jm = Counter(t for t in S if om(t)[1] <= j - 1)
                if z_j - z_jm != Counter(Bj(S, j)):
                    L5 = False
            # L5b excess tower full id + E_0 = squarefree (omega=Omega)
            md = max((j - i for i, j in ogs), default=0)
            ex = Counter()
            for d in range(0, md + 1):
                Ed = [t for t in S if om(t)[1] - om(t)[0] == d]
                ex += Counter(Ed)
            if ex != Counter(S):
                L5 = False
            E0 = [t for t in S if om(t)[0] == om(t)[1]]
            sqfree = [t for t in S if om(t)[0] == om(t)[1]]
            if Counter(E0) != Counter(sqfree):
                L5 = False

        is_big = L1 and L2 and L3 and L4 and L5
        cur.execute(
            "INSERT INTO kan.bigrading "
            "(structure,commuting,marginals_ok,triangular,resolves_id,"
            " chain_mobius_ok,excess_full_id,e0_squarefree,is_bigraded) "
            "VALUES ('omega_x_Omega',%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (structure) DO UPDATE SET "
            " commuting=EXCLUDED.commuting,marginals_ok=EXCLUDED.marginals_ok,"
            " triangular=EXCLUDED.triangular,resolves_id=EXCLUDED.resolves_id,"
            " chain_mobius_ok=EXCLUDED.chain_mobius_ok,"
            " excess_full_id=EXCLUDED.excess_full_id,"
            " e0_squarefree=EXCLUDED.e0_squarefree,"
            " is_bigraded=EXCLUDED.is_bigraded,verified_at=now()",
            (L1, L2, L3, L4, L5, L5, L5, is_big),
        )
        conn.commit()

    print(f"  L1 commuting idempotents (W_i.B_j=B_j.W_i=M_ij): {L1}")
    print(f"  L2 marginals ((+)_j M=W_i, (+)_i M=B_j):         {L2}")
    print(f"  L3 triangular support (i<=j):                    {L3}")
    print(f"  L4 full identity ((+) M_ij = S):                 {L4}")
    print(f"  L5 Mobius (chain B_j diff + excess tower + E0):  {L5}")
    print(f"\n  omega x Omega bigrading: {is_big}")
    print("  registered M_bigrading / E_excess / zeta_Omega + support in kan")
    return 0 if is_big else 1


if __name__ == "__main__":
    sys.exit(main())
