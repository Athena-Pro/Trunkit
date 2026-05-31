# Demonstration corpus — paper → Trunkit capability map

Target set: `C:\Users\thegi\Downloads\Papers` (31 distinct papers; the Valero
partial-metric paper appears twice). Each row records the paper, the Trunkit
layer it exercises, and — where the paper contains a *concrete, re-checkable*
fact — the cert method tier that certifies it.

Method tiers: `comp_sql` (in-DB probe) · `struct_kan` (categorical invariant) ·
`cert_kernel` (untrusted certificate, step 94) · `empirical_corpus` (provenance
of a literature assertion) · `formal_external` (hash-pinned artifact).

Everything entered as a claim is bound into the hash-chained ledger (step 95):
`row_hash = SHA256(content ‖ inference_hash ‖ prev_hash ‖ premise_hashes)`.

---

## A. Directly demonstrate Trunkit's own machinery

| Paper | arXiv | Demonstrates | Tier |
|---|---|---|---|
| **When Agda met Vampire** | 2602.18844 | The cert_kernel thesis itself: an untrusted ATP emits a proof object the ITP re-checks (solve ≠ verify). Cite as the design ancestor of step 94. | `empirical_corpus` |
| **Open Horn Type Theory** | 2512.24498 | OCTT crown-consensus (`cert.crown_consensus`, SKILL.md): coherence/rupture adjudication, the `contested` state. | `empirical_corpus` |
| **LeanArchitect** | 2601.22554 | Blueprint dependency graph linking informal↔formal = `cert.derivation` DAG; "exposes latent inconsistencies" = a refuted derivation premise. | `empirical_corpus` |
| **Generalized Decidability via Brouwer Trees** | 2602.10844 | Ordinal-graded decidability ↔ Trunkit's three-valued `valid/refuted/unverified` honesty rule. | `empirical_corpus` |
| **The Ontological Neutrality Theorem** | 2601.14271 | "A neutral shared substrate must be pre-causal & pre-normative" — design rationale for cert as a neutral, append-only ledger. | `empirical_corpus` |
| **CSLib: the Lean CS Library** | 2602.04846 | External formal-artifact target for `formal_external` (Lean checker_cmd + sha256 pin). | `formal_external` |
| **Pursuit of Truth and Beauty in Lean 4** | 2602.12891 | Formally-verified grammars/optimization/matroids — `formal_external` artifact exemplar. | `formal_external` |
| **Discernment is all you need** | 2602.20038 | HOL / Schönfinkel combinators — relates to the `curry` layer (combinatory provenance). | `empirical_corpus` |

## B. Concrete computational claims (kernel- or SQL-checkable)

| Paper | arXiv | Concrete claim | Tier |
|---|---|---|---|
| **Egyptian Fractions with odd denominators** (Elsholtz) | 1606.02117 | `1 = 1/3+1/5+1/7+1/9+1/11+1/15+1/35+1/45+1/231` (distinct, odd) | **`cert_kernel`** (`unit_fraction`) — built |
| **Resolution of an Erdős problem on LCMs** (Cambie) | 2410.09138 | `lcm{53..59} > lcm{63..70}`; `f(7)=2³·3²·5·7·11·13 > f(10)` | `comp_sql` (calx) / candidate `cert_kernel` `lcm` |
| **3AP-free permutations have no exp. growth** (Ho) | 2602.13617 | θ(n) prefix `1,2,4,10,20,48,104,282,496,1066,2460,…` | `comp_sql` + OEIS match |
| **Integer Cantor Sets** | 2602.15292 | digit-restricted base-b integer sets; uniform distribution counts | `comp_sql` (calx) |
| **Cantor sets in higher dimensions II** | 2602.16667 | Hausdorff-dimension sum threshold `dim K + dim K' vs d` | `empirical_corpus` (analytic) |
| **Topological Erdős similarity** | 2410.01275 | strong-measure-zero / Gδ universality | `empirical_corpus` (analytic) |

## C. kan homology engines (knot/Floer/Khovanov cluster)

