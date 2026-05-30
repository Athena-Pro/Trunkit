#!/usr/bin/env python3
"""Subject-existence guard for tel_project claims (Trunkit finding #3).

The cert DB runs inside a container and cannot stat the host filesystem, so a
baked 'build:valid' probe can attest green against a subject that has moved or
been archived. This host-side guard stats each claim's subject_ref.path,
captures the build evidence once, and records existence into cert.subject_probe.
The tel_project probes (rewritten separately) gate on that row, three-valued:
  subject present  -> retain build verdict (valid)
  subject missing  -> ok = NULL (UNVERIFIED, not a silent green)

Run with --repoint to also correct each path to its canonical tel-clean location
(root or archive/) and write it back to subject_ref. Idempotent / re-runnable.
"""
import os, sys, json, psycopg
REPOINT = "--repoint" in sys.argv
CANON = "C:/AI-Local/tel-clean"
DSN = "postgresql://trunk:trunk@localhost:5434/trunk"

def resolve(path):
    base = os.path.basename(path.rstrip("/"))
    cand = os.path.join(CANON, base)
    if os.path.isdir(cand): return cand, True
    arch = os.path.join(CANON, "archive")
    if os.path.isdir(arch):
        for root, dirs, _ in os.walk(arch):
            if os.path.basename(root) == base:
                return root, True
    return path, os.path.exists(path)

with psycopg.connect(DSN) as conn, conn.cursor() as cur:
    cur.execute("SELECT id, subject_ref->>'path', probe_sql FROM cert.claim WHERE subject_kind='tel_project' ORDER BY id")
    for cid, path, probe in cur.fetchall():
        # capture the current build evidence once (run the existing baked probe)
        bev = None
        try:
            cur.execute(probe); r = cur.fetchone(); bev = r[1] if r else None
        except Exception:
            conn.rollback()
        if REPOINT:
            cpath, exists = resolve(path)
        else:
            cpath, exists = path, os.path.exists(path)
        fp = str(len(os.listdir(cpath))) if exists and os.path.isdir(cpath) else None
        cur.execute("""INSERT INTO cert.subject_probe(claim_id,path,exists,fingerprint,build_evidence,checked_at)
                       VALUES(%s,%s,%s,%s,%s,now())
                       ON CONFLICT(claim_id) DO UPDATE SET
                         path=EXCLUDED.path, exists=EXCLUDED.exists,
                         fingerprint=EXCLUDED.fingerprint, checked_at=now(),
                         build_evidence=COALESCE(cert.subject_probe.build_evidence,EXCLUDED.build_evidence)""",
                    (cid, cpath, exists, fp, json.dumps(bev) if bev is not None else None))
        if REPOINT and cpath != path:
            cur.execute("UPDATE cert.claim SET subject_ref=jsonb_set(subject_ref,'{path}',to_jsonb(%s::text)) WHERE id=%s",(cpath,cid))
            print(f"  repoint {cid}: {path} -> {cpath}")
        print(f"  {cid} exists={exists} {cpath}")
    conn.commit()
print("guard run complete" + (" (repointed)" if REPOINT else " (check-only)"))
