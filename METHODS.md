# Trunkit verification methods

Universal, proof-carrying verification methods for the Trunkit ledger. Each is **verify-easy /
find-hard**: a tiny deterministic kernel re-checks a compact, portable witness and returns one of
three honest verdicts — `valid` / `refuted` / `unverified` (never a guess). Each maps onto
`curry` (the pure check function) + `witness_carry` (the witness) + `kernel_verify` (the re-check).

| Method | Verifies | Witness | Refutation certificate |
|--------|----------|---------|------------------------|
| [arith_check](arith-check/arith_check_spec.md) | a numeric claim, by exact recomputation | typed expression AST (JSON) | exact mismatch / dimensional mismatch / interval separation |
| [quote_carry](quote-carry/quote_carry_spec.md) | a citation, by span + content hash | doc hash + span + quote hash | quote absent or at a different offset; version mismatch |
| [csp_carry](csp-carry/csp_carry_spec.md) | a solution to a constraint problem | the assignment map | the specific violated constraint, named |
| [puzzle-parity](puzzle-parity/parity_results.md) | sliding-puzzle solvability | move sequence (or none) | the parity invariant I = -1 |

`puzzle-parity` is the sequential special case of `csp_carry` (witness = move list). `quote_carry`
generalizes `empirical_corpus`. Together they cover the recompute / ground / satisfy / invariant
corners of one verify-easy pattern.

Each folder contains a runnable reference kernel (`*.py`, no external deps beyond the stdlib;
`puzzle-parity` and the rhyme tooling aside) with a `__main__` verdict battery, plus a spec.
