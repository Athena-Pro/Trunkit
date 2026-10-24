# Nerode construction and minimization

After updating the Python package, apply the bundled SQL functions to each
existing Nerode database:

```bash
nerode --dsn "$NERODE_DSN" close --apply
```

`nerode.minimize(id)` uses Hopcroft worklist refinement with smaller-half
splitting. It removes unreachable states, handles missing transitions with an
implicit rejecting sink, and leaves the input automaton unchanged. Refinement
uses indexed arrays in PostgreSQL rather than SQL queries for each block and
symbol. Only the final minimal DFA receives a complete transition table.
Its state IDs are contiguous, starting with the initial state `q0`, in breadth
first order over the alphabet sorted using the `C` collation.

The `construction_log.result.partition` object still maps output state IDs to
equivalent original state IDs. It covers reachable original states. A class
containing only the implicit sink has an empty member array; `implicit_sink`
identifies the sink's output state ID, or is null when no sink was needed.
`reachable_states` and `unreachable_states` record the input-state counts.
`minimize_certified` carries these fields into its partition witness.

Flat literal alternatives such as `(cat|car|dog)` build a trie directly before
minimization. Literals support the existing `[a-zA-Z0-9_]` grammar, with optional
outer parentheses. Other regex expressions retain the Thompson/subset path.
Both paths preserve construction logs and certificates.

The CLI routes SIGINT and SIGTERM through psycopg's query cancellation before
rolling back and closing its connection. Exit codes are 130 and 143 respectively.
`min --write` performs one minimization and certifies that result.

Regression cases in `tests/test_minimize_regressions.py` run in ordinary database
CI, including a 10,914-state sparse trie and a 3,000-word flat regex. Each query
has a 120-second PostgreSQL statement timeout and wall-clock budget. Their
expected minimal size is computed independently from trie suffix signatures;
full product traversal checks language equivalence. Smaller random partial DFAs
are checked against an independent Moore refinement. These synthetic cases
exercise the reported workload scale; the original report's vocabulary is not
included in this repository.

Run the focused regressions against a dedicated scratch database:

```bash
NERODE_TEST_DSN="$SCRATCH_DSN" python -m pytest \
  tests/test_minimize_regressions.py tests/test_regex_trie.py \
  tests/test_nerode_cli_lifecycle.py
```
