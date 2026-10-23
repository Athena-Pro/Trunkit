# Exact DFA enumeration and finite-horizon optimum certificates

These certificates carry the automaton itself. Consumers need neither the
producer's database nor its search algorithm. All arithmetic in the portable
schemas is integral; floats and nondeterministic transitions are unsupported.
An invalid count, matrix identity, Bellman inequality, or attaining word is a
refuted certificate. Malformed or over-budget inputs are unverified.

## Immutable DFA snapshots

A snapshot declares its alphabet, distinct integer state identifiers, one
initial state, accepting states, and deterministic transitions. For example,
binary words with no adjacent ones have this partial DFA:

```json
{
  "alphabet": ["0", "1"],
  "states": [0, 1],
  "initial": 0,
  "accepting": [0, 1],
  "transitions": [[0, "0", 0], [0, "1", 1], [1, "0", 0]]
}
```

Missing transitions reject. The empty word has length zero and is accepted
exactly when the initial state is accepting. Different alphabet symbols taking
the same edge still describe different words.

`canonical_dfa` returns an owned copy with sorted states, alphabet, accepting
states, and transitions. `dfa_digest` hashes that semantic snapshot with
SHA-256 over canonical UTF-8 JSON (`sort_keys=True`, separators `,` and `:`;
Unicode is preserved). Input order and database identifiers external to the
snapshot do not affect the digest. This commits to a particular labelled DFA,
not its language up to state renaming or minimization.

## Counts and structurally proved recurrences

```python
from calx.automata_count import (
    check_automata_count, claim_subject_ref, export_count_witness,
)

witness = export_count_witness(dfa, 20, annihilator=[-1, -1, 1])
ok, evidence = check_automata_count(witness)
assert ok is True
assert witness["counts"][:8] == [1, 2, 3, 5, 8, 13, 21, 34]
subject_ref = claim_subject_ref(witness)
```

The `dfa_count.v1` witness contains `dfa`, `dfa_digest`, the integer transfer
`matrix`, and `counts` at every length from zero through the requested maximum.
In sorted state order, `matrix[i][j]` is the number of alphabet symbols taking
state `i` to state `j`. The checker reconstructs that matrix and every count.
It counts words across **all** alphabet symbols, not one repeated-symbol walk.

An optional `recurrence: {"polynomial": [-1, -1, 1]}` uses coefficients in
ascending power order. The checker requires the exact identity
`-I - A + A² = 0`. Therefore the corresponding count sequence satisfies
`-c(n) - c(n+1) + c(n+2) = 0` for every `n >= 0`, even beyond the carried
prefix. A recurrence that only fits the observed prefix is rejected. A scalar
recurrence may be true without annihilating the entire matrix; this deliberately
strong sufficient certificate does not accept all possible scalar recurrences.

The typed count subject is:

```json
{
  "witness_schema": "dfa_count.v1",
  "instance_digest": "<SHA-256 of the canonical DFA>",
  "predicate": {"name": "accepted_word_counts", "max_length": 20}
}
```

The kernel dispatcher, CLI/MCP, and portable bundle verifier use the same
checker. For a saved snapshot, the CLI entry point is:

```console
trunkit dfa count dfa.json --length 20 --annihilator "[-1,-1,1]" --bundle
```

## Bounded synchronous products

`product_dfa(left, right, op="intersection")` supports intersection and union
on equal alphabets. It constructs only reachable pairs and gives absent edges
implicit rejecting sink states, so union remains correct for partial DFAs.
The inputs are copied and preserved. `state_budget` and `transition_budget`
are enforced before adding a new state or edge. Exceeding a budget fails
explicitly; it never produces a truncated language.

The output can be passed to `export_count_witness`. A count certificate then
proves the output snapshot's counts. It does not, by itself, prove that the
snapshot was correctly derived as a product of two separately named inputs.

## Integer-weighted Bellman certificates

`dfa_extremal.v1` proves a best or worst payoff over **all accepted words of
length at most a finite horizon**, including the empty word. A weighted
snapshot contains a DFA and one integer weight for each `(source, symbol)`:

```python
from calx.automata_count import (
    check_automata_extremal, export_extremal_witness,
    extremal_claim_subject_ref,
)

weighted = {
    "dfa": dfa,
    "weights": [[0, "0", 0], [0, "1", 1], [1, "0", 0]],
    "initial_weight": 0,
    "final_weights": [[0, 0], [1, 0]],
}
witness = export_extremal_witness(weighted, horizon=4, sense="best")
assert witness["value"] == 2
assert check_automata_extremal(witness)[0] is True
subject_ref = extremal_claim_subject_ref(witness)
```

