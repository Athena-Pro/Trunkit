#!/usr/bin/env bash
# lean_check.sh — driver for the cert Lean bridge (T1).
#
#   lean_check.sh <project_root> <Fully.Qualified.Decl>
#
# 1. fetch cached Mathlib oleans (no build-from-source, no network at attest time)
# 2. `lake build` the project
# 3. run tools/AxiomAudit.lean against <decl>; print its JSON verdict to stdout
#
# Exit code is the verdict consumed by tools/cert_formal.py:
#   0  -> valid   (built, sorry-free, axioms ⊆ allowed)
#   1  -> refuted (build failed, or sorry / disallowed axiom)
#   2  -> error   (bad usage, decl not found)
#
# Timeout via LEAN_CHECKER_TIMEOUT (seconds, default 1200).
# For UNTRUSTED / AI-supplied proofs, invoke this script under a sandbox wrapper
# (e.g. `firejail --net=none --read-only=<root>` or a rootless container) and
# register that wrapped command as the artifact's checker_cmd. Lean elaboration
# executes arbitrary code, so building an external proof == running it.
set -euo pipefail

ROOT="${1:?usage: lean_check.sh <project_root> <decl>}"
DECL="${2:?usage: lean_check.sh <project_root> <decl>}"
TIMEOUT="${LEAN_CHECKER_TIMEOUT:-1200}"

# resolve auditor path relative to this script, before we cd away
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUDITOR="$SCRIPT_DIR/AxiomAudit.lean"

cd "$ROOT"

# 1. Mathlib cache (best-effort; absent cache just means a slower build)
if grep -q 'require .*[Mm]athlib' lakefile.lean lakefile.toml 2>/dev/null; then
  timeout "$TIMEOUT" lake exe cache get >/dev/null 2>&1 || true
fi

# 2. build — failure ⇒ refuted
if ! timeout "$TIMEOUT" lake build >&2; then
  echo "{\"decl\":\"$DECL\",\"ok\":false,\"error\":\"lake build failed\"}"
  exit 1
fi

# 3. axiom + sorry audit; its stdout (JSON) and exit code are the verdict.
#    Module = the decl's leading namespace component (e.g. Erdos728.main -> Erdos728).
#    For deeper module paths, register the module explicitly and pass it here.
MODULE="${LEAN_AUDIT_MODULE:-${DECL%%.*}}"
timeout "$TIMEOUT" lake env lean --run "$AUDITOR" "$MODULE" "$DECL"
