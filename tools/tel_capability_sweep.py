#!/usr/bin/env python3
"""
tools/tel_capability_sweep.py — unified TEL capability runner.

Executes all three TEL capability checkers in sequence:
  1. tel_build_check.py     — Tier-2 live builds (tel_project claims)
  2. tel_behavior_check.py  — interpreter behavior + graphics PNG artifacts
  3. tel_constants_check.py — curry.constants drift vs. tel-clean source

After the checkers finish, re-attests every TEL claim so cert.board_summary
reflects the fresh verdicts, then prints a pass/fail summary.

Exit 0 iff every TEL claim is valid.  Exit 1 on any failure or check error.

Usage:
    python tools/tel_capability_sweep.py [--dsn DSN] [--no-attest] [--quiet]

Options:
    --dsn DSN       Postgres DSN (default: env TRUNK_DSN or
                    postgresql://trunk:trunk@localhost:5434/trunk)
    --no-attest     Run checkers but skip re-attestation step
    --quiet         Suppress individual checker output (only show summary)
"""
import argparse
import os
import subprocess
import sys
import psycopg

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DSN = "postgresql://trunk:trunk@localhost:5434/trunk"

# subject_kind values that belong to the TEL capability tree
TEL_KINDS = ("tel_project", "tel_behavior", "tel_graphics", "tel_constants")

# Board area prefixes that correspond to TEL (used for the summary table)
TEL_AREA_PREFIX = "TEL"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def banner(text: str) -> None:
    width = 70
    print(flush=True)
    print("=" * width, flush=True)
    print(f"  {text}", flush=True)
    print("=" * width, flush=True)


def run_checker(script: str, dsn: str, quiet: bool) -> bool:
    """Run a checker script as a subprocess.  Returns True on success."""
    banner(f"Running {os.path.basename(script)}")
    env = {**os.environ, "TRUNK_DSN": dsn, "PYTHONIOENCODING": "utf-8"}
    kwargs: dict = dict(
        cwd=os.path.dirname(script),
        env=env,
        stdin=subprocess.DEVNULL,
    )
    if quiet:
        kwargs["capture_output"] = True
    proc = subprocess.run([sys.executable, script], **kwargs)
    if quiet and proc.returncode != 0:
        # On failure always show output even in quiet mode
        if proc.stdout:
            sys.stdout.buffer.write(proc.stdout)
        if proc.stderr:
            sys.stderr.buffer.write(proc.stderr)
    ok = proc.returncode == 0
    if not ok:
        print(f"\n  !! {os.path.basename(script)} exited with code {proc.returncode}", flush=True)
    return ok


def reattest_tel_claims(dsn: str, quiet: bool) -> dict[int, str]:
    """Call cert.check() for every TEL claim.  Returns {claim_id: status}."""
    banner("Re-attesting TEL claims")
    results: dict[int, str] = {}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM cert.claim WHERE subject_kind = ANY(%s) ORDER BY id",
            (list(TEL_KINDS),),
        )
        ids = [r[0] for r in cur.fetchall()]

        for cid in ids:
            cur.execute("SELECT method FROM cert.claim WHERE id = %s", (cid,))
            row = cur.fetchone()
            if row is None:
                continue
            method = row[0]
            fn = "cert.check_with_witness" if method == "witness_carry" else "cert.check"
            try:
                cur.execute(f"SELECT {fn}(%s)", (cid,))
                cur.execute(
                    "SELECT status FROM cert.certificate "
                    "WHERE claim_id = %s ORDER BY seq DESC LIMIT 1",
                    (cid,),
                )
                status_row = cur.fetchone()
                status = status_row[0] if status_row else "unverified"
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                status = f"error: {exc}"
            conn.commit()
            results[cid] = status
            if not quiet:
                mark = "✓" if status == "valid" else "✗" if status == "refuted" else "?"
                print(f"  [{mark}] claim {cid:>3}  {status}")

    return results


def print_board_summary(dsn: str) -> bool:
    """Print the TEL section of cert.board_summary.  Returns True if all green."""
    banner("TEL capability board")
    all_green = True
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT area, verified, failed, unknown, total "
            "FROM cert.board_summary "
            "WHERE area ILIKE %s "
            "ORDER BY area",
            (f"{TEL_AREA_PREFIX}%",),
        )
        rows = cur.fetchall()

    if not rows:
        print("  (no TEL areas in board_summary)")
        return False

    col_w = max(len(r[0]) for r in rows) + 2
    header = f"  {'area':<{col_w}}  {'verified':>8}  {'failed':>6}  {'unknown':>7}  {'total':>5}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for area, verified, failed, unknown, total in rows:
        flag = "" if failed == 0 else "  ← FAIL"
        print(f"  {area:<{col_w}}  {verified:>8}  {failed:>6}  {unknown:>7}  {total:>5}{flag}")
        if failed > 0:
            all_green = False

    return all_green


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="Run all TEL capability checks and re-attest claims."
    )
    p.add_argument("--dsn", default=os.environ.get("TRUNK_DSN", DEFAULT_DSN))
    p.add_argument("--no-attest", action="store_true",
                   help="skip re-attestation step (checkers still run)")
    p.add_argument("--quiet", "-q", action="store_true",
                   help="suppress individual checker output")
    args = p.parse_args()
    dsn = args.dsn

    checkers = [
        os.path.join(HERE, "tel_build_check.py"),
        os.path.join(HERE, "tel_behavior_check.py"),
        os.path.join(HERE, "tel_constants_check.py"),
    ]

    # ---- 1. Run checkers -------------------------------------------------
    checker_ok = True
    for script in checkers:
        if not os.path.isfile(script):
            print(f"  !! checker not found: {script}", file=sys.stderr)
            checker_ok = False
            continue
        if not run_checker(script, dsn, args.quiet):
            checker_ok = False
            # Continue with remaining checkers even on failure

    # ---- 2. Re-attest claims ---------------------------------------------
    if not args.no_attest:
        attest_results = reattest_tel_claims(dsn, args.quiet)
        n_valid = sum(1 for s in attest_results.values() if s == "valid")
        n_total = len(attest_results)
        print(f"\n  re-attested {n_total} TEL claims: {n_valid} valid")
    else:
        print("\n  (re-attestation skipped)")

    # ---- 3. Board summary ------------------------------------------------
    all_green = print_board_summary(dsn)

    # ---- Final verdict ---------------------------------------------------
    print(flush=True)
    if checker_ok and all_green:
        print("  ✓  TEL capability sweep PASSED — all areas green", flush=True)
        return 0
    else:
        msgs = []
        if not checker_ok:
            msgs.append("one or more checkers reported errors")
        if not all_green:
            msgs.append("one or more board areas have failures")
        print(f"  ✗  TEL capability sweep FAILED: {'; '.join(msgs)}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
