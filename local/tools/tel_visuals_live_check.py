#!/usr/bin/env python3
"""Live end-to-end check for calx visualisation renderers (Ulam spiral +
factorization mosaic).

Runs each renderer script, verifies the output PNG is a real PNG file, and
writes a verdict row into cert.tel_visuals_live.  The matching claims' probe_sql
reads that table.

Three-valued per claim:
  valid      – renderer exited 0 AND output is a real PNG
  failed     – renderer failed OR PNG missing/corrupt
  unverified – telc binary missing, DB unreachable, timeout, etc.

Re-runnable.  Optional --only <claim_id>.
"""
import os
import subprocess
import sys
import psycopg

DSN = os.environ.get("TRUNK_DSN", "postgresql://trunk:trunk@localhost:5434/trunk")
TIMEOUT = int(os.environ.get("VISUALS_TIMEOUT", "180"))

ONLY: int | None = None
if "--only" in sys.argv:
    ONLY = int(sys.argv[sys.argv.index("--only") + 1])

HERE = os.path.dirname(os.path.abspath(__file__))
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def verify_png(path: str) -> tuple[bool, str]:
    if not os.path.isfile(path):
        return False, f"PNG not produced: {path}"
    with open(path, "rb") as f:
        head = f.read(8)
    if head != PNG_MAGIC:
        return False, f"file exists but not a valid PNG: {path}"
    return True, f"valid PNG, {os.path.getsize(path)} bytes"


def main() -> int:
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cert.tel_visuals_live (
                claim_id   integer PRIMARY KEY,
                script     text,
                png_path   text,
                png_bytes  integer,
                status     text,
                detail     text,
                checked_at timestamptz
            )
        """)
        conn.commit()

        cur.execute(
            "SELECT id, subject_ref->>'script', subject_ref->>'repo', subject_ref->>'produces' "
            "FROM cert.claim WHERE subject_kind = 'tel_visuals_live' ORDER BY id"
        )
        rows = [r for r in cur.fetchall() if ONLY is None or r[0] == ONLY]

    if not rows:
        print("  no tel_visuals_live claims found (run tel_visuals_live_claim.sql first)")
        return 1

    env = {**os.environ, "TRUNK_DSN": DSN, "PYTHONIOENCODING": "utf-8"}

    for cid, script_rel, repo, png_rel in rows:
        repo = repo or "C:/AI-Local/tel-clean"
        script_path = os.path.join(HERE, script_rel) if script_rel else ""
        png_path    = os.path.join(repo, png_rel) if png_rel else ""

        if not os.path.isfile(script_path):
            status = "unverified"
            detail = f"renderer script not found: {script_path}"
            _write(cid, script_path, png_path, None, status, detail, DSN)
            print(f"  {cid} {status:10} {detail[:80]}")
            continue

        # Remove stale PNG so the run must produce it fresh
        if png_path and os.path.isfile(png_path):
            os.remove(png_path)

        try:
            proc = subprocess.run(
                [sys.executable, script_path],
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
                stdin=subprocess.DEVNULL,
                env=env,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode != 0:
                status = "failed"
                detail = f"renderer exited {proc.returncode}: {out[-300:]}"
            else:
                ok, png_detail = verify_png(png_path)
                status = "valid" if ok else "failed"
                detail = f"{png_detail} | {out[-200:]}"
        except subprocess.TimeoutExpired:
            status, detail = "unverified", f"timeout after {TIMEOUT}s"
        except Exception as exc:  # noqa: BLE001
            status, detail = "unverified", f"{type(exc).__name__}: {str(exc)[:200]}"

        png_bytes = os.path.getsize(png_path) if os.path.isfile(png_path) else None
        _write(cid, script_path, png_path, png_bytes, status, detail, DSN)
        print(f"  {cid} {status:10} {detail[:80]}")

    print("tel visuals live check complete")
    return 0


def _write(cid, script, png_path, png_bytes, status, detail, dsn):
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO cert.tel_visuals_live
                   (claim_id, script, png_path, png_bytes, status, detail, checked_at)
               VALUES (%s, %s, %s, %s, %s, %s, now())
               ON CONFLICT (claim_id) DO UPDATE SET
                   script=EXCLUDED.script, png_path=EXCLUDED.png_path,
                   png_bytes=EXCLUDED.png_bytes, status=EXCLUDED.status,
                   detail=EXCLUDED.detail, checked_at=now()""",
            (cid, script, png_path, png_bytes, status, detail),
        )
        conn.commit()


if __name__ == "__main__":
    sys.exit(main())
