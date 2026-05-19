"""OEIS b-file loader.

Pulls curated OEIS A-series b-files via HTTPS, parses them, and seeds
sequence_membership for any value that fits in our integers table.
Each sequence is tagged with an algebraic family label.

Two passes:
  1. backfill_local_families() — set family on the 34 locally-seeded sequences.
  2. fetch_oeis_whitelist()     — pull the curated b-files from oeis.org.

Skip rule: OEIS sequences that encode FUNCTION VALUES (τ, σ, ω, Ω, μ) don't
fit our sequence_membership schema — n is the *value*, idx is the *position*,
and these functions take the same value at many positions. We compute those
locally from the factorizations table instead.

Network etiquette: 1.0s sleep between requests, single retry on transient
failure, User-Agent identifies the project.
"""

from __future__ import annotations

import io
import os
import time
import urllib.error
import urllib.request
from collections.abc import Iterator

from calx import db


USER_AGENT = "calx OEIS loader (educational; one-shot; oeis.org/A123456/b123456.txt)"
RATE_LIMIT_S = 1.0
MAX_RETRIES = 1
TIMEOUT_S = 30


# Sequences whose membership is computable directly from our `integers`,
# `primes`, `factorizations`, and `divisor_sum` tables. After the OEIS
# fetch we replace the truncated b-file data with the full local computation,
# keeping the OEIS family/name/formula metadata.
SQL_OVERRIDES: dict[str, str] = {
    "A006512": """
        SELECT p1.p, ROW_NUMBER() OVER (ORDER BY p1.p)
        FROM primes p1
        WHERE EXISTS (SELECT 1 FROM primes p2 WHERE p2.p = p1.p - 2)
          AND p1.p <= %s
        ORDER BY p1.p
    """,
    # A005384 Sophie Germain primes: NOT overridden — local SQL requires 2p+1 ≤ N
    # so we miss SG primes with p in (N/2, N]. OEIS b-file gives full coverage.
    "A023200": """
        SELECT p1.p, ROW_NUMBER() OVER (ORDER BY p1.p)
        FROM primes p1
        WHERE EXISTS (SELECT 1 FROM primes p2 WHERE p2.p = p1.p + 4)
          AND p1.p <= %s
        ORDER BY p1.p
    """,
    "A005100": """
        SELECT n, ROW_NUMBER() OVER (ORDER BY n)
        FROM deficient_numbers WHERE n <= %s ORDER BY n
    """,
    "A003586": """
        SELECT s.n, ROW_NUMBER() OVER (ORDER BY s.n)
        FROM smooth_numbers s WHERE s.largest_prime_factor <= 3 AND s.n <= %s
        ORDER BY s.n
    """,
    "A051037": """
        SELECT s.n, ROW_NUMBER() OVER (ORDER BY s.n)
        FROM smooth_numbers s WHERE s.largest_prime_factor <= 5 AND s.n <= %s
        ORDER BY s.n
    """,
    "A002473": """
        SELECT s.n, ROW_NUMBER() OVER (ORDER BY s.n)
        FROM smooth_numbers s WHERE s.largest_prime_factor <= 7 AND s.n <= %s
        ORDER BY s.n
    """,
    "A014613": """
        SELECT n, ROW_NUMBER() OVER (ORDER BY n)
        FROM integers WHERE big_omega = 4 AND n <= %s ORDER BY n
    """,
    "A007304": """
        SELECT n, ROW_NUMBER() OVER (ORDER BY n)
        FROM integers WHERE omega = 3 AND big_omega = 3 AND is_squarefree AND n <= %s
        ORDER BY n
    """,
    "A002144": """
        SELECT p, ROW_NUMBER() OVER (ORDER BY p)
        FROM primes WHERE p > 2 AND p %% 4 = 1 AND p <= %s ORDER BY p
    """,
    "A002145": """
        SELECT p, ROW_NUMBER() OVER (ORDER BY p)
        FROM primes WHERE p %% 4 = 3 AND p <= %s ORDER BY p
    """,
    "A007528": """
        SELECT p, ROW_NUMBER() OVER (ORDER BY p)
        FROM primes WHERE p %% 6 = 5 AND p <= %s ORDER BY p
    """,
    "A002476": """
        SELECT p, ROW_NUMBER() OVER (ORDER BY p)
        FROM primes WHERE p %% 6 = 1 AND p <= %s ORDER BY p
    """,
    "A054753": """
        SELECT n, ROW_NUMBER() OVER (ORDER BY n)
        FROM integers WHERE omega = 2 AND big_omega = 3 AND n <= %s ORDER BY n
    """,
}


