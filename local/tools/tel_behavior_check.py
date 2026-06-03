#!/usr/bin/env python3
"""Live behavioral verification for tel_behavior claims (capability-tree T1).

Runs the TEL interpreter on each claim's program and checks for the expected
output substring. Three-valued, mirroring tel_build_check.py:
  exit 0 AND expect-substring present -> valid
  ran but mismatch / nonzero exit     -> failed (-> cert refuted)
  binary/program missing, timeout     -> unverified

Re-runnable; records into cert.tel_behavior. Optional --only <claim_id>.
"""
import os, subprocess, sys, psycopg

DSN = "postgresql://trunk:trunk@localhost:5434/trunk"
TIMEOUT = int(os.environ.get("BEHAVIOR_TIMEOUT", "60"))
ONLY = None
if "--only" in sys.argv:
    ONLY = int(sys.argv[sys.argv.index("--only") + 1])


def telc_bin(repo):
    for c in ("target/debug/telc.exe", "target/debug/telc"):
        p = os.path.join(repo, c)
        if os.path.isfile(p):
            return p
    return None


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def artifact_ok(path):
    """Verify a produced artifact exists and, for .png, is a real PNG. Returns
    (ok, detail)."""
    if not os.path.isfile(path):
        return False, f"artifact not produced: {path}"
    if path.lower().endswith(".png"):
        with open(path, "rb") as f:
            head = f.read(8)
        if head != PNG_MAGIC:
            return False, f"artifact is not a valid PNG: {path}"
        size = os.path.getsize(path)
        return True, f"valid PNG, {size} bytes"
    return True, f"artifact present, {os.path.getsize(path)} bytes"


with psycopg.connect(DSN) as conn, conn.cursor() as cur:
    cur.execute(
        "SELECT id, subject_ref->>'repo', subject_ref->>'program', subject_ref->>'expect', "
        "subject_ref->>'produces' "
        "FROM cert.claim WHERE subject_kind IN ('tel_behavior','tel_graphics') ORDER BY id"
    )
    rows = [r for r in cur.fetchall() if ONLY is None or r[0] == ONLY]
    for cid, repo, program, expect, produces in rows:
        binp = telc_bin(repo or "")
        prog_path = os.path.join(repo or "", program or "")
        art_path = os.path.join(repo or "", produces) if produces else None
        if not binp or not os.path.isfile(prog_path):
            status, detail = "unverified", f"missing telc binary or program ({binp}, {prog_path})"
        else:
            try:
                if art_path and os.path.isfile(art_path):
                    os.remove(art_path)        # ensure the run actually re-creates it
                p = subprocess.run(
                    [binp, prog_path, "--interpret"],
                    cwd=repo,  # so a program's relative output paths resolve under the repo
                    capture_output=True, text=True, timeout=TIMEOUT,
                    stdin=subprocess.DEVNULL,
                )
                out = (p.stdout or "") + (p.stderr or "")
                ok = (p.returncode == 0) and (expect in out)
                detail = out[-260:]
                if ok and art_path:
                    a_ok, a_detail = artifact_ok(art_path)
                    ok = ok and a_ok
                    detail = f"{a_detail} | {detail}"
                status = "valid" if ok else "failed"
            except subprocess.TimeoutExpired:
                status, detail = "unverified", f"timeout after {TIMEOUT}s"
            except Exception as e:  # noqa: BLE001
                status, detail = "unverified", f"{type(e).__name__}: {str(e)[:200]}"
        cur.execute(
            """INSERT INTO cert.tel_behavior(claim_id,program,expect,status,detail,checked_at)
               VALUES(%s,%s,%s,%s,%s,now())
               ON CONFLICT(claim_id) DO UPDATE SET program=EXCLUDED.program,
                 expect=EXCLUDED.expect,status=EXCLUDED.status,detail=EXCLUDED.detail,checked_at=now()""",
            (cid, program, expect, status, detail),
        )
        conn.commit()
        print(f"  {cid} {status:10} {program}")
print("tel behavior check complete")
