# Trunkit verification methods

Universal, proof-carrying verification methods for the Trunkit ledger. Each is **verify-easy /
find-hard**: a tiny deterministic kernel re-checks a compact, portable witness and returns one of
three honest verdicts — `valid` / `refuted` / `unverified` (never a guess).

The kernels live in the package at [`src/calx/methods/`](src/calx/methods/) and are registered
with `calx.kernel.verify_witness`, so every dispatch surface picks them up automatically:
`trunkit verify --bundle`, the consumer bundle checker, and the trunkit-mcp `kernel_verify`
tool. Their verdict batteries run in the test suite (`tests/test_methods.py`).

| Method | Kernel schema | Verifies | Witness (kind) | Refutation certificate |
|--------|---------------|----------|----------------|------------------------|
| [arith_check](docs/methods/arith_check_spec.md) | `arith_check` | a numeric claim, by exact recomputation | typed expression AST (`term`) | exact mismatch / dimensional mismatch / interval separation |
| [quote_carry](docs/methods/quote_carry_spec.md) | `quote_carry` | a citation, by span + content hash | doc hash + span + quote hash (`quote_span`) | quote absent or at a different offset; version mismatch |
| [csp_carry](docs/methods/csp_carry_spec.md) | `csp_carry` | a solution to a constraint problem | the assignment map (`term`) | the specific violated constraint, named |
| [puzzle_parity](docs/methods/parity_results.md) | `puzzle_parity` | sliding-puzzle solvability | move sequence, or none (`trace`) | the parity invariant I = −1 |

`puzzle_parity` is the sequential special case of `csp_carry` (witness = move list). `quote_carry`
generalizes `empirical_corpus` and carries the new `quote_span` witness kind (registered in the
canonical vocabulary, `84_cert_witness.sql`). Together they cover the recompute / ground /
satisfy / invariant corners of one verify-easy pattern.

Every kernel is stdlib-only, keeping the `calx.kernel` charter: a consumer who receives an
exported bundle re-verifies these witnesses with nothing but a Python interpreter.
