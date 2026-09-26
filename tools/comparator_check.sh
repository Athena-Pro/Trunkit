#!/usr/bin/env bash
# comparator_check.sh — cert Lean bridge (T1), comparator checker kind.
#
#   comparator_check.sh <project_root> <config.json>
#
# Runs leanprover/comparator (github.com/leanprover/comparator) on a
# challenge/solution pair and prints ONE JSON verdict line in the same shape
# tools/AxiomAudit.lean prints, so tools/cert_formal.py consumes it unchanged:
#
#   {"decl":"<theorem names>","type":"…","axioms":[<permitted_axioms>],
#    "uses_sorry":false,"checker":"comparator","kernels":[…],"ok":true|false}
#
# WHY A SECOND CHECKER KIND. AxiomAudit judges one environment: is this
# declaration sorry-free and axiom-clean, and (117) has its statement closure
# drifted since it was bound. Comparator judges TWO: it builds the challenge
# (the statement, with `sorry`) and the solution in separate sandboxes, checks
# every declaration the statement uses is identical in both, checks the
# permitted axioms, and replays the proof through Lean's kernel and optionally
# independent ones (nanoda, eink0rn). That is the standard the 2026
# Navier-Stokes and Riemann-zeta certificates were published under, and it is
# the only one of the two that resists a deliberately crafted statement.
#
# "axioms" reports the challenge's permitted_axioms -- the set comparator
# ENFORCED, an upper bound on what the proof uses. cert_formal.py re-gates it
# against its own allowed set, so a challenge that permits Lean.ofReduceBool is
# refused unless LEAN_AUDIT_ALLOW_NATIVE is set, whatever comparator said.
#
# Exit codes (the lean_check.sh contract):
#   0  valid    comparator accepted
#   1  refuted  comparator rejected (statement mismatch, axiom, kernel failure)
#   2  error    could not run -- missing landrun/lean4export, bad config.
#               Comparator's sandbox needs Linux (landrun + systemd-run); on
#               any other host this is the honest answer, not a refutation.
#
# Env: COMPARATOR_CMD  (default `lake exe comparator`), COMPARATOR_LANDRUN,
#      COMPARATOR_LEAN4EXPORT, LEAN_CHECKER_TIMEOUT (seconds, default 3600).
set -uo pipefail

ROOT="${1:?usage: comparator_check.sh <project_root> <config.json>}"
CONFIG="${2:?usage: comparator_check.sh <project_root> <config.json>}"
TIMEOUT="${LEAN_CHECKER_TIMEOUT:-3600}"
CMD="${COMPARATOR_CMD:-lake exe comparator}"
PY="${PYTHON:-python}"

emit_error() {
  "$PY" -c 'import json,sys; print(json.dumps({"checker":"comparator","ok":False,"error":sys.argv[1]}))' "$1"
  exit 2
}

cd "$ROOT" || emit_error "project root not found: $ROOT"
[ -f "$CONFIG" ] || emit_error "comparator config not found: $CONFIG"

missing=()
command -v "${COMPARATOR_LANDRUN:-landrun}" >/dev/null 2>&1 || missing+=("landrun")
command -v "${COMPARATOR_LEAN4EXPORT:-lean4export}" >/dev/null 2>&1 || missing+=("lean4export")
if [ ${#missing[@]} -gt 0 ]; then
  emit_error "comparator prerequisites missing: ${missing[*]} (comparator's sandbox is Linux-only)"
fi

# Parse the config once, up front: a config we cannot read is an error, not a
# verdict. Prints: <decl>\t<axioms json>\t<kernels json>\t<challenge_module>
meta="$("$PY" - "$CONFIG" <<'EOF'
import json, sys
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
names = cfg.get("theorem_names") or []
if not names:
    sys.exit("config has no theorem_names")
kernels = ["lean"]
if cfg.get("enable_nanoda"):
    kernels.append("nanoda")
kernels += sorted((cfg.get("external_kernels") or {}).keys())
print("\t".join([",".join(names), json.dumps(cfg.get("permitted_axioms") or []),
                 json.dumps(kernels), str(cfg.get("challenge_module", ""))]))
EOF
)" || emit_error "unreadable comparator config: $CONFIG"
IFS=$'\t' read -r DECL AXIOMS KERNELS CHALLENGE <<<"$meta"

# shellcheck disable=SC2086  # CMD is a command line by design
timeout "$TIMEOUT" $CMD "$CONFIG" >&2
rc=$?
if [ $rc -eq 124 ]; then emit_error "comparator timed out after ${TIMEOUT}s"; fi

"$PY" - "$DECL" "$AXIOMS" "$KERNELS" "$CHALLENGE" "$rc" <<'EOF'
import json, sys
decl, axioms, kernels, challenge, rc = sys.argv[1:]
ok = rc == "0"
print(json.dumps({
    "decl": decl,
    "type": f"statement identical to challenge module {challenge}" if ok
            else f"comparator rejected against challenge module {challenge} (exit {rc})",
    "axioms": json.loads(axioms),
    "uses_sorry": False,
    "checker": "comparator",
    "kernels": json.loads(kernels),
    "ok": ok,
}))
EOF
[ $rc -eq 0 ] && exit 0 || exit 1
