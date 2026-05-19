"""
nerode.automata — JSON automaton import / export and pretty-printing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import psycopg

# ---------------------------------------------------------------------------
# JSON schema (canonical format)
# ---------------------------------------------------------------------------
# {
#   "type": "DFA",                      # DFA | NFA | NFA_E
#   "name": "optional name",
#   "alphabet": ["a", "b"],
#   "states": [
#     {"id": 0, "is_initial": true, "is_accepting": false, "label": "q0"}
#   ],
#   "transitions": [
#     {"from": 0, "symbol": "a", "to": 1}   # symbol null = ε
#   ]
# }
# ---------------------------------------------------------------------------


def load_json(path: str | Path) -> dict[str, Any]:
    """Load an automaton definition from a JSON file."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def import_to_db(conn: psycopg.Connection, data: dict[str, Any]) -> int:
    """
    Import an automaton from the canonical JSON format into the database.
    Returns the new automaton id.
    """
    auto_type = data.get("type", "DFA").upper()
    auto_name = data.get("name")
    alphabet   = sorted(data["alphabet"])
    states     = data["states"]
    transitions = data["transitions"]

    alph_name = "auto_" + __import__("hashlib").md5(
        "".join(alphabet).encode()
    ).hexdigest()[:12]

    with conn.transaction():
        # Upsert alphabet
        conn.execute(
            """
            INSERT INTO nerode.alphabets (name, symbols)
            VALUES (%s, %s)
            ON CONFLICT (name) DO NOTHING
            """,
            (alph_name, alphabet),
        )
        alph_id: int = conn.execute(
            "SELECT id FROM nerode.alphabets WHERE name = %s", (alph_name,)
        ).fetchone()[0]

        # Create automaton record
        auto_id: int = conn.execute(
            """
            INSERT INTO nerode.automata (name, type, alphabet_id, state_count)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (auto_name, auto_type, alph_id, len(states)),
        ).fetchone()[0]

        # Insert states
        for s in states:
            conn.execute(
                """
                INSERT INTO nerode.states
                    (automaton_id, state_id, label, is_initial, is_accepting)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    auto_id,
                    s["id"],
                    s.get("label"),
                    s.get("is_initial", False),
                    s.get("is_accepting", False),
                ),
            )

        # Insert transitions
        for t in transitions:
            conn.execute(
                """
                INSERT INTO nerode.transitions
                    (automaton_id, from_state, symbol, to_state)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (auto_id, t["from"], t.get("symbol"), t["to"]),
            )

    return auto_id


def export_from_db(conn: psycopg.Connection, automaton_id: int) -> dict[str, Any]:
    """
    Export an automaton from the database to the canonical JSON format.
    """
    row = conn.execute(
        """
        SELECT au.name, au.type, al.symbols
        FROM nerode.automata au
        JOIN nerode.alphabets al ON al.id = au.alphabet_id
        WHERE au.id = %s
        """,
        (automaton_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Automaton {automaton_id} not found.")

    name, auto_type, symbols = row

    states = conn.execute(
        """
        SELECT state_id, label, is_initial, is_accepting
        FROM nerode.states WHERE automaton_id = %s ORDER BY state_id
        """,
        (automaton_id,),
    ).fetchall()

    transitions = conn.execute(
        """
        SELECT from_state, symbol, to_state
        FROM nerode.transitions WHERE automaton_id = %s
        ORDER BY from_state, symbol NULLS LAST, to_state
        """,
        (automaton_id,),
    ).fetchall()

    return {
        "type": auto_type,
        "name": name,
        "alphabet": sorted(symbols),
        "states": [
            {
                "id": s[0],
                "label": s[1],
                "is_initial": s[2],
                "is_accepting": s[3],
            }
            for s in states
        ],
        "transitions": [
            {"from": t[0], "symbol": t[1], "to": t[2]}
            for t in transitions
        ],
    }


def print_transition_table(data: dict[str, Any]) -> None:
    """
    Pretty-print a transition table for a DFA to stdout.

    Example:
        State  | a  | b  | Accepting
        -------|----|----|----------
        →  q0  | q1 | q0 |
           q1  | q1 | q2 | ✓
           q2  | q2 | q2 |
    """
    states = {s["id"]: s for s in data["states"]}
    alphabet = sorted(data["alphabet"])

    # Build transition lookup: (from, symbol) → to
    trans: dict[tuple[int, str | None], int] = {}
    for t in data["transitions"]:
        trans[(t["from"], t.get("symbol"))] = t["to"]

    col_w = max(4, *(len(str(sid)) for sid in states))
    sym_w = max(3, *(len(s) for s in alphabet))
    header_parts = (
        [f"{'State':^{col_w + 3}}"]
        + [f"{s:^{sym_w}}" for s in alphabet]
        + ["Accept"]
    )
    header = " | ".join(header_parts)
    sep    = "-+-".join(["-" * (col_w + 3)] + ["-" * sym_w] * len(alphabet) + ["------"])

    print(header)
    print(sep)

    for sid, sinfo in sorted(states.items()):
        prefix = "→" if sinfo["is_initial"] else " "
        acc    = "✓" if sinfo["is_accepting"] else ""
        row_parts = (
            [f"{prefix} q{sid:{col_w}}"]
            + [str(trans.get((sid, sym), "-")) for sym in alphabet]
            + [acc]
        )
        print(" | ".join(row_parts))
