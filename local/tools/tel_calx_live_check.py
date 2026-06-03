#!/usr/bin/env python3
"""Live end-to-end check for the calx→TEL→PNG bridge (capability-tree T1).

Runs tools/tel_calx_render.py to regenerate calx_render.tel from the live
calx DB, then verifies telc produces a valid PNG artifact.  Writes a verdict
into cert.tel_calx_live; the matching claim's probe_sql reads it.

Three-valued:
  valid      – render script exited 0 AND PNG is a real PNG
  failed     – render script failed OR PNG is missing/corrupt
  unverified – telc binary missing, DB unreachable, timeout, etc.

Re-runnable.  Optional --only <claim_id>.
"""
import json
import os
import subprocess
import sys
import psycopg

DSN = os.environ.get("TRUNK_DSN", "postgresql://trunk:trunk@localhost:5434/trunk")
TIMEOUT = int(os.environ.get("CALX_LIVE_TIMEOUT", "120"))

ONLY: int | None = None
if "--only" in sys.argv:
    ONLY = int(sys.argv[sys.argv.index("--only") + 1])

HERE = os.path.dirname(os.path.abspath(__file__))
RENDER_SCRIPT = os.path.join(HERE, "tel_calx_render.py")

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def verify_png(path: str) -> tuple[bool, str]:
    if not os.path.isfile(path):
        return False, f"PNG not produced: {path}"
    with open(path, "rb") as f:
        head = f.read(8)
    if head != PNG_MAGIC:
        return False, f"file exists but is not a valid PNG: {path}"
    size = os.path.getsize(path)
    return True, f"valid PNG, {size} bytes"


def main() -> int:
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        # Ensure backing table exists
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cert.tel_calx_live (
                claim_id   integer PRIMARY KEY,
                n          integer,
                tel_path   text,
                png_path   text,
                png_bytes  integer,
                status     text,
                detail     text,
                checked_at timestamptz
            )
        """)
        conn.commit()

        cur.execute(
            "SELECT id, subject_ref->>'repo', subject_ref->>'produces', "
            "       (subject_ref->>'n')::int "
            "FROM cert.claim WHERE subject_kind = 'tel_calx_live' ORDER BY id"
        )
        rows = [r for r in cur.fetchall() if ONLY is None or r[0] == ONLY]

    if not rows:
        print("  no tel_calx_live claims found (run tel_calx_live_claim.sql first)")
        return 1

    for cid, repo, png_rel, n in rows:
        repo = repo or "C:/AI-Local/tel-clean"
        png_rel = png_rel or "bootstrap/output/calx_divisors.png"
        png_path = os.path.join(repo, png_rel)

        # Remove stale PNG so we can confirm the run actually produced it
        if os.path.isfile(png_path):
            os.remove(png_path)

        env = {**os.environ, "TRUNK_DSN": DSN, "PYTHONIOENCODING": "utf-8"}
        try:
            proc = subprocess.run(
                [sys.executable, RENDER_SCRIPT],
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
                stdin=subprocess.DEVNULL,
                env=env,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode != 0:
                status = "failed"
                detail = f"render script exited {proc.returncode}: {out[-300:]}"
            else:
                ok, png_detail = verify_png(png_path)
                if ok:
                    status = "valid"
                    detail = f"{png_detail} | {out[-200:]}"
                else:
                    status = "failed"
                    detail = f"{png_detail} | script output: {out[-200:]}"
        except subprocess.TimeoutExpired:
            status, detail = "unverified", f"timeout after {TIMEOUT}s"
        except Exception as exc:  # noqa: BLE001
            status, detail = "unverified", f"{type(exc).__name__}: {str(exc)[:200]}"

        png_bytes = os.path.getsize(png_path) if os.path.isfile(png_path) else None
        tel_path = os.path.join(repo, "bootstrap", "output", "calx_render.tel")

        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO cert.tel_calx_live
                       (claim_id, n, tel_path, png_path, png_bytes, status, detail, checked_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                   ON CONFLICT (claim_id) DO UPDATE SET
                       n=EXCLUDED.n, tel_path=EXCLUDED.tel_path, png_path=EXCLUDED.png_path,
                       png_bytes=EXCLUDED.png_bytes, status=EXCLUDED.status,
                       detail=EXCLUDED.detail, checked_at=now()""",
                (cid, n, tel_path, png_path, png_bytes, status, detail),
            )
            conn.commit()

        print(f"  {cid} {status:10} {detail[:80]}")

    print("tel calx live check complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