# ─── Algebraic family map for locally-seeded sequences ───────────────────────

LOCAL_FAMILY_MAP = {
    "A000040": "primality",
    "A000045": "recursive",
    "A000079": "multiplicative",
    "A000244": "multiplicative",
    "A000351": "multiplicative",
    "A000217": "figurate",
    "A000290": "figurate",
    "A000578": "figurate",
    "A000326": "figurate",
    "A000384": "figurate",
    "A000142": "recursive",
    "A000108": "recursive",
    "A000110": "recursive",
    "A001006": "recursive",
    "A005132": "recursive",
    "A001358": "almost_prime",
    "A014612": "almost_prime",
    "A005117": "signature_class",
    "A002808": "multiplicative",
    "A000396": "aliquot_class",
    "A005101": "aliquot_class",
    "A002182": "highly_composite",
    "A001359": "primality",
    "A006881": "signature_class",
}


# ─── Curated whitelist of new sequences to fetch ─────────────────────────────
#
# Each entry: (seq_id, family, name, formula).
# Restricted to set-membership sequences (value ∈ N where some property holds).
# Function-valued sequences are NOT here; they're computed locally.

WHITELIST: list[tuple[str, str, str, str]] = [
    # ── primality / prime structure ──────────────────────────────────────────
    ("A006512", "primality", "Upper twin primes",
        "p prime with p−2 prime"),
    ("A005384", "primality", "Sophie Germain primes",
        "p prime with 2p+1 prime"),
    ("A023200", "primality", "Cousin primes (lower)",
        "p prime with p+4 prime"),

    # ── aliquot / sociable structure ─────────────────────────────────────────
    ("A063990", "aliquot_class", "Amicable pair members",
        "n with σ(n)=σ(s(n)) and s(n)≠n where s(n)=σ(n)−n"),
    ("A002975", "aliquot_class", "Sociable cycle members",
        "n contained in some sociable aliquot cycle of length ≥ 3"),
    ("A005100", "aliquot_class", "Deficient numbers",
        "σ(n) < 2n"),

    # ── smooth / rough ───────────────────────────────────────────────────────
    ("A003586", "smooth", "3-smooth",
        "n with no prime factor > 3"),
    ("A051037", "smooth", "5-smooth",
        "n with no prime factor > 5"),
    ("A002473", "smooth", "7-smooth",
        "n with no prime factor > 7"),

    # ── almost-prime variants ────────────────────────────────────────────────
    ("A014613", "almost_prime", "4-almost primes",
        "Ω(n)=4"),
    ("A007304", "signature_class", "Sphenic numbers",
        "ω(n)=3 ∧ μ²(n)=1 (squarefree product of 3 distinct primes)"),

    # ── congruence classes of primes ─────────────────────────────────────────
    ("A002144", "congruence", "Pythagorean primes",
        "Primes ≡ 1 (mod 4)"),
    ("A002145", "congruence", "Gaussian primes (real)",
        "Primes ≡ 3 (mod 4)"),
    ("A007528", "congruence", "Primes ≡ 5 (mod 6)",
        "Primes p with p mod 6 = 5"),
    ("A002476", "congruence", "Primes ≡ 1 (mod 6)",
        "Primes p with p mod 6 = 1"),

    # ── signature classes ────────────────────────────────────────────────────
    ("A054753", "signature_class", "Numbers of form p²·q (signature [2,1])",
        "Ω(n)=3 ∧ ω(n)=2"),

    # ── highly composite friends ─────────────────────────────────────────────
    ("A002201", "highly_composite", "Superior highly composite",
        "HCNs at extremal positions of τ(n)/n^s"),
]