`initial_weight` defaults to zero. `final_weights` defaults to zero for every
accepting state; if supplied, it must cover exactly those states. A word's
payoff is initial weight plus all traversed edge weights plus its final weight.
Negative weights are supported.

The witness carries the canonical weighted `automaton`, its `instance_digest`,
`horizon`, `sense` (`best` or `worst`), integer `value`, Boolean `feasible`,
`labels`, and the attaining `history` of alphabet symbols. `labels[k][i]` is
an integer bound at length `k` and sorted state index `i`; null asserts
unreachability at that length.

The consumer checks the following finite proof, without rerunning optimization:

1. Layer zero is the initial weight at the unique initial state; other states
   have null labels.
2. For every layer and every edge from a non-null label, the successor label
   exists. For best payoff it is at least the predecessor label plus the edge
   weight; for worst payoff it is at most that sum.
3. The claimed value bounds every labelled accepting state at every layer,
   including its terminal weight.
4. Replaying `history` gives an accepting word within the horizon whose exact
   payoff equals the claimed value.

Steps 1–3 bound every admissible word; step 4 attains that bound. Neither a
good candidate alone nor an untrusted solver's assertion establishes an optimum.
If `feasible` is false, `value` must be null and `history` empty; the closed
reachability labels must contain no accepting state at any layer. This proves
infeasibility only within the specified horizon.

The typed predicate is
`{"name":"finite_horizon_extremum","horizon":4,"sense":"best","value":2,"feasible":true}`,
bound to the weighted snapshot digest. The CLI accepts the weighted snapshot:

```console
trunkit dfa extremal weighted.json --horizon 4 --sense best --bundle
```

## PostgreSQL entry points and quantitative payoff limitations

`nerode.dfa_snapshot(id)` exports a DFA in the portable snapshot shape.
`nerode.count_language(id, max_length, state_budget, transition_budget)` returns
`(length, word_count)` using exact `NUMERIC` arithmetic over all alphabet symbols.
It rejects ambiguous transitions and malformed alphabets. Its defaults are 256
states and 16,384 transitions. `nerode.run_sequence` retains its separate
repeated-symbol semantics.

The existing EHA/WFFA functions operate on numeric data values and payoff ASTs;
they are distinct from the integer-weighted portable Bellman schema above.
`nerode.payoff_opt` currently certifies only affine expressions composed from
`const`, `bind`, `otimes`, and interval guards. Nested maxima (`oplus`) are
explicitly unsupported: the previous breakpoint collector missed interior
crossovers. An objective unbounded on its admissible interval is flagged rather
than reported as a finite optimum. `nerode.extremal` propagates unsupported
results, checks deterministic inputs, and distinguishes them from an empty
accepted language within a finite horizon.

A WFFA `payoff_trace` establishes a realised value, not optimality or an empty
language. `nerode.certify_extremal` therefore creates an **unverified** certificate
until an independent bound proof is available. `nerode.certify_payoff_attainment`
and `nerode.replay_payoff_trace` verify explicit attainment scope; the unified
`nerode.verify` also binds the automaton, value, and horizon to the claim.
These SQL traces are not automatically converted into portable integer Bellman
certificates, and the integer Bellman checker does not establish general
continuous or unbounded-horizon optimization.

## Resource limits

Portable inputs support at most 256 states, 16,384 transitions, 16,384 alphabet
symbols (at most 1,024 characters each), and a count length/horizon of 4,096.
Enumeration and Bellman work is limited to 8,000,000 edge-layer steps. Integer
inputs, counts, and carried labels are limited to 8,192 bits; producers fail
explicitly when generated outputs exceed that budget. Entire portable witnesses
also have a 16,000,000-byte compact JSON budget and a 16,000,000-bit aggregate
integer budget. Bellman producers conservatively preflight the entire label
table and history before allocating them; small labels can still support 256
states through horizon 4,096. Checkers scan aggregate sizes before arithmetic
and avoid serializing a second full copy of the proof. Matrix annihilators have
degree at most 128 and a conservative dense-work limit of 8,000,000 operations.
Products permit smaller caller-selected state/transition budgets within these
hard limits. SQL counting uses the same state, edge, horizon, and edge-layer
limits; its numeric storage is governed by PostgreSQL rather than the portable
integer bit limit.

Tests compare counts and both senses of weighted optima with exhaustive word
enumeration, including partial DFAs, empty alphabets, negative weights, empty
words, forged infeasibility, tampered Bellman labels, and oversized inputs.
