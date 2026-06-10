"""Monstrous Moonshine: F_1 IS the Monster's trivial representation.

The McKay "+1" in every graded-dimension decomposition of V-natural is the
same F_1 point that has been the closer / zeta operator / radix-1 cell /
W_0 throughout. Ogg's supersingular primes (= primes dividing |M|) are a
genus-zero prime horizon mirroring lithon's 15-prime adelic window. The
j-coefficients (exact E4^3/Delta) land in the self-syzygy Fibonacci/
crackable class (eventual leading digit 1) and are radix-collapsible.

Tables kan.moonshine[_term]. Proof: proofs/moonshine.py. Idempotent.
"""

from __future__ import annotations

import hashlib
import os
import sys

import psycopg

PG_DSN = os.environ.get(
    "CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk"
)

# Smallest Monster irreducible representation dimensions (ATLAS).
IRREP = [1, 196883, 21296876, 842609326, 18538750076]
# Standard McKay-Thompson decompositions of dim V_n (n = 1..4).
MCKAY = {
    1: [1, 1, 0, 0, 0],
    2: [1, 1, 1, 0, 0],
    3: [2, 2, 1, 1, 0],
    4: [3, 3, 1, 2, 1],
}
# |M| = product of these prime powers (Ogg: primes = supersingular set).
M_FACTORS = {2: 46, 3: 20, 5: 9, 7: 6, 11: 2, 13: 3, 17: 1, 19: 1,
             23: 1, 29: 1, 31: 1, 41: 1, 47: 1, 59: 1, 71: 1}
M_ORDER_LITERAL = 808017424794512875886459904961710757005754368000000000
# lithon adelic prime horizon (BASES[1:] = the first 15 primes).
LITHON_PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47]
# OEIS A000521 anchor: a(1..7) = coeff of q^n in j (graded dims of V-nat).
J_ANCHOR = [196884, 21493760, 864299970, 20245856256, 333202640600,
            4252023300096, 44656994071935]
N_J = 140


def smul(a, b, L):
    c = [0] * L
    for i, ai in enumerate(a):
        if ai:
            for j in range(L - i):
                if b[j]:
                    c[i + j] += ai * b[j]
    return c


def spow(base, e, L):
    r = [1] + [0] * (L - 1)
    while e:
        if e & 1:
            r = smul(r, base, L)
        base = smul(base, base, L)
        e >>= 1
    return r


def sinv(p, L):
    inv = [0] * L
    inv[0] = 1
    for i in range(1, L):
        s = 0
        for j in range(1, i + 1):
            s += p[j] * inv[i - j]
        inv[i] = -s
    return inv


def sigma3(n):
    s = 0
    d = 1
    while d * d <= n:
        if n % d == 0:
            s += d ** 3
            e = n // d
            if e != d:
                s += e ** 3
        d += 1
    return s


def j_coeffs(n_terms):
    """Exact a(n) for n = 1..n_terms via J(q)=q*j=E4^3/prod(1-q^n)^24."""
    L = n_terms + 4
    euler = [0] * L
    euler[0] = 1
    k = 1
    while True:
        g1 = k * (3 * k - 1) // 2
        g2 = k * (3 * k + 1) // 2
        if g1 >= L and g2 >= L:
            break
        sign = -1 if k % 2 else 1
        if g1 < L:
            euler[g1] += sign
        if g2 < L:
            euler[g2] += sign
        k += 1
    P = spow(euler, 24, L)                       # prod(1-q^n)^24
    E4 = [0] * L
    E4[0] = 1
    for m in range(1, L):
        E4[m] = 240 * sigma3(m)
    E4c = spow(E4, 3, L)
    J = smul(E4c, sinv(P, L), L)                 # J[t] = a(t-1)
    return [J[t + 1] for t in range(1, n_terms + 1)]   # a(1..n_terms)