# ─── Fetcher ─────────────────────────────────────────────────────────────────

def fetch_b_file(seq_id: str) -> Iterator[tuple[int, int]]:
    """Yield (idx, value) from oeis.org/A___/b___.txt. Skips comments + blanks."""
    num = seq_id[1:].lstrip("0") or "0"
    url = f"https://oeis.org/{seq_id}/b{seq_id[1:]}.txt"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                data = resp.read().decode("utf-8", errors="replace")
            break
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < MAX_RETRIES:
                time.sleep(5)
                continue
            raise RuntimeError(f"fetch {seq_id}: {e}") from e

    for line in io.StringIO(data):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            idx_s, val_s = line.split(None, 1)
            yield int(idx_s), int(val_s)
        except ValueError:
            continue


def load_sequence(cur, seq_id: str, name: str, formula: str, family: str, lim: int) -> int:
    """Fetch + insert. Returns count of new memberships."""
    cur.execute(
        """
        INSERT INTO sequences (seq_id, name, seq_type, formula, family)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (seq_id) DO UPDATE
          SET name = EXCLUDED.name,
              seq_type = COALESCE(sequences.seq_type, EXCLUDED.seq_type),
              formula = EXCLUDED.formula,
              family  = EXCLUDED.family
        """,
        (seq_id, name, family, formula, family),
    )

    seen: set[int] = set()
    rows: list[tuple[str, int, int]] = []
    for idx, val in fetch_b_file(seq_id):
        if val < 1 or val > lim:
            continue
        if val in seen:
            continue
        seen.add(val)
        rows.append((seq_id, val, idx))

    if not rows:
        return 0

    with cur.copy(
        "COPY sequence_membership (seq_id, n, idx) FROM STDIN"
    ) as copy:
        for seq, n, idx in rows:
            try:
                copy.write_row((seq, n, idx))
            except Exception:
                # Likely PK conflict — fall through to per-row insert with ON CONFLICT
                pass

    # COPY can't ON-CONFLICT. If the seq was previously seeded, re-do as
    # per-row INSERTs that allow conflict resolution.
    return len(rows)


def load_sequence_safe(cur, seq_id: str, name: str, formula: str, family: str, lim: int) -> int:
    """Variant: per-row INSERT … ON CONFLICT DO NOTHING — slower but idempotent."""
    cur.execute(
        """
        INSERT INTO sequences (seq_id, name, seq_type, formula, family)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (seq_id) DO UPDATE
          SET name = EXCLUDED.name,
              seq_type = COALESCE(sequences.seq_type, EXCLUDED.seq_type),
              formula = EXCLUDED.formula,
              family  = EXCLUDED.family
        """,
        (seq_id, name, family, formula, family),
    )

    new = 0
    seen: set[int] = set()
    for idx, val in fetch_b_file(seq_id):
        if val < 1 or val > lim:
            continue
        if val in seen:
            continue
        seen.add(val)
        cur.execute(
            "INSERT INTO sequence_membership (seq_id, n, idx) VALUES (%s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            (seq_id, val, idx),
        )
        new += cur.rowcount or 0
    return new


# ─── Driver ──────────────────────────────────────────────────────────────────

def backfill_local_families(conn) -> int:
    """Set family on the locally-seeded sequences (+ orbit families)."""
    updated = 0
    with conn.cursor() as cur:
        for sid, fam in LOCAL_FAMILY_MAP.items():
            cur.execute(
                "UPDATE sequences SET family = %s WHERE seq_id = %s AND (family IS NULL OR family <> %s)",
                (fam, sid, fam),
            )
            updated += cur.rowcount
        # orbit-derived sequences
        cur.execute(
            "UPDATE sequences SET family = 'orbit' "
            "WHERE family IS NULL "
            "AND (seq_id LIKE 'collatz%%' OR seq_id LIKE 'aliquot%%')"
        )
        updated += cur.rowcount
    conn.commit()
    return updated


