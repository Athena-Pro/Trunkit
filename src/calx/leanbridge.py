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


def statement_closure(audit: Mapping) -> dict[str, tuple[str, str]] | None:
    """The auditor's ``statement_closure`` as ``{constant: (type_hash, value_hash)}``.

    ``None`` when the auditor did not emit one (an older AxiomAudit, or
    LEAN_AUDIT_NO_CLOSURE set). Callers must read ``None`` as "not observed",
    never as "no drift".
    """
    rows = audit.get("statement_closure")
    if rows is None:
        return None
    return {str(name): (str(th), str(vh)) for name, th, vh in rows}


def closure_manifest_digest(manifest: Mapping[str, tuple[str, str]]) -> str:
    """Canonical digest of a statement closure.

    Recipe (mirrored exactly by cert.record_closure_manifest in 117, which
    refuses a manifest whose digest does not match -- so the ledger can never
    hold a digest paired with a manifest it did not come from):

        lines  := sorted by name (code-point order, i.e. COLLATE "C"),
                  "<name>\\t<type_hash>\\t<value_hash>"
        digest := sha256("\\n".join(lines))
    """
    lines = [f"{n}\t{th}\t{vh}" for n, (th, vh) in sorted(manifest.items())]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def closure_diff(bound: Mapping[str, tuple[str, str]],
                 observed: Mapping[str, tuple[str, str]]) -> dict[str, list[str]]:
    """Which constants a statement's meaning moved through.

    ``changed`` is the actionable list: a definition the statement depends on
    now has a different type or body. ``added``/``removed`` follow from it (a
    weakened definition usually stops mentioning what it used to) and are
    reported for completeness, not as separate defects.
    """
    return {
        "changed": sorted(n for n in bound.keys() & observed.keys()
                          if bound[n] != observed[n]),
        "added": sorted(observed.keys() - bound.keys()),
        "removed": sorted(bound.keys() - observed.keys()),
    }


def default_checker_cmd(project_root: str, target_decl: str) -> str:
    from calx import get_shared_data_dir
    script = get_shared_data_dir("tools") / "lean_check.sh"
    return f'"{script}" "{project_root}" "{target_decl}"'


def comparator_checker_cmd(project_root: str, config: str) -> str:
    """Checker command for the comparator kind (tools/comparator_check.sh).

    ``config`` is the comparator challenge JSON, relative to ``project_root``
    (e.g. ``ComparatorChallenges/NavierStokes.json``).
    """
    from calx import get_shared_data_dir
    script = get_shared_data_dir("tools") / "comparator_check.sh"
    return f'"{script}" "{project_root}" "{config}"'
