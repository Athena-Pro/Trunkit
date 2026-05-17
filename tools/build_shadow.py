"""Static adelic shadow over the live corpus.

rho(N) = SUM_{s=0..16} C(16,s) * A(N-s),  A(m) = # subsets of the 240
prime-power atoms summing to m (each prime row super-increasing -> 0/1, so
ALL multiplicity is the F_1 binomial kernel convolved with A). We compute
rho + the F_1-marginal per stored corpus term within a bounded window, build
a coarse per-sequence shadow signature, and test whether the shadow
SEPARATES the residual combined-invariant collision kernel (the pairs in
kan.combined_similarity) -- the axis the multiplicative tower could not see.

Tables kan.shadow_term/_signature/_separation. Proof: proofs/shadow.py.
Idempotent.
"""

from __future__ import annotations

import hashlib
import math
import os
import sys

import psycopg

PG_DSN = os.environ.get(
    "CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk"
)
BASES = [1, 2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47]
COLS = 16
CAP = 200_000                       # shadow window


def prime_power_atoms(cap: int) -> list[int]:
    vals = []
    for r in range(1, len(BASES)):          # prime rows only (F_1 handled sep.)
        p = BASES[r]
        for c in range(COLS):
            v = p ** (c + 1)
            if v <= cap:
                vals.append(v)
    return vals


def build_A(cap: int) -> list[int]:
    """A[m] = # subsets of prime-power atoms summing to m (0/1 subset-sum)."""
    A = [0] * (cap + 1)
    A[0] = 1
    for v in prime_power_atoms(cap):
        for m in range(cap, v - 1, -1):
            if A[m - v]:
                A[m] += A[m - v]
    return A


def binom16():
    return [math.comb(16, s) for s in range(17)]


def shadow(n: int, A: list[int], C16: list[int]):
    """(rho, f1_mean, f1_max, f1_support) for integer n, or None if n>CAP."""
    if n < 0 or n > CAP:
        return None
    rho = 0
    weighted = 0
    fmax = -1
    supp = 0
    for s in range(0, 17):
        m = n - s
        if 0 <= m <= CAP and A[m]:
            w = C16[s] * A[m]
            rho += w
            weighted += s * w
            supp += 1
            fmax = s
    if rho == 0:
        return (0, None, None, 0)
    return (rho, weighted / rho, fmax, supp)


def main() -> int:
    A = build_A(CAP)
    C16 = binom16()
    print(f"  prime-power atoms <= CAP: {len(prime_power_atoms(CAP))}; "
          f"A built to {CAP}")

    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT seq_id FROM calx.sequences ORDER BY seq_id")
        seqs = [r[0] for r in cur.fetchall()]
        cur.execute("DELETE FROM kan.shadow_term")
        cur.execute("DELETE FROM kan.shadow_signature")

        sig_by = {}
        for sid in seqs:
            cur.execute(
                "SELECT term FROM kan.sequence_terms WHERE seq_id=%s ORDER BY idx",
                (sid,),
            )
            terms = [int(r[0]) for r in cur.fetchall()]
            coarse = []
            nwin = 0
            for n in terms:
                inw = 0 <= n <= CAP
                if inw:
                    sh = shadow(n, A, C16)
                    rho, fmean, fmax, supp = sh
                    nwin += 1
                    coarse.append((
                        round(fmean, 3) if fmean is not None else -1.0,
                        rho.bit_length() if isinstance(rho, int) else 0,
                        supp,
                    ))
                    a_at = A[n] if 0 <= n <= CAP else None
                    cur.execute(
                        "INSERT INTO kan.shadow_term "
                        "(seq,n,in_window,rho,a_count,f1_mean,f1_max,f1_support) "
                        "VALUES (%s,%s,TRUE,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (seq,n) DO UPDATE SET in_window=TRUE,"
                        " rho=EXCLUDED.rho,a_count=EXCLUDED.a_count,"
                        " f1_mean=EXCLUDED.f1_mean,f1_max=EXCLUDED.f1_max,"
                        " f1_support=EXCLUDED.f1_support",
                        (sid, n, str(rho),
                         str(a_at) if a_at is not None else None,
                         (round(fmean, 6) if fmean is not None else None),
                         fmax if fmax is not None else None, supp),
                    )
                else:
                    cur.execute(
                        "INSERT INTO kan.shadow_term "
                        "(seq,n,in_window,rho,a_count,f1_mean,f1_max,f1_support) "
                        "VALUES (%s,%s,FALSE,NULL,NULL,NULL,NULL,0) "
                        "ON CONFLICT (seq,n) DO UPDATE SET in_window=FALSE",
                        (sid, n),
                    )
            sig = hashlib.sha256(
                repr(sorted(coarse)).encode()).hexdigest()
            sig_by[sid] = sig
            cur.execute(
                "INSERT INTO kan.shadow_signature (seq,window_terms,sig_sha) "
                "VALUES (%s,%s,%s) ON CONFLICT (seq) DO UPDATE SET "
                " window_terms=EXCLUDED.window_terms,sig_sha=EXCLUDED.sig_sha",
                (sid, nwin, sig),
            )

        # Separation test against the combined-invariant collision kernel
        cur.execute(
            "SELECT seq_a, seq_b FROM kan.combined_similarity ORDER BY seq_a,seq_b"
        )
        pairs = cur.fetchall()
        cur.execute("DELETE FROM kan.shadow_separation")
        resolved = 0
        for a, b in pairs:
            sa, sb = sig_by.get(a), sig_by.get(b)
            distinct = (sa is not None and sb is not None and sa != sb)
            cur.execute(
                "INSERT INTO kan.shadow_separation "
                "(seq_a,seq_b,combined_equal,shadow_distinct,resolves) "
                "VALUES (%s,%s,TRUE,%s,%s) ON CONFLICT (seq_a,seq_b) DO UPDATE "
                "SET shadow_distinct=EXCLUDED.shadow_distinct,"
                "resolves=EXCLUDED.resolves",
                (a, b, distinct, distinct),
            )
            resolved += 1 if distinct else 0
            print(f"  collision {a} ~ {b}: shadow_distinct={distinct}")
        conn.commit()

    print(f"\n  shadow separates {resolved}/{len(pairs)} combined collisions")
    return 0 if (pairs and resolved == len(pairs)) else (0 if not pairs else 1)


if __name__ == "__main__":
    sys.exit(main())
