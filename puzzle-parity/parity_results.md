# Sliding-puzzle solvability as a permutation-parity feature (Trunkit-shaped)

## The algebraic feature
The whole puzzle reduces to one number:

    I(state) = sign(tile permutation vs goal) · (−1)^(Manhattan distance of blank from its goal cell)

Every legal slide is a transposition (flips the sign) that also moves the blank one step
(flips the (−1)^dist factor), so **I is invariant under legal play**. The goal has I = +1, so:

    state is solvable  ⇔  I(state) = +1

No search required — solvability is read off in O(n).

## Proof the feature is exactly right
On the 8-puzzle (all 9! = 362,880 states): the BFS-reachable-from-goal set and the
invariant-positive set are **identical**, both 181,440 = 9!/2. The algebraic feature isn't a
heuristic — it *is* reachability. On the 15-puzzle the invariant held constant over 5,000
random legal moves (full BFS there is 16!/2 ≈ 10¹³, so the invariant is the only practical handle).

## Three verdicts, the Trunkit way
| Verdict | When | Certificate |
|---------|------|-------------|
| `valid` | a move-sequence witness is supplied and the kernel re-plays it to the goal | the replayed moves |
| `refuted` | I = −1 (no witness can exist) **or** a supplied witness misses the goal | the parity number, or the failed replay |
| `unverified` | I = +1 (solvable) but no witness attached yet | — |

Demonstrated: solvable scramble → `unverified` with no witness, `valid` once the 14-move
solution `UULDDRULLURDDR` is attached; the same instance with a truncated witness → `refuted`;
two swapped tiles → `refuted` purely from I = −1.

## Mapping to Trunkit
- `curry` pure function — `puzzle_check(state, goal, moves) -> bool` (this file's `kernel_verify`).
- `claim` — "state S is solvable", method `struct_kan` (the parity invariant gives the structural verdict).
- `witness` — the move sequence, method `witness_carry`; `witness_attach` carries it.
- `kernel` — `kernel_verify` re-plays the moves, mirroring `trunkit.kernel_verify`.

The notable property: the parity invariant is a **refutation certificate**. Most checkers can
only say "I couldn't find a solution"; here an unsolvable instance is *proved* unsolvable by a
single number the kernel re-derives in O(n) — exactly the kind of honest `refuted` Trunkit's
three-valued ledger wants.

## Registering it for real
Pushing this in as a live claim needs Trunkit write access (`TRUNKIT_ALLOW_WRITE=1`,
`claim_check`/`witness_attach`). This module is structured so that step is a thin adapter: the
pure check-function and the witness format are already in the shape those tools expect.

## Files
- `parity_puzzle.py` — feature, kernel verifier, solver, the 9! proof, and the verdict demos.