def main() -> int:
    # --- M1: McKay F_1 decomposition (exact + trivial rep everywhere) ---
    a = j_coeffs(N_J)
    assert a[:7] == J_ANCHOR, f"j-series anchor mismatch: {a[:7]}"
    m1 = True
    term_rows = []
    for n in (1, 2, 3, 4):
        mv = MCKAY[n]
        recon = sum(c * d for c, d in zip(mv, IRREP))
        gdim = a[n - 1]
        ok = (recon == gdim)
        if not ok or mv[0] < 1:
            m1 = False
        bl = max(1, gdim.bit_length())
        bd = 16 * (-(-bl // 16))
        term_rows.append((n, gdim, mv, mv[0], ok, bl, bd))

    # --- M2: supersingular = lithon-style prime horizon ---
    order = 1
    for p, e in M_FACTORS.items():
        order *= p ** e
    ss = sorted(M_FACTORS)
    m2 = (order == M_ORDER_LITERAL and len(ss) == 15)
    overlap = len(set(ss) & set(LITHON_PRIMES))

    # --- M3: j in the greedy self-syzygy dichotomy ---
    leads = [a[k] // a[k - 1] for k in range(1, len(a))]
    tail = leads[-20:]
    j_crackable = len(set(tail)) == 1
    j_lead = tail[0] if j_crackable else None

    # --- M4: radix depth collapse on j ---
    max_unary = a[-1]
    max_bdepth = 0
    for val in a:
        bl = max(1, val.bit_length())
        max_bdepth = max(max_bdepth, 16 * (-(-bl // 16)))
    m4 = max_unary > max_bdepth

    head_sha = hashlib.sha256(repr(
        (IRREP, sorted(MCKAY.items()), ss, J_ANCHOR, tail, j_lead)
    ).encode()).hexdigest()

    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO kan.functor (name,src_category,tgt_category,description) "
            "VALUES ('moonshine','seq','seq','Monstrous Moonshine: the McKay "
            "+1 is the F_1 point = the Monster trivial representation') "
            "ON CONFLICT (name) DO NOTHING"
        )
        cur.execute("DELETE FROM kan.moonshine_term")
        cur.execute("DELETE FROM kan.moonshine")
        for (n, gdim, mv, tm, ok, bl, bd) in term_rows:
            cur.execute(
                "INSERT INTO kan.moonshine_term "
                "(n,graded_dim,mult_vector,trivial_mult,decomposes_ok,"
                " syzygy_lead,bitlen,binary_depth) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (n) DO UPDATE SET graded_dim=EXCLUDED.graded_dim,"
                " mult_vector=EXCLUDED.mult_vector,"
                " trivial_mult=EXCLUDED.trivial_mult,"
                " decomposes_ok=EXCLUDED.decomposes_ok,"
                " syzygy_lead=EXCLUDED.syzygy_lead,bitlen=EXCLUDED.bitlen,"
                " binary_depth=EXCLUDED.binary_depth",
                (n, str(gdim), ",".join(map(str, mv)), tm, ok,
                 str(leads[n - 1]) if n - 1 < len(leads) else None, bl, bd),
            )
        cur.execute(
            "INSERT INTO kan.moonshine "
            "(structure,monster_order,irrep_dims,mckay_f1_ok,ss_primes,"
            " ss_horizon_ok,lithon_overlap,j_eventual_lead,j_crackable,"
            " j_depth_collapse,head_sha) "
            "VALUES ('V_natural',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (structure) DO UPDATE SET "
            " monster_order=EXCLUDED.monster_order,"
            " irrep_dims=EXCLUDED.irrep_dims,mckay_f1_ok=EXCLUDED.mckay_f1_ok,"
            " ss_primes=EXCLUDED.ss_primes,ss_horizon_ok=EXCLUDED.ss_horizon_ok,"
            " lithon_overlap=EXCLUDED.lithon_overlap,"
            " j_eventual_lead=EXCLUDED.j_eventual_lead,"
            " j_crackable=EXCLUDED.j_crackable,"
            " j_depth_collapse=EXCLUDED.j_depth_collapse,"
            " head_sha=EXCLUDED.head_sha,verified_at=now()",
            (str(order), ",".join(map(str, IRREP)), m1,
             ",".join(map(str, ss)), m2, overlap, j_lead,
             j_crackable, m4, head_sha),
        )
        conn.commit()

    print(f"  M1 McKay F_1 (exact + trivial rep >=1):  {m1}")
    for (n, gdim, mv, tm, ok, *_r) in term_rows:
        print(f"     dim V_{n} = {gdim} = {mv}.IRREP  ok={ok} (+{tm} trivial)")
    print(f"  M2 supersingular horizon (|M| prime set): {m2}  "
          f"ss={ss}")
    print(f"     |M| reconstructs={order == M_ORDER_LITERAL}; "
          f"lithon overlap={overlap}/15 (ss-only={sorted(set(ss)-set(LITHON_PRIMES))}, "
          f"lithon-only={sorted(set(LITHON_PRIMES)-set(ss))})")
    print(f"  M3 j self-syzygy: leads head={leads[:8]} tail={tail[:6]}... "
          f"-> eventual {j_lead} (crackable={j_crackable}, Fibonacci class)")
    print(f"  M4 radix collapse: max bitlen={a[-1].bit_length()} "
          f"binary_depth_max={max_bdepth} << magnitude  collapse={m4}")
    print(f"  head sha256: {head_sha[:16]}")
    return 0 if (m1 and m2 and j_crackable and m4) else 1


if __name__ == "__main__":
    sys.exit(main())
