"""puzzle_parity — sliding-puzzle solvability as a PERMUTATION-PARITY invariant.

The whole puzzle reduces to one number:

    I(state) = sign(perm of tiles vs goal) * (-1)^(Manhattan dist of blank from its goal cell)

Every legal slide is a transposition (flips the sign) that also moves the
blank one step (flips the (-1)^dist factor), so I is INVARIANT under legal
play. The goal has I = +1, therefore: state is solvable  <=>  I(state) == +1.

Three verdicts, cleanly:
  valid       a witness (move sequence) is supplied and re-plays to the goal
  refuted     I(state) == -1: NO witness can exist — the parity computation IS
              the refutation certificate (also: a witness that fails to reach
              the goal, or makes an illegal move)
  unverified  I(state) == +1 (solvable) but no witness attached yet

This is the sequential special case of csp_carry (witness = move list).

Kernel-dispatch witness (calx.kernel schema "puzzle_parity"):
  {"schema": "puzzle_parity", "rows": R, "cols": C,
   "state": [tile, ...],          # row-major, 0 = blank
   "moves": "UDLR..." | ["U", ...]?}   # optional; absent -> parity-only check
Spec / battery results: docs/methods/parity_results.md.
"""

from __future__ import annotations

import random
from collections import deque
from typing import Any


def perm_sign(perm) -> int:
    n = len(perm)
    seen = [False] * n
    sign = 1
    for i in range(n):
        if not seen[i]:
            j, cycle_len = i, 0
            while not seen[j]:
                seen[j] = True
                j = perm[j]
                cycle_len += 1
            if cycle_len % 2 == 0:
                sign = -sign
    return sign


class Puzzle:
    def __init__(self, rows: int, cols: int):
        self.R, self.C, self.N = rows, cols, rows * cols
        self.goal = tuple(list(range(1, self.N)) + [0])

    def feature(self, st) -> int:
        pos = {v: i for i, v in enumerate(self.goal)}
        sg = perm_sign([pos[v] for v in st])
        bi, gi = st.index(0), self.goal.index(0)
        dist = abs(bi // self.C - gi // self.C) + abs(bi % self.C - gi % self.C)
        return sg * (1 if dist % 2 == 0 else -1)

    def solvable(self, st) -> bool:
        return self.feature(st) == 1

    def _neighbors(self, bi: int):
        r, c = divmod(bi, self.C)
        out = []
        for dr, dc, mv in ((-1, 0, "U"), (1, 0, "D"), (0, -1, "L"), (0, 1, "R")):
            nr, nc = r + dr, c + dc
            if 0 <= nr < self.R and 0 <= nc < self.C:
                out.append((nr * self.C + nc, mv))
        return out

    def apply_move(self, st, mv: str):
        bi = st.index(0)
        r, c = divmod(bi, self.C)
        dr, dc = {"U": (-1, 0), "D": (1, 0), "L": (0, -1), "R": (0, 1)}[mv]
        nr, nc = r + dr, c + dc
        if not (0 <= nr < self.R and 0 <= nc < self.C):
            return None
        ni = nr * self.C + nc
        lst = list(st)
        lst[bi], lst[ni] = lst[ni], lst[bi]
        return tuple(lst)

    def kernel_verify(self, st, moves) -> tuple[str, str]:
        """The kernel: re-play a witness, return a three-valued verdict."""
        cur = st
        for k, mv in enumerate(moves):
            if mv not in "UDLR":
                return ("refuted", f"illegal move #{k + 1} '{mv}'")
            cur = self.apply_move(cur, mv)
            if cur is None:
                return ("refuted", f"illegal move #{k + 1} '{mv}'")
        if cur == self.goal:
            return ("valid", f"reaches goal in {len(moves)} moves")
        return ("refuted", "witness does not reach goal")

    def claim_check(self, st, witness=None) -> tuple[str, str]:
        """Verdict for the claim 'st is solvable'."""
        if witness is not None:
            return self.kernel_verify(st, witness)
        if not self.solvable(st):
            return ("refuted",
                    f"parity invariant I={self.feature(st)} (must be +1) — "
                    "unsolvable, no witness exists")
        return ("unverified", "solvable by parity, but no witness attached")

    def solve(self, st):
        """Produce a witness (BFS — intended for small boards like 3x3)."""
        if st == self.goal:
            return []
        seen = {st}
        q = deque([(st, [])])
        while q:
            cur, path = q.popleft()
            for ni, mv in self._neighbors(cur.index(0)):
                lst = list(cur)
                bi = cur.index(0)
                lst[bi], lst[ni] = lst[ni], lst[bi]
                nx = tuple(lst)
                if nx in seen:
                    continue
                if nx == self.goal:
                    return path + [mv]
                seen.add(nx)
                q.append((nx, path + [mv]))
        return None

    def scramble(self, k: int, seed: int = 0):
        rng = random.Random(seed)
        st = self.goal
        for _ in range(k):
            opts = self._neighbors(st.index(0))
            ni, _mv = rng.choice(opts)
            bi = st.index(0)
            lst = list(st)
            lst[bi], lst[ni] = lst[ni], lst[bi]
            st = tuple(lst)
        return st


# ── calx.kernel adapter (schema "puzzle_parity") ────────────────────────────

def check_puzzle_parity(w: dict[str, Any]) -> tuple[bool | None, dict[str, Any]]:
    rows, cols, state = w.get("rows"), w.get("cols"), w.get("state")
    if not isinstance(rows, int) or not isinstance(cols, int) \
            or rows < 2 or cols < 2:
        return None, {"error": "witness needs integer rows, cols >= 2"}
    if not isinstance(state, list) or sorted(state) != list(range(rows * cols)):
        return None, {"error": f"state must be a permutation of 0..{rows * cols - 1}"}
    puzzle = Puzzle(rows, cols)
    moves = w.get("moves")
    if moves is not None and not isinstance(moves, (str, list)):
        return None, {"error": "moves must be a string or list of U/D/L/R"}
    status, detail = puzzle.claim_check(tuple(state), moves)
    ok = True if status == "valid" else False if status == "refuted" else None
    return ok, {"status": status, "detail": detail,
                "invariant": puzzle.feature(tuple(state))}