| Paper | arXiv | Demonstrates | Tier |
|---|---|---|---|
| **Knot Floer homology & the four-ball genus** (Ozsváth–Szabó) | math/0301149 | τ(K) invariant, slice-genus bounds — kan homology engine subject | `struct_kan` |
| **Holomorphic disks & genus bounds** (Ozsváth–Szabó) | math/0311496 | HF detects Seifert genus / Thurston norm | `struct_kan` |
| **Khovanov's homology for tangles & cobordisms** (Bar-Natan) | math/0410495 | categorification / TQFT functor — `kan` monoidal + functor laws | `struct_kan` |
| **HF=HM I** (Kutluhan–Lee–Taubes) | 1007.1979 | HF ≅ Seiberg–Witten Floer — a cross-engine natural isomorphism | `struct_kan` |
| **Reflection positivity & invertible topological phases** (Freed–Hopkins) | 1604.06527 | bordism/Thom-spectrum SPT classification — enrichment layer | `empirical_corpus` |

## D. Topology / fixed-point cluster (kan reflexive closure, Perron–Frobenius)

| Paper | arXiv / venue | Demonstrates | Tier |
|---|---|---|---|
| **On Banach fixed point theorems for partial metric spaces** (Valero) | AGT 6(2) 2005 *(file appears twice)* | contraction fixed points ↔ `kan` reflexive-closure attractor | `empirical_corpus` |
| **Completeness of topological spaces: an induction-free review** (Akofor) | 2603.04627 | graded base spaces / Cauchy nets — `strat` site/tower analogue | `empirical_corpus` |

## E. Adjacent systems papers (context / motivation, not certified facts)

