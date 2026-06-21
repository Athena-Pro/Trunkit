"""Shared helpers for the cert Lean bridge (T1).

Single source of truth for:
  - the closure-digest recipe (so the CLI that registers a Lean artifact and the
    harness that re-checks it compute the *same* trusted digest), and
  - the axiom/sorry gate (pure logic, unit-testable without a DB or a Lean
    toolchain).

No database and no `lake` dependency — importing this module is cheap and safe in
plain CI. The actual build/audit is shelled by tools/lean_check.sh; this module
only decides, given the auditor's JSON, whether the declaration is acceptable.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path

# Mathlib's three trusted axioms. native_decide's trust root (Lean.ofReduceBool)
# is intentionally NOT here; admit it only when explicitly allowed.
AXIOM_ALLOWED = frozenset({"propext", "Classical.choice", "Quot.sound"})
NATIVE_DECIDE_AXIOM = "Lean.ofReduceBool"

# Files that constitute a Lean proof's build closure (relative to project root).
_CLOSURE_TOP = ("lakefile.lean", "lakefile.toml", "lean-toolchain", "lake-manifest.json")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def discover_closure(project_root: Path) -> list[str]:
    """Repo-relative file list defining a Lean project's build closure.

    The manifest/toolchain files plus every ``*.lean`` source, excluding the
    ``.lake`` build directory. Deterministic (sorted, de-duplicated).
    """
    root = Path(project_root)
    rels: set[str] = set()
    for name in _CLOSURE_TOP:
        if (root / name).is_file():
            rels.add(name)
    for lean in root.rglob("*.lean"):
        if ".lake" in lean.parts:
            continue
        rels.add(str(lean.relative_to(root)).replace("\\", "/"))
    return sorted(rels)


def compute_file_digests(project_root: Path, relpaths: Iterable[str]) -> dict[str, str]:
    root = Path(project_root)
    return {rel: sha256_file(root / rel) for rel in relpaths}


def closure_digest(file_digests: Mapping[str, str]) -> str:
    """Canonical digest over a {relpath: hex_sha256} map.

    Recipe (must stay identical on register and re-check):
        lines := sorted("<relpath>:<hex_sha256>")
        digest := sha256("\\n".join(lines))
    """
    lines = sorted(f"{rel}:{dig}" for rel, dig in file_digests.items())
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def read_toolchain(project_root: Path) -> dict[str, str]:
    """Best-effort toolchain pin: lean-toolchain + Mathlib rev from lake-manifest."""
    root = Path(project_root)
    tc: dict[str, str] = {}
    p = root / "lean-toolchain"
    if p.is_file():
        tc["lean"] = p.read_text(encoding="utf-8").strip()
    m = root / "lake-manifest.json"
    if m.is_file():
        try:
            data = json.loads(m.read_text(encoding="utf-8"))
            for pkg in data.get("packages", []):
                if pkg.get("name") == "mathlib":
                    rev = pkg.get("rev") or pkg.get("inputRev")
                    if rev:
                        tc["mathlib_rev"] = rev
        except (ValueError, OSError):
            pass
    return tc


def audit_ok(axioms: Iterable[str], uses_sorry: bool, *, allow_native: bool = False) -> bool:
    """The Lean correctness gate: sorry-free AND axioms within the allowed set."""
    if uses_sorry:
        return False
    allowed = set(AXIOM_ALLOWED)
    if allow_native:
        allowed.add(NATIVE_DECIDE_AXIOM)
    return all(a in allowed for a in axioms)


def default_checker_cmd(project_root: str, target_decl: str) -> str:
    from calx import get_shared_data_dir
    script = get_shared_data_dir("tools") / "lean_check.sh"
    return f'"{script}" "{project_root}" "{target_decl}"'
