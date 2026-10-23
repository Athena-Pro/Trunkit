"""Import external addition-only ASCII DRUP artifacts without running a solver.

The supported DRUP subset has one clause per line, ending in ``0``; blank
lines and ``c`` comment lines are allowed. A final empty clause is required.
Deletion instructions, format headers, LRAT hints, and non-RUP additions are
not accepted as evidence. Parsing checks syntax and fixed resource ceilings;
the certificate builder independently regenerates the colouring CNF and
checks every imported addition with the portable RUP kernel.
"""

from __future__ import annotations

import re
from typing import Any

from .combinatorial import (
    CNF_ENCODING,
    check_combinatorial,
    graph_colouring_cnf,
    make_witness,
)

MAX_BYTES = 8_000_000
MAX_STEPS = 100_000
MAX_LITERALS = 2_000_000
MAX_LITERAL = 2_147_483_647
_INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)\Z", flags=re.ASCII)

__all__ = ["parse_drup", "make_colouring_exclusion"]


def _limit(value: Any, ceiling: int, label: str, minimum: int = 1) -> int:
    if type(value) is not int or not minimum <= value <= ceiling:
        raise ValueError(f"{label} must be an integer in {minimum}..{ceiling}")
    return value


def parse_drup(
    text: str,
    *,
    max_bytes: int = MAX_BYTES,
    max_steps: int = MAX_STEPS,
    max_literals: int = MAX_LITERALS,
) -> dict[str, Any]:
    """Parse the bounded addition-only DRUP subset into ``rup.v1`` JSON.

    This is a syntactic import, not a proof verdict: the final empty clause
    must still be justified by a checker against the intended CNF. Budget
    overrides can only lower the fixed ceilings. Integers use canonical ASCII
    decimal syntax and signed 32-bit DIMACS variable indices.
    """
    max_bytes = _limit(max_bytes, MAX_BYTES, "max_bytes")
    max_steps = _limit(max_steps, MAX_STEPS, "max_steps")
    max_literals = _limit(max_literals, MAX_LITERALS, "max_literals", minimum=0)
    if not isinstance(text, str):
        raise ValueError("DRUP artifact must be text")
    if len(text) > max_bytes:
        raise ValueError("DRUP byte budget exceeded")
    if not text.isascii():
        raise ValueError("DRUP artifact must contain ASCII only")
    steps: list[list[int]] = []
    literal_count = 0
    complete = False
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("c"):
            continue
        if complete:
            raise ValueError(f"DRUP has records after its empty clause at line {line_number}")
        if line.startswith("d"):
            raise ValueError("DRUP deletion records are unsupported; supply addition-only DRUP")
        if line[0] not in "-0123456789":
            raise ValueError(f"unsupported proof format or instruction at line {line_number}")
        if len(steps) >= max_steps:
            raise ValueError("DRUP step budget exceeded")
        tokens = line.split()
        if tokens[-1] != "0":
            raise ValueError(f"DRUP clause lacks its 0 terminator at line {line_number}")
        clause = []
        for token in tokens[:-1]:
            if token == "0":
                raise ValueError(f"DRUP requires one terminated clause per line ({line_number})")
            if len(token.lstrip("-")) > 10 or _INTEGER.fullmatch(token) is None:
                raise ValueError(f"invalid DIMACS integer at line {line_number}")
            literal = int(token)
            if literal == 0 or abs(literal) > MAX_LITERAL:
                raise ValueError(
                    f"DIMACS literal outside the supported range at line {line_number}"
                )
            literal_count += 1
            if literal_count > max_literals:
                raise ValueError("DRUP literal budget exceeded")
            clause.append(literal)
        steps.append(clause)
        complete = not clause
    if not complete:
        raise ValueError("incomplete DRUP artifact: a final empty clause is required")
    return {"format": "rup.v1", "steps": steps}


def make_colouring_exclusion(instance: dict, k: int, proof_text: str) -> dict[str, Any]:
    """Return a checked ``comb.v1`` non-k-colourability witness.

    Imported artifacts cannot choose the CNF, encoding, or executable command.
    Unsupported, incomplete, over-budget, and rejected proofs raise ValueError;
    an unsuccessful import never asserts a mathematical negative result.
    """
    proof = parse_drup(proof_text)
    cnf = graph_colouring_cnf(instance, k)
    witness = make_witness(
        instance,
        {"name": "not_k_colourable", "k": k},
        {"encoding": CNF_ENCODING, "cnf_digest": cnf["cnf_digest"], "proof": proof},
    )
    ok, evidence = check_combinatorial(witness)
    if ok is not True:
        detail = evidence.get("error", evidence.get("detail", "proof was not verified"))
        raise ValueError(f"DRUP artifact does not establish the colouring exclusion: {detail}")
    return witness
