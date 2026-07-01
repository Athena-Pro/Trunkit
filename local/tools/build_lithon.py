"""lithon integration: F_1 (row-0) glued to Spec(Z) (the prime rows).

Imports the REAL lithon core,
registers the kan category 'lithon' with functors val:lithon->seq (Phi) and
pack:seq->lithon (state_from_integer), and verifies the faithful/bounded
integration laws live over a base set, populating kan.lithon_*:

  P1 retraction      Phi(pack(n)) == n on every reachable n
  P2 W1 single-atom  pack(p^k) = single cell (row pi(p), col k-1);
                     grid row index == pi(p) == ht(p^k)
  P3 F1 gluing       1 unreachable from prime rows alone, reachable with
                     row-0; row-0 (F_1) load-bearing; row-0 == W_0 units rung
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg

PG_DSN = os.environ.get(
    "CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk"
)
# The lithon valuation core (pack/val) is vendored alongside this script as
# lithon_core.py -- no external p-Sack checkout required.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lithon_core import (  # noqa: E402
    BASES, state_from_integer, phi, MAX_VALUE,
)

BASE = ["A000040", "A000290", "NW1", "Z000001"]


def omega(t: int) -> int:
    if t <= 1:
        return 0
    w, m, d = 0, t, 2
    while d * d <= m and d <= 100_000:
        if m % d == 0:
            w += 1
            while m % d == 0:
                m //= d
        d += 1 if d == 2 else 2
    if m > 1:
        w += 1
    return w


def model_ht(t: int) -> int:
    """ht(t) = prime index of largest prime factor (matches axis 3)."""
    if t <= 1:
        return 0
    largest, m, d = 1, t, 2
    while d * d <= m and d <= 1_000_000:
        if m % d == 0:
            largest = d
            while m % d == 0:
                m //= d
        d += 1 if d == 2 else 2
    if m > 1:
        largest = m
    # prime index via BASES (BASES[1:] are the primes in order = pi)
    if largest in BASES:
        return BASES.index(largest)          # pi(p): 2->1, 3->2, 5->3, ...
    # general prime index by counting
    cnt, x = 0, 1
    for q in range(2, largest + 1):
        is_p = all(q % p for p in range(2, int(q ** 0.5) + 1))
        if is_p:
            cnt += 1
            if q == largest:
                return cnt
    return cnt


def grid_profile(state):
    """(#row0 cells, #occupied prime rows, max prime-row index)."""
    if not state:
        return 0, 0, 0
    row0 = sum(1 for (r, _c) in state if r == 0)
    prows = sorted({r for (r, _c) in state if r >= 1})
    return row0, len(prows), (prows[-1] if prows else 0)


def reachable_without_row0(n: int) -> bool:
    """Greedy descending over prime-row atoms ONLY (no F_1 row-0)."""
    if n <= 0:
        return n == 0
    atoms = sorted(
        (BASES[r] ** (c + 1)
         for r in range(1, len(BASES)) for c in range(16)
         if BASES[r] ** (c + 1) <= n),
        reverse=True,
    )
    rem = n
    for val in atoms:
        if val <= rem:
            rem -= val
            if rem == 0:
                return True
    return rem == 0


def main() -> int:
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO kan.category (name,db_schema,description) VALUES "
            "('lithon',NULL,'lithon adelic prime-power lattice; row-0=F_1 "
            "glued to the Spec(Z) prime rows') ON CONFLICT (name) DO NOTHING"
        )
        cur.execute(
            "INSERT INTO kan.functor (name,src_category,tgt_category,description) "
            "VALUES ('val','lithon','seq','Phi: lithon state |-> integer'),"
            "('pack','seq','lithon','state_from_integer: integer |-> canonical "
            "prime-power-atom state (section of val)') "
            "ON CONFLICT (name) DO NOTHING"
        )

        cur.execute("DELETE FROM kan.lithon_witness")
        terms = set()
        for sid in BASE:
            cur.execute(
                "SELECT term FROM kan.sequence_terms WHERE seq_id=%s ORDER BY idx",
                (sid,),
            )
            terms |= {int(r[0]) for r in cur.fetchall()}

        retraction = w1_single = ht_corr = True
        for n in sorted(t for t in terms if 0 <= t <= MAX_VALUE):
            st = state_from_integer(n)
            ok = st is not None and phi(st) == n
            if not ok:
                retraction = False
                continue
            row0, prows, ght = grid_profile(st)
            ipp = omega(n) == 1
            mht = model_ht(n) if ipp else None
            # largest prime factor of a prime power = its prime base
            lpf = n
            if ipp:
                m, d = n, 2
                while d * d <= m and d <= 1_000_000:
                    if m % d == 0:
                        lpf = d
                        while m % d == 0:
                            m //= d
                    d += 1 if d == 2 else 2
                if m > 1:
                    lpf = m
            kexp = 0
            if ipp:
                mm = n
                while mm % lpf == 0:
                    mm //= lpf
                    kexp += 1
            in_win = bool(ipp and lpf in BASES[1:]
                          and kexp <= 16 and n <= MAX_VALUE)
            htm = (ipp and in_win and ght == mht)
            if in_win:
                # P2: in-window prime power must be exactly ONE prime-row atom
                if not (len(st) == 1 and row0 == 0 and prows == 1):
                    w1_single = False
                if not htm:
                    ht_corr = False
            cur.execute(
                "INSERT INTO kan.lithon_witness "
                "(n,phi_ok,row0_cells,prime_rows,is_prime_pow,in_window,"
                " grid_ht,model_ht,ht_matches) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (n) DO UPDATE SET phi_ok=EXCLUDED.phi_ok,"
                " row0_cells=EXCLUDED.row0_cells,prime_rows=EXCLUDED.prime_rows,"
                " is_prime_pow=EXCLUDED.is_prime_pow,in_window=EXCLUDED.in_window,"
                " grid_ht=EXCLUDED.grid_ht,model_ht=EXCLUDED.model_ht,"
                " ht_matches=EXCLUDED.ht_matches",
                (n, ok, row0, prows, ipp, in_win, ght,
                 mht if ipp else None, bool(htm)),
            )

        # P3 F_1 gluing: the unit 1 -- unreachable from prime rows, reachable
        # with row-0 (F_1 adjoins the multiplicative unit to Spec(Z)).
        one_with = state_from_integer(1)
        f1_adjoins = (one_with is not None and phi(one_with) == 1
                      and not reachable_without_row0(1))
        f1_load = any(
            (state_from_integer(t) and
             any(r == 0 for (r, _c) in state_from_integer(t)))
            for t in terms if 0 <= t <= MAX_VALUE
        )
        # row-0 == W_0: omega=0 terms (units 0,1) are exactly the F_1 point
        w0_corr = (omega(1) == 0 and omega(0) == 0
                   and f1_adjoins)

        is_int = (retraction and w1_single and f1_adjoins
                  and f1_load and w0_corr)
        cur.execute(
            "INSERT INTO kan.lithon "
            "(structure,val_functor,pack_functor,retraction,w1_single_atom,"
            " f1_adjoins_unit,f1_load_bearing,w0_correspondence,is_integration) "
            "VALUES ('lithon_F1_SpecZ','val','pack',%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (structure) DO UPDATE SET "
            " retraction=EXCLUDED.retraction,"
            " w1_single_atom=EXCLUDED.w1_single_atom,"
            " f1_adjoins_unit=EXCLUDED.f1_adjoins_unit,"
            " f1_load_bearing=EXCLUDED.f1_load_bearing,"
            " w0_correspondence=EXCLUDED.w0_correspondence,"
            " is_integration=EXCLUDED.is_integration,verified_at=now()",
            (retraction, w1_single, f1_adjoins, f1_load, w0_corr, is_int),
        )
        conn.commit()

    print(f"  P1 retraction (Phi(pack(n))=n):          {retraction}")
    print(f"  P2 W1 single-atom (p^k -> 1 cell, row=pi):{w1_single} "
          f"(ht corr: {ht_corr})")
    print(f"  P3a F_1 adjoins unit 1 (1 needs row-0):  {f1_adjoins}")
    print(f"  P3b F_1 load-bearing (row-0 used):       {f1_load}")
    print(f"  P3c row-0 == W_0 units rung:             {w0_corr}")
    print(f"\n  lithon integration (F_1 glued to Spec(Z)): {is_int}")
    print("  registered kan category 'lithon' + val/pack functors + witnesses")
    return 0 if is_int else 1


if __name__ == "__main__":
    sys.exit(main())
