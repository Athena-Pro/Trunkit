# Portable combinatorial certificates

Trunkit checks finite constructions with standard-library Python and PostgreSQL.
Solvers may produce candidates; their output becomes evidence only after the
independent checker accepts it. The core has no solver or symbolic-engine dependency.

## Start with a complete proof

```sh
trunkit comb demo --output c5.json
trunkit verify --bundle c5.json --offline
trunkit comb check c5.json
```

The bundle proves that the five-cycle has chromatic number three. It carries a
proper three-colouring and an odd-cycle lower-bound certificate. Neither database
access nor a search engine is needed to check it.

```python
from calx.combinatorial import c5_chromatic_example, check_combinatorial

witness = c5_chromatic_example()
ok, evidence = check_combinatorial(witness)
assert ok is True
```

## The contract

A witness has `schema: "comb.v1"`, an immutable normalized `instance`, its
`instance_digest`, a structured `predicate`, and a `certificate`. The digest is
SHA-256 of a domain-separated canonical JSON representation. Instance labels and
all fields that affect the property must travel inside the normalized instance.

The matching claim's `subject_ref` contains `witness_schema`, `instance_digest`,
and the same `predicate`. `claim_subject_ref(witness)` constructs it. Bundle
verification checks this agreement in addition to the witness kernel, lifecycle,
claim-content commitment, and transitive certificate dependencies. Descriptive
statement text cannot substitute for a typed proposition.

Old bundles without a typed witness proposition remain readable but their
carried witnesses cannot establish arbitrary claim text. Such attribution is
`unverified`; explicitly migrate/reissue the claim. A generic proof-object claim
can use `calx.proposition.witness_subject_ref(body)` to assert the validity of that
particular proof object, rather than a separate mathematical proposition.

## Supported finite predicates

| Predicate | Carried evidence |
|---|---|
| `wellformed`, `uniform` (`k`), `triangle_free` | Complete incidence structure |
| `primitive_family`, `intersecting_family` | Complete set family, including empty members |
| `t_intersecting`, `codegree`, `codegree_at_most`, `t_design` | Exact intersection and incidence counts over complete finite families |
| `primitive_set` | Exact positive integer values |
| `colouring` (`k`) | `colouring`: one colour per vertex |
| `independent_set`, `clique` (`k`) | `vertices`: supplied subset |
| `odd_cycle` | `vertices`: simple odd cycle in traversal order |
| `matching` (`k`) | `matching`: disjoint graph edges |
| `vertex_cover`, `hitting_set` (`k`) | `vertices`: covering subset |
| `isomorphism` (`target_digest`) | `target` instance and a vertex `mapping` |
| `chromatic_exact` (`k`) | Proper colouring plus clique, odd-cycle, or checked propositional exclusion |
| `vertex_cover_exact`, `matching_exact` (`k`) | Feasible matching plus an independently checked matching/cover bound |
| `unit_distance` (`faithful`) | Graph and exact configuration, with injective real placement |

Invalid candidates for existential statements leave those statements
`unverified`. A failed complete property scan can refute that property. Unknown
schemas, unsupported domains, malformed input, and exceeded work budgets remain
`unverified`. These distinctions apply equally to CLI, MCP, and Python consumers.

## Import, produce, transform, export

```sh
trunkit comb import graph.json --output normalized.json
trunkit comb search graph.json --colours 3 --max-steps 100000
trunkit comb export graph.json --predicate '{"name":"triangle_free"}' --bundle --output proof.json
trunkit comb transform colouring.json --operation relabel --mapping '[4,3,2,1,0]'
trunkit comb transform colouring.json --operation disjoint_union --other second.json
```

The graph format is `{"kind":"graph","ground_n":5,"blocks":[[0,1],...]}`.
Block objects with explicit indices are also supported. Transform operations
carry their premises and maps; the verifier checks both the premises and the
resulting construction. They do not trust a free-text inference-rule name.

Database import requires `--write --subject NAME` and the intended `--dsn`.
An existing subject naming a different structure is refused. Search is an
optional bounded producer; unsuccessful search never generates an impossibility
claim. `search --write` records effort through `cert.record_attempt`, separately
from truth-bearing certificates.

## Negative certificates and exact bounds

`graph_colouring_cnf(instance, k)` deterministically generates the exact formula
for k-colourability. `not_k_colourable` certificates carry a proof in the
addition-only `rup.v1` format: each added clause must follow by reverse unit
propagation, and the proof must derive the empty clause. The checker regenerates
the encoding and checks its digest; it does not trust a supplied solver formula.
The external-artifact adapter imports addition-only ASCII DRUP files:

```sh
trunkit comb exclude graph.json --colours 2 --proof solver.drup --bundle --output exclusion.json
trunkit verify --bundle exclusion.json --offline
```

`calx.sat_adapter.parse_drup` also exposes the bounded parser directly. The
adapter verifies the proof before exporting a certificate. Deletions, RAT steps,
unsupported formats, and incomplete proofs are refused. Solver commands embedded
in a witness are never executed.

`chromatic_exact` combines a proper k-colouring with exclusion of k−1 colours
for the identical graph. Matching and cover dual witnesses provide a smaller
exact-optimization workflow without a SAT solver.

SQL120 adds typed objective/attainment functions for deposited constructions.
These bind feasibility, a measured objective, and quantity parameters. Exact
optimality needs a dedicated typed formal-bound claim with a compatible verified
opposite bound. Existing unrelated claims cannot be relabeled as bound proofs;
the universal theorem remains an explicit external formal-verification dependency.
Portable `chromatic_exact`, `matching_exact`, and `vertex_cover_exact` instead
check their complete supported certificates locally. The legacy
`cert.bound_optimal` / `calx.bound.optimal` functions now report unknown for
frontier/standing metadata alone; best-known status is not a mathematical proof.

## Geometry and automata

Portable Euclidean certificates currently support rationals and validated real
quadratic extensions. Wider coordinate vectors, coincident vertices, non-real
fields, and unsupported extensions cannot certify a real realization. Arbitrary
algebraic identities are not silently treated as Euclidean placements.

See [Automata certificates](AUTOMATA_CERTIFICATES.md) for exact all-symbol counts,
matrix-identity recurrence certificates, bounded synchronous products, and
finite-horizon Bellman certificates for integer-weighted DFAs.

The MCP server exposes `comb_import`, `comb_export`, `comb_transform`,
`comb_search`, `comb_exclude`, `automata_count`, `automata_product`, and `automata_extremal`.
`kernel_verify` and `bundle_verify` check the new schemas through the same
verification pipeline as the CLI. All these local computation tools work without
database access; the existing write-enabled certificate tools retain their
`TRUNKIT_ALLOW_WRITE` gate.