| Paper | arXiv | Relevance |
|---|---|---|
| **A Hodge-Based Framework for Serverless** (Reali–Femminella) | 2603.08192 | Hodge decomposition of operational flows — the TEL Hodge-crown tooling (`compute_hodge_crown`). |
| **Intertwining Markov Processes via Matrix Product Operators** | 2603.09928 | duality operators / MPO — `strat` duality site. |
| **Safe Destination Passing** (Bagrel thesis) | 2601.08529 | linear/destination-passing purity — `curry` purity attestation. |
| **Uniqueness is Separation** (O'Connor et al.) | 2602.06386 | uniqueness types ↔ Separation Logic — provenance/aliasing. |
| **Towards Analyzing N-language Polyglot Programs** | 2602.00303 | n-language interaction graph — multi-schema federation analogue. |
| **H-Neurons** | 2512.01797 | hallucination-associated neurons — motivation for verifiable (not asserted) claims. |
| **Gliders on the Stranded Cellular Automata Model** | 2601.21007 | algebraic CA patterns — Nerode automata adjacency. |
| **Tessellations in contemporary composition** | 2601.15179 | aperiodic monotile ("Hat") — structural/tiling motif. |
| **DNN Music Source Separation w/ Phase Features** | 1807.02710 | out-of-domain; provenance-only if ingested. |

---

## Worked demonstration path

```bash
make apply-trunkit                         # loads steps incl. 94 (kernel) + 95 (ledger)
python tools/cert_kernel.py --write        # attests the seeded kernel claims, incl.
                                           #   the Egyptian-fraction certificate (1606.02117)
trunkit standing --method cert_kernel      # show the untrusted-certificate tier
trunkit verify <egyptian_claim_id>         # kernel re-sums 1/d_i — independent of the search
trunkit export <id> ... > bundle.json      # v2 bundle carries witness + chain hashes
python tools/verify_bundle.py bundle.json  # consumer re-runs the kernel, no DB
psql $TRUNK_DSN -c "SELECT * FROM cert.verify_chain();"   # ledger intact
```

Each lettered group is a self-contained demo slice; group **B**'s Egyptian-fraction
claim is the only one wired end-to-end so far (kernel + seed). The rest are mapped
and ready to seed as `cert.claim` rows on request.

---

# Batch 3 — second paper set (36 papers, 2024–2026)

## F. Direct hits on the cert / proof-DAG thesis

| Paper | arXiv | Demonstrates | Tier |
|---|---|---|---|
| **AI and the Structure of Mathematics** (Barkeshli, Douglas, Freedman) | 2604.06107 | "Universal proof hypergraph / hypergraph of proofs / proof objects / canonicalization" = `cert.derivation` DAG + the whole Trunkit thesis. | `empirical_corpus` |
| **The simplicity of the Hodge bundle** (Patel) | 2603.19052 | AI agent (Aletheia) produced the proof + a Human–AI Interaction Card — the AI-math provenance theme; Hodge bundle ↔ TEL Hodge-crown. | `formal_external` |
| **Equational and Inductive Reasoning for Maude in Athena** (maude2athena) | 2604.19475 | model-checking ↔ theorem-proving bridge; an external checker emits obligations another system discharges — cert_kernel ancestor. | `empirical_corpus` |
| **Computational Paths form a Weak ω-Groupoid** (Lean 4) | 2512.00657 | explicit coherence witnesses (pentagon/triangle) for higher cells = `kan` NT/coherence + OHTT theme. | `formal_external` |
| **A Foundation for the Core Mathematician** (Mumford, Friedman) | 2605.03868 | ℝ/ℤ as the substrate "core math" actually uses; truth-platonist stance ↔ Trunkit three-valued honesty + calx integer bedrock. | `empirical_corpus` |
| **From Gödel incompleteness to consistency of circuit lower bounds** (Atserias, Müller) | 2604.25251 | bounded arithmetic consistency — logic context for cert's "attested under assumptions". | `empirical_corpus` |

## G. Concrete computational claims (kernel- or SQL-checkable)

| Paper | arXiv | Concrete claim | Tier |
|---|---|---|---|
| **On Word Representations & Embeddings in Complex Matrices** (Bell, Kenison, Niskanen, Potapov, Semukhin) | 2604.15386 | `a·b·a = [[2,3],[1,2]]` in SL(2,ℤ) — matrix-semigroup membership | **`cert_kernel`** (`matrix_word`) — **built** |
| **Faithful representations of diagram categories & monoids** (East, Johnson, Kambites) | 2605.04630 | TL/Brauer/partition reps over an idempotent semiring; TL dims are Fibonacci | `struct_kan` (kan `TL_Bool` enrichment) |
| **A Matrix-Theoretic Exact Formula for Counting Primes** (Shi) | 2605.21529 | `P_k = N_k − S_k + E_k` from divisor structure, no primality test; verified k ≤ 10⁴ | `comp_sql` (calx) / candidate `cert_kernel` |
| **The Collision Invariant** (Petty) | 2604.00045 | reflection `S(a)+S(m−a) = −1`; grand mean −½ ("neutrality"); gate-width `#{g:C(g)=0}=b−1`; half-group `|W_n|=φ(m)/2` | `comp_sql` over (ℤ/mℤ)× |
| **The Collision Transform / Spectrum** (Petty) | 2604.00047 / 2604.00054 | odd-character vanishing; `Ŝ°(χ) = −B₁·S_G/φ` Bernoulli–L factorization | `comp_sql` / `empirical_corpus` |
| **Large chirotopes with computable triangulation counts** (Bouvel et al.) | 2603.10251 | recursive triangulation-count polynomials of the double circle | `comp_sql` |
| **Fibonomial determinants** (Komatsu) | 2605.14342 | determinant identities for Fibonomial coefficients (OEIS A010048) | `comp_sql` + OEIS match |

## H. Collatz / arithmetic-dynamics cluster (calx dynamics; open ⇒ `unverified`)

| Paper | arXiv | Demonstrates | Tier |
|---|---|---|---|
| **A Structural Reduction of Collatz to one-bit orbit mixing** (Chang) | 2603.25753 | Map Balance Theorem; reduces Collatz to bit-4 residue-class balance — a *checkable* finite lemma vs an *open* conjecture | `comp_sql` (lemma) / `unverified` (conjecture) |
| **Exploring Collatz Dynamics with Human–LLM Collaboration** (Chang) | 2603.11066 | burst–gap decomposition; documents the human-LLM method (Porter handoff analogue) | `unverified` |
| **(p,q)-adic Analysis and the Collatz Conjecture** (Siegel) | 2412.02902 | p-adic interpolation of the Collatz map | `unverified` |
| **Rational dynamics of a prime-representing map** (Carvalho) | 2605.21802 | order of `T(x)=⌊x⌋(1+{x})` on rationals; density-one finite order | `comp_sql` (calx dynamics) |
| **Unlikely intersections in polynomial skew products** (Noytaptim, Zhong) | 2604.04881 | heights / preperiodic points in families | `empirical_corpus` |
| **Metric mean dimension of factor maps** (Yang) | 2605.17473 | weighted/relative metric mean dimension; variational principles | `empirical_corpus` |

## I. kan / category / algebra layer

| Paper | arXiv | Demonstrates | Tier |
|---|---|---|---|
| **All elementary functions from a single operator** (Odrzywołek) | 2603.21852 | `eml(x,y)=exp(x)−ln(y)` generates the calculator; grammar `S→1\|eml(S,S)` | `struct_kan` (single generator) + Nerode grammar |
| **Lie's theorem for supertropical algebra** (Mukherjee, Ali) | 2604.13510 | max-plus / ghost semiring — `kan` idempotent-semiring enrichment | `struct_kan` |
| **A new proof of Funayama's theorem** (Bezhanishvili, Holliday) | 2603.23464 | complete-Boolean-algebra embedding iff JID+MID — lattice/`kan` | `struct_kan` |
| **Boolean inverse monoids & ample groupoids** (Ng, Tian) | 2603.25148 | inverse-semigroup ↔ groupoid correspondence — Nerode/`kan` | `struct_kan` |
| **The geometry of rectangular multisets** (Dougherty, McCammond) | 2604.14383 | multiset space cell structure; dual graph ≅ overlaid Cayley graph | `struct_kan` |
| **From cut sets to cube complexes** (Haulmark, Manning) | 2603.20424 | CAT(0) cube-complex actions from divisions — `kan` | `empirical_corpus` |
| **Robust QI embeddings of virtually free groups** (Tsouvalas) | 2603.25098 | non-Anosov QI embeddings into GLₙ | `empirical_corpus` |

## J. Topology / order / logic-complexity (strat + three-valued)

| Paper | arXiv | Demonstrates | Tier |
|---|---|---|---|
| **Cofinal types of topological groups** (Gong, Peng) | 2605.25445 | Tukey order, *fineness index* fi(P), P-base bounds — `strat` site/tower | `empirical_corpus` |
| **What can Topology tell us about Logical Complexity?** (Kihara, Ng) | 2605.14086 | Lawvere–Tierney order ≅ gamified Katětov; topology controls complexity — `strat` | `empirical_corpus` |
| **Topologically valued transition structures** (Collinson) | 2604.14031 | transition structures + topology, contravariant adjunction — Nerode + `kan` + `strat` | `struct_kan` |
| **Axiom Beta Implies Elementary Transfinite Recursion** (Frittaion, Genovesi) | 2603.13913 | transfinite recursion / constructible hierarchy — `strat` tower depth | `empirical_corpus` |
| **Three-Dimensional Affine Spatial Logics** (Trybus) | 2603.16308 | region-based affine spatial logic | `empirical_corpus` |
| **The geometry of polycons / counterexample to Wachspress** (Brüser) | 2603.18643 | adjoint-curve counterexample; AI used for visualization | `empirical_corpus` |

## K. Automata / systems / adjacent

| Paper | arXiv | Relevance |
|---|---|---|
| **Step Automata** (Wang) | 2603.08043 | automata model — Nerode adjacency. |
| **Classical Sorting Algorithms as a Model of Morphogenesis** (Zhang, Goldstein, Levin) | 2401.05375 | self-sorting arrays / basal cognition — Porter cybernetic-DFA monitoring analogue. |
| **Token Optimization for LLM Oracle→PostgreSQL Migration** (Grynets et al.) | 2605.28557 | **direct tie to `docs/CERT_SQL_GENERATION_GUARDRAILS.md`** — SQL token optimization, semantic-drift avoidance. |

---

## Updated kernel inventory (step 94)

| schema | checks | hard problem it certifies | paper |
|---|---|---|---|
| `factorization` | n = ∏pᵉ, σ(n) recompute | integer factorization | (perfect numbers) |
| `crt` | x ≡ rᵢ (mod mᵢ), coprime moduli | CRT lift | (CRT) |
| `unit_fraction` | exact Σ1/dᵢ = target + distinct/odd | Egyptian-fraction search | 1606.02117 |
| `matrix_word` | ∏ generators in word order = target | matrix-semigroup membership / word problem | 2604.15386 |

All four: SQL checker (`cert.kernel_*`) + byte-mirrored Python (`calx.kernel.check_*`) +
DB-free tests, riding the immutable hash-chained ledger (step 95).
