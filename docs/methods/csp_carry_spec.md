# csp_carry — method spec (Trunkit universal method)

Verify a **solution to a constraint problem**. The general verify-easy / find-hard frame:
finding an assignment is NP-hard across coloring, scheduling, SAT, Sudoku; checking one is
polynomial. The witness is the assignment; refutation names the violated constraint.

## Claim shape
    assignment A satisfies instance I = (variables+domains, constraints)

## Instance + witness
    instance = {"vars": {"x":[0,1,2], ...}, "constraints": [ {"type":...}, ... ]}
    witness  = {"x": 0, "y": 2, ...}            # the assignment

Constraint types (extensible): `all_different`, `not_equal`, `equal`,
`linear` (`Σ coef·var  rel  rhs`), `clause` (SAT, vars in {0,1}).

## Verdict semantics
| Verdict | When |
|---|---|
| `valid` | every variable in-domain and every constraint holds |
| `refuted` | a variable is out of domain, or a specific constraint is violated — the kernel returns **which one** as the certificate |
| `unverified` | the witness omits a variable, or uses a constraint type the kernel doesn't implement |

## Why it's the right altitude
The sliding-puzzle parity method is the **sequential cousin** (witness = move list, constraints =
legal-move + reaches-goal). `csp_carry` is the static-assignment generalization — one method that
absorbs a whole family of puzzles. Checking is O(#constraints); only finding is hard. That
asymmetry is exactly what Trunkit's witness model monetizes.

## Determinism / portability
The kernel is a constraint evaluator — no search, no randomness, identical verdict on any host.
The witness is a plain map; the instance is declarative data.

## Trunkit mapping
`curry` = `check_constraint(con, A)`; method = `comp_sql` / `csp_carry`; witness via
`witness_carry`; `kernel_verify(instance, A)` mirrors `trunkit.kernel_verify`.

## Demo results (`csp_carry.py`)
3-coloring valid / clash refuted (names the edge) · Latin square valid / duplicate refuted (names
the line) · SAT sat→valid, unsat-assignment→refuted (names the clause) · linear valid/refuted ·
out-of-domain → refuted · missing var / unknown constraint → unverified.
