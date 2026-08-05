"""Digest recipes for statement bindings (106) and goal hashing (107).

One module, so that every producer of a digest -- the CLI, a harness, a test
-- computes the same bytes. The ledger stores ``digest_kind`` alongside every
digest precisely because these recipes are not interchangeable, and a
syntactic digest must never silently compare equal to an elaborated one.

WHAT THE SYNTACTIC RECIPE IS FOR. It answers "is this the same text, modulo
formatting" and nothing more. That is enough for the job it is given:
detecting that a theorem statement changed under a proof (AlphaProof Nexus's
sandbox check, arXiv:2605.22763) and detecting that a `sorry`'d gap is the
target verbatim. It is deliberately NOT a decision procedure for goal
identity:

  * equal digests  => the same normalised text, so the obligations are the
    same. Sound.
  * unequal digests => nothing. Two goals that differ by definitional
    unfolding, implicit-argument elaboration, or binder names will hash apart
    while being the same obligation. Incomplete.

Every consumer must be written for that asymmetry: a cache hit is reusable, a
cache miss means "try", a circularity hit is a defect, a circularity miss is
not a clean bill of health. The `elaborated` kind is reserved for a digest
taken over Lean's own pretty-printed elaborated goal state, which narrows the
incompleteness without removing it; nothing here produces one yet.
"""

from __future__ import annotations

import hashlib
import re

__all__ = [
    "normalize",
    "statement_digest",
    "goal_digest",
    "extract_declaration",
    "find_sorries",
]

# Lean comment forms. Block comments nest in Lean 4, which a regex cannot
# express, so nested blocks are handled by the scanner in _strip_comments.
_LINE_COMMENT = re.compile(r"--[^\n]*")


def _strip_comments(src: str) -> str:
    """Remove `--` line comments and (nesting) `/- ... -/` block comments.

    Comments carry no logical content, and leaving them in would make a
    re-worded docstring look like statement drift -- the false positive most
    likely to make someone switch the check off.
    """
    out: list[str] = []
    i, depth, n = 0, 0, len(src)
    while i < n:
        if src.startswith("/-", i):
            depth += 1
            i += 2
        elif src.startswith("-/", i) and depth:
            depth -= 1
            i += 2
        elif depth:
            i += 1
        else:
            out.append(src[i])
            i += 1
    return _LINE_COMMENT.sub("", "".join(out))


def normalize(text: str) -> str:
    """Canonical form: comments stripped, whitespace collapsed, ends trimmed.

    Whitespace is collapsed rather than removed. Removing it entirely would
    merge tokens (`h x` and `hx` are different identifiers), which would make
    the digest unsound in the one direction it is supposed to be sound.
    """
    return " ".join(_strip_comments(text).split())


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def statement_digest(statement: str) -> str:
    """Digest of a declaration's statement text (the part before `:=`)."""
    return _sha256(normalize(statement))


def goal_digest(target: str, context: str = "") -> str:
    """Digest of a proof obligation: hypothesis context plus target.

    The two parts are joined with a separator that cannot occur in normalised
    Lean source, so that no pair of (context, target) can collide with a
    different pair by re-partitioning the same characters.
    """
    return _sha256(f"{normalize(context)}\n␞\n{normalize(target)}")


# `theorem foo ... : T := proof`  /  `lemma foo ... : T := proof`
# Also matches `example`-free forms with `where`/`by` bodies. The statement is
# everything between the declaration name and the top-level `:=`.
_DECL_START = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)*(?:private\s+|protected\s+|noncomputable\s+)*"
    r"(theorem|lemma|def|abbrev)\s+(?P<name>[^\s:({\[]+)",
    re.MULTILINE,
)


def extract_declaration(source: str, decl_name: str) -> tuple[str, str] | None:
    """Return ``(signature, body)`` for ``decl_name`` in Lean ``source``.

    The signature is the text from the declaration keyword up to the top-level
    ``:=`` -- name, binders, and the stated type. That is what a statement
    binding is over: the proof may change freely, the statement may not.

    ``decl_name`` is matched against the trailing component too, so both
    ``Erdos125.target_theorem_0`` and ``target_theorem_0`` find a declaration
    written inside ``namespace Erdos125``.

    Returns ``None`` if no such declaration is found. Bracket-aware rather
    than regex-terminated: a ``:=`` inside a structure instance or a
    ``let`` in a binder default must not end the signature.
    """
    stripped = _strip_comments(source)
    short = decl_name.rsplit(".", 1)[-1]

    for m in _DECL_START.finditer(stripped):
        found = m.group("name")
        # Compare short-to-short so a query works from either side of the
        # namespace: `Erdos125.target_theorem_0` finds a declaration written
        # bare inside `namespace Erdos125`, and vice versa.
        if found != decl_name and found.rsplit(".", 1)[-1] != short:
            continue
        i, n = m.end(), len(stripped)
        depth = 0
        while i < n:
            ch = stripped[i]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif depth == 0 and stripped.startswith(":=", i):
                return stripped[m.start():i].strip(), stripped[i + 2:].strip()
            i += 1
        # No top-level `:=`: a declaration stated but not given a body.
        return stripped[m.start():].strip(), ""
    return None


_SORRY = re.compile(r"\bsorry\b")


def find_sorries(source: str) -> list[tuple[int, str]]:
    """Locate `sorry` occurrences as ``(line_number, line_text)``.

    Comment-stripped first, so a `sorry` discussed in prose is not reported as
    a gap. Line numbers are 1-based and refer to the STRIPPED text, which is
    good enough for the ledger's purpose -- identifying that gaps exist and how
    many -- and is not offered as an editor coordinate.
    """
    stripped = _strip_comments(source)
    return [
        (idx, line.strip())
        for idx, line in enumerate(stripped.splitlines(), start=1)
        if _SORRY.search(line)
    ]
