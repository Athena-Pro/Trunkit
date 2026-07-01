"""
lithon_core.py — vendored p-Sack valuation core for the kan lithon engine.
==========================================================================
The adelic prime-power lattice underlying kan.lithon (step 67). A 16x16
boolean grid where:
  - rows  r in [0,15]  correspond to BASES[r]  (base-1 = F_1 unary bridge)
  - cols  c in [0,15]  correspond to exponents (c+1)
  - atom  (r,c) has value BASES[r] ** (c+1)
  - global value Phi(state) = sum of all active atom values

This is the compute substrate for lithon's two functors:
  val  = phi                (grid -> integer)
  pack = state_from_integer (integer -> grid; greedy section of val)

Vendored verbatim (minus an unused import) from the standalone p-Sack
research module (core/p_sack.py) so build_lithon.py rebuilds without an
external checkout. Pure stdlib; no dependencies.
"""

from __future__ import annotations

from typing import Generator

# The 16 bases: base-1 (unary bridge) + first 15 primes
BASES: list[int] = [1, 2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47]

ROWS = 16
COLS = 16
TOTAL_CELLS = ROWS * COLS  # 256 bits

# Precompute the full atom table as a 2-D list of Python ints (arbitrary precision)
ATOM_TABLE: list[list[int]] = [
    [BASES[r] ** (c + 1) for c in range(COLS)]
    for r in range(ROWS)
]

# Flat sorted list of (value, row, col) for quick lookup
ATOMS_SORTED: list[tuple[int, int, int]] = sorted(
    ((ATOM_TABLE[r][c], r, c) for r in range(ROWS) for c in range(COLS)),
    reverse=True,
)

# Maximum reachable integer (all bits set)
MAX_VALUE: int = sum(ATOM_TABLE[r][c] for r in range(ROWS) for c in range(COLS))

# ──────────────────────────────────────────────────────────────────────────────
# State representation
# ──────────────────────────────────────────────────────────────────────────────

# A State is a frozenset of (row, col) pairs for active cells.
State = frozenset[tuple[int, int]]


def empty_state() -> State:
    """Return the all-zero state (energy = 0)."""
    return frozenset()


def phi(state: State) -> int:
    """Global valuation map: Phi(state) = Sum atom(r,c) for active (r,c)."""
    return sum(ATOM_TABLE[r][c] for r, c in state)


def atom_value(r: int, c: int) -> int:
    """Value of the atom at row r, column c."""
    return ATOM_TABLE[r][c]


def active_atoms(state: State) -> list[tuple[int, int, int]]:
    """Return list of (value, row, col) for all active cells, sorted descending."""
    return sorted(
        ((ATOM_TABLE[r][c], r, c) for r, c in state),
        reverse=True,
    )


def available_atoms(state: State) -> list[tuple[int, int, int]]:
    """Return list of (value, row, col) for all inactive cells."""
    return [
        (ATOM_TABLE[r][c], r, c)
        for r in range(ROWS)
        for c in range(COLS)
        if (r, c) not in state
    ]


# ──────────────────────────────────────────────────────────────────────────────
# Encoding helpers
# ──────────────────────────────────────────────────────────────────────────────

def state_from_grid(grid: list[list[bool]]) -> State:
    """Convert a 16x16 bool grid to a State."""
    return frozenset(
        (r, c)
        for r in range(ROWS)
        for c in range(COLS)
        if grid[r][c]
    )


def state_to_grid(state: State) -> list[list[bool]]:
    """Convert a State back to a 16x16 bool grid."""
    return [
        [((r, c) in state) for c in range(COLS)]
        for r in range(ROWS)
    ]


def state_from_integer(n: int) -> State | None:
    """
    Greedy canonical encoding: find one State with Phi(state) = n using
    a descending-value greedy subset-sum.  Returns None if n is not reachable.
    """
    if n < 0 or n > MAX_VALUE:
        return None
    remaining = n
    chosen: set[tuple[int, int]] = set()
    for val, r, c in ATOMS_SORTED:
        if val <= remaining:
            chosen.add((r, c))
            remaining -= val
            if remaining == 0:
                break
    return frozenset(chosen) if remaining == 0 else None


def describe(state: State) -> str:
    """Human-readable sum-of-prime-powers string."""
    parts = [f"{BASES[r]}^{c+1}" for val, r, c in active_atoms(state)]
    return " + ".join(parts) if parts else "0"


# ──────────────────────────────────────────────────────────────────────────────
# Orbit enumeration (small N only)
# ──────────────────────────────────────────────────────────────────────────────

def orbit(n: int, max_states: int = 10_000) -> Generator[State, None, None]:
    """
    Enumerate all States with Phi(state) = n via DFS subset-sum.
    Yields up to max_states results; intended for small n only.
    """
    atoms = [(val, r, c) for val, r, c in ATOMS_SORTED if val <= n]
    # Precompute suffix sums for pruning (avoids O(n) slice+sum at every node)
    suffix = [0] * (len(atoms) + 1)
    for i in range(len(atoms) - 1, -1, -1):
        suffix[i] = suffix[i + 1] + atoms[i][0]
    count = 0

    def dfs(index: int, remaining: int, current: list[tuple[int, int]]):
        nonlocal count
        if count >= max_states:
            return
        if remaining == 0:
            yield frozenset(current)
            count += 1
            return
        if index >= len(atoms):
            return
        # Suffix-sum pruning (O(1) lookup)
        if suffix[index] < remaining:
            return
        val, r, c = atoms[index]
        # Include this atom only if it doesn't exceed remaining
        if val <= remaining:
            current.append((r, c))
            yield from dfs(index + 1, remaining - val, current)
            current.pop()
        # Skip this atom
        yield from dfs(index + 1, remaining, current)

    yield from dfs(0, n, [])


def orbit_size(n: int, max_states: int = 10_000) -> int:
    """Count representations of n (capped at max_states)."""
    return sum(1 for _ in orbit(n, max_states))
