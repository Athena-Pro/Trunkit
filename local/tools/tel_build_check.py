#!/usr/bin/env python3
"""Live build/test verification for tel_project claims (Tier 2).
Replaces baked 'build:valid' with an actual build run, three-valued:
  exit 0   -> valid
  exit !=0 -> failed  (-> cert refuted)
  timeout / no toolchain / no manifest -> unverified
Re-runnable; records into cert.live_build. Optional --only <claim_id>.
"""
import os, re, subprocess, json, sys, psycopg
DSN = "postgresql://trunk:trunk@localhost:5434/trunk"
TIMEOUT = int(os.environ.get("BUILD_TIMEOUT", "300"))
ONLY = None
if "--only" in sys.argv: ONLY = int(sys.argv[sys.argv.index("--only")+1])
# Git bash (NOT WSL bash): it carries the Windows Erlang/Elixir toolchain on PATH.
GIT_BASH = next((b for b in (r"C:/Program Files/Git/bin/bash.exe",
                             r"C:/Program Files/Git/usr/bin/bash.exe") if os.path.exists(b)), None)
# script-based launchers that CreateProcess can resolve directly
LAUNCHER = {"mix": "mix.bat"}

def dir_and_cmd(path):
    for d in (path, os.path.dirname(path)):
        if os.path.isfile(os.path.join(d, "Cargo.toml")):
            extra = ["--bin","telc"] if os.path.basename(path)=="src" else []
            return d, ["cargo","check","--quiet"]+extra
        if os.path.isfile(os.path.join(d, "rebar.config")): return d, ["rebar3","compile"]
        if os.path.isfile(os.path.join(d, "mix.exs")):       return d, ["mix","compile"]
        # Lean 4 lake project (e.g. lean-formalization/CondensedTEL): elaboration
        # health of the formal tower, not just cargo builds (2026-07-17).
        if os.path.isfile(os.path.join(d, "lakefile.lean")): return d, ["lake","build"]
    return None, None

env = {**os.environ, "CARGO_TERM_COLOR":"never", "MIX_ENV":"dev", "HEX_OFFLINE":"1"}
with psycopg.connect(DSN) as conn, conn.cursor() as cur:
    cur.execute("SELECT id, subject_ref->>'path' FROM cert.claim WHERE subject_kind='tel_project' ORDER BY id")
    rows = [r for r in cur.fetchall() if ONLY is None or r[0]==ONLY]
    for cid, path in rows:
        d, cmd = dir_and_cmd(path)
        if not cmd:
            status, tool, detail, cmds = "unverified", None, "no recognized build manifest", None
        else:
            tool, cmds = cmd[0], " ".join(cmd)
            def _run(argv):
                return subprocess.run(argv, cwd=d, capture_output=True, text=True,
                                      timeout=TIMEOUT, stdin=subprocess.DEVNULL, env=env)
            try:
                try:
                    p = _run([LAUNCHER.get(cmd[0], cmd[0])] + cmd[1:])
                except FileNotFoundError:
                    # Windows: rebar3 is an extensionless escript CreateProcess can't
                    # resolve; retry via GIT bash (which has the Windows toolchain).
                    if not GIT_BASH:
                        raise
                    p = _run([GIT_BASH, "-lc", cmds])
                status = "valid" if p.returncode==0 else "failed"
                detail = ((p.stderr or "")+(p.stdout or ""))[-500:]
                # env couldn't invoke the toolchain itself -> unverified, NOT a real failure
                if status == "failed" and re.search(r"not found|No such file|exec: .*: not found", detail):
                    status, detail = "unverified", "toolchain not invocable in checker env: " + detail[:160]
            except subprocess.TimeoutExpired:
                status, detail = "unverified", f"timeout after {TIMEOUT}s"
            except Exception as e:
                status, detail = "unverified", f"{type(e).__name__}: {str(e)[:200]}"
        cur.execute("""INSERT INTO cert.live_build(claim_id,tool,cmd,status,detail,checked_at)
                       VALUES(%s,%s,%s,%s,%s,now())
                       ON CONFLICT(claim_id) DO UPDATE SET tool=EXCLUDED.tool,cmd=EXCLUDED.cmd,
                         status=EXCLUDED.status,detail=EXCLUDED.detail,checked_at=now()""",
                    (cid, tool, cmds, status, detail))
        conn.commit()
        print(f"  {cid} {status:10} {tool or '-':8} {d or path}")
print("live build check complete")
