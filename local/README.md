# local/ — workspace extension overlay

This directory holds project-specific extensions to Trunkit that are not
part of the public library.  It is excluded from the `public` branch.

## Structure

```
local/
├── sql/       — project-specific SQL (seeds, claims, observability views)
│               Loaded by: trunkit init --local
│               Applied in filename order after the core 00–89 SQL.
├── tools/     — project-specific Python tools and SQL claim files
├── docs/      — project planning docs, session notes, STATUS files
├── tests/     — project-specific test vectors and test modules
└── benchmarks/— project-specific benchmark scripts and logs
```

## Usage

```bash
# Initialise the core schema only (suitable for a fresh public install)
trunkit init

# Initialise the core schema AND apply all local/sql/ extensions
trunkit init --local
```

## What lives here

| Path | Contents |
|------|----------|
| `sql/22_curry_calx_functor_seed.sql` | Curry → calx functor and wrapper seed |
| `sql/31_kan_corpus_seed.sql` | Kolomatskaia–Shulman corpus document row |
| `sql/32_kan_sequence_terms.sql` | A000040/45/90 sequence terms for chromatic convergence |
| `sql/50_curry_lib.sql` | Trusted function library (math_*, cert_*) |
| `sql/51_curry_lib_claims.sql` | Cert claims for the library |
| `sql/90–96_*.sql` | Project cert claims (Feigenbaum, TEL, kernel, ledger) |
| `tools/tel_*.py` | TEL capability checkers and visualisation renderers |
| `tools/build_*.py` | KAN engine data builders |
| `tools/gen_board.py` / `gen_status.py` | Board graphic and status generation |
| `docs/NEXT_SESSION.md` | Session planning |
| `docs/STATUS.md` / `STATUS_BOARD.html` | Living project status |

## Adding your own extensions

Drop SQL files into `local/sql/` (named `NN_description.sql` or
`NNN_description.sql`) and run `trunkit init --local` to apply them.
Files apply in **numeric prefix order** (`99_` before `100_`; letter
suffixes like `41a_` sort after their base number), not raw filename
order.  Tools go in `local/tools/` and are invoked directly with
`python local/tools/your_tool.py`.
