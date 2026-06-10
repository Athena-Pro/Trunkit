"""The F_1 radix axis over the recursively-defined corpus.

lithon row-0 = 16 unit cells. Unary reading (steps 70/73): cell=1, value=
popcount, multiplicity C(16,s) -- the zeta kernel. Binary reading: cell c =
2^c, unset cells are significant zeros. The F_1 slack of an explosive term
needs c_0 = a_n unit copies under unary (depth = magnitude) but only
ceil(bitlen/16) carry blocks under binary (depth = O(log a_n)) -- the
explosive depth collapses. The two readings reconcile on a_n; the radix only
trades depth against multiplicity. Tables kan.f1_radix[_term]. Proof:
proofs/f1_radix.py. Idempotent.
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
RECURSIVE = ["A000108", "A000110", "A001006", "A000045", "A000142"]
NAME = {
    "A000108": "Catalan", "A000110": "Bell", "A001006": "Motzkin",
    "A000045": "Fibonacci", "A000142": "Factorial",
}
COLS = 16                          # lithon row-0 width
BLOCK = 1 << COLS                  # 16-bit binary block radix = 65536


def binary_bijection() -> bool:
    """R1: the 16 powers 2^c are strictly super-increasing (per-cell count
    in {0,1} => the 16-bit code is a bijection onto [0,65535], multiplicity
    1), in contrast to the unary popcount whose value v has multiplicity
    C(16,v) > 1."""
    run = 0
    for c in range(COLS):
        if (1 << c) <= run:                        # must strictly exceed prefix
            return False
        run += 1 << c
    # multiplicity contrast on the small range fully covered by 16 cells
    binary_mult = {v: 0 for v in range(1 << COLS)}
    for code in range(1 << COLS):
        binary_mult[code] += 1                      # code IS the value (b=2)
    if any(m != 1 for m in binary_mult.values()):
        return False
    # unary: value = popcount, multiplicity C(16,v) -- strictly > 1 for 0<v<16
    return all(math.comb(COLS, v) > 1 for v in range(1, COLS))


def blocks_of(a_n: int):
    """Split a_n into base-65536 (16-bit) carry blocks, low to high."""
    blks = []
    m = a_n
    if m == 0:
        return [0]
    while m:
        blks.append(m & (BLOCK - 1))
        m >>= COLS
    return blks


def main() -> int:
    bij = binary_bijection()
    with psycopg.connect(PG_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO kan.functor (name,src_category,tgt_category,description) "
            "VALUES ('f1radix','seq','seq','F_1 radix axis: row-0 read in "
            "binary place-value -- the depth-collapsing dual of the unary "
            "zeta reading') ON CONFLICT (name) DO NOTHING"
        )
        cur.execute("DELETE FROM kan.f1_radix_term")
        cur.execute("DELETE FROM kan.f1_radix")

        for sid in RECURSIVE:
            cur.execute(
                "SELECT term FROM kan.sequence_terms WHERE seq_id=%s ORDER BY idx",
                (sid,),
            )
            a = [int(r[0]) for r in cur.fetchall()]
            if len(a) < 3:
                continue
            reconciles = True
            max_unary = 0
            max_bin = 0
            max_blocks = 0
            sig = []
            for n, term in enumerate(a):
                bitlen = max(1, term.bit_length())
                unary_depth = term                     # c_0 unit copies
                blks = blocks_of(term)
                nblocks = len(blks)
                binary_depth = COLS * nblocks
                # R3: decode the 16-bit blocks back to the integer
                recon = 0
                for b in reversed(blks):
                    recon = (recon << COLS) | b
                ok = (recon == term)
                if not ok:
                    reconciles = False
                max_unary = max(max_unary, unary_depth)
                max_bin = max(max_bin, binary_depth)
                max_blocks = max(max_blocks, nblocks)
                sig.append((n, bitlen, nblocks, binary_depth))
                cur.execute(
                    "INSERT INTO kan.f1_radix_term "
                    "(seq,n,term,bitlen,unary_depth,blocks,binary_depth,"
                    " reconstructs_ok) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (seq,n) DO UPDATE SET term=EXCLUDED.term,"
                    " bitlen=EXCLUDED.bitlen,unary_depth=EXCLUDED.unary_depth,"
                    " blocks=EXCLUDED.blocks,binary_depth=EXCLUDED.binary_depth,"
                    " reconstructs_ok=EXCLUDED.reconstructs_ok",
                    (sid, n, str(term), bitlen, str(unary_depth),
                     nblocks, binary_depth, ok),
                )
            collapses = max_unary > max_bin            # magnitude >> log
            head_sha = hashlib.sha256(repr(sig).encode()).hexdigest()
            cur.execute(
                "INSERT INTO kan.f1_radix "
                "(seq,n_terms,binary_bijection,depth_collapses,reconciles,"
                " max_unary_depth,max_binary_depth,max_blocks,head_sha) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (seq) DO UPDATE SET n_terms=EXCLUDED.n_terms,"
                " binary_bijection=EXCLUDED.binary_bijection,"
                " depth_collapses=EXCLUDED.depth_collapses,"
                " reconciles=EXCLUDED.reconciles,"
                " max_unary_depth=EXCLUDED.max_unary_depth,"
                " max_binary_depth=EXCLUDED.max_binary_depth,"
                " max_blocks=EXCLUDED.max_blocks,"
                " head_sha=EXCLUDED.head_sha,verified_at=now()",
                (sid, len(a), bij, collapses, reconciles,
                 str(max_unary), max_bin, max_blocks, head_sha),
            )
            print(f"  {sid} {NAME[sid]:<10s} unary_max(bitlen)={max_unary.bit_length():>4d} "
                  f"binary_depth_max={max_bin:>3d} blocks={max_blocks} "
                  f"collapse/{collapses} recon/{reconciles} sig={head_sha[:12]}")
        conn.commit()

    print(f"\n  R1 binary bijection (mult 1 vs C(16,s)): {bij}")
    print("  F_1 radix axis: unary depth = magnitude, binary depth = O(log)")
    return 0 if bij else 1


if __name__ == "__main__":
    sys.exit(main())