def replace_with_local_computation(cur, seq_id: str, lim: int) -> int:
    """For sequences with a known SQL definition, replace the (possibly truncated)
    OEIS-loaded membership rows with a full local computation."""
    sql = SQL_OVERRIDES.get(seq_id)
    if not sql:
        return 0
    cur.execute("DELETE FROM sequence_membership WHERE seq_id = %s", (seq_id,))
    cur.execute(sql, (lim,))
    rows = cur.fetchall()
    if not rows:
        return 0
    with cur.copy(
        "COPY sequence_membership (seq_id, n, idx) FROM STDIN"
    ) as copy:
        for n, idx in rows:
            copy.write_row((seq_id, n, idx))
    return len(rows)


def fetch_whitelist(conn, lim: int, only_family: str | None = None,
                    only_seq: str | None = None) -> dict:
    """Pull whitelist b-files. For sequences with a SQL override, replace
    the (typically OEIS-truncated) b-file data with the local full computation."""
    results: dict[str, int] = {}
    items = [e for e in WHITELIST
             if (only_family is None or e[1] == only_family)
             and (only_seq is None or e[0] == only_seq)]

    print(f"fetching {len(items)} sequences from oeis.org "
          f"(rate-limited to 1/{RATE_LIMIT_S}s)")

    with conn.cursor() as cur:
        for seq_id, family, name, formula in items:
            t0 = time.time()
            try:
                fetched = load_sequence_safe(cur, seq_id, name, formula, family, lim)
                if seq_id in SQL_OVERRIDES:
                    final = replace_with_local_computation(cur, seq_id, lim)
                    note = f"{fetched:>6} fetched -> {final:>6} after local recompute"
                else:
                    final = fetched
                    note = f"{fetched:>6} members"
                results[seq_id] = final
                print(f"  {seq_id:8s}  {family:18s}  {note}  ({time.time()-t0:.1f}s)")
            except RuntimeError as e:
                results[seq_id] = -1
                print(f"  {seq_id:8s}  FETCH FAILED: {e}")
            conn.commit()
            time.sleep(RATE_LIMIT_S)
    return results


def main():
    import argparse
    p = argparse.ArgumentParser(prog="oeis_loader")
    p.add_argument("--family", help="restrict to one algebraic family")
    p.add_argument("--seq-id", help="restrict to one OEIS A-number")
    p.add_argument("--backfill-only", action="store_true",
                   help="only set family on already-seeded sequences; skip OEIS fetch")
    p.add_argument("--dsn")
    args = p.parse_args()

    dsn = args.dsn or os.environ.get(
        "CALX_DSN",
        "postgresql://trunk:trunk@localhost:5434/trunk",
    )
    with db.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(n) FROM integers")
            lim = cur.fetchone()[0]
            assert lim, "no integers populated"
        print(f"DB range: 1..{lim:,}")

        updated = backfill_local_families(conn)
        print(f"backfilled family on {updated} existing sequence rows")

        if args.backfill_only:
            return

        fetch_whitelist(conn, lim, only_family=args.family, only_seq=args.seq_id)

        with conn.cursor() as cur:
            cur.execute(
                "SELECT family, COUNT(*) AS seqs, SUM(sz) AS total_members FROM ("
                "  SELECT s.family, COUNT(sm.n) AS sz"
                "  FROM sequences s LEFT JOIN sequence_membership sm USING (seq_id)"
                "  GROUP BY s.seq_id, s.family"
                ") t GROUP BY family ORDER BY total_members DESC NULLS LAST"
            )
            rows = cur.fetchall()
        print("\nCatalog by family:")
        print(f"  {'family':<20s}  {'#seqs':>6s}  {'#members':>10s}")
        for fam, nseq, total in rows:
            print(f"  {fam or '(unset)':<20s}  {nseq:>6}  {(total or 0):>10,}")


if __name__ == "__main__":
    main()
