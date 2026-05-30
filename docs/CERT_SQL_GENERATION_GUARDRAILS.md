# Guardrails for LLM-generated Trunkit SQL

From *Token Optimization Strategies for LLM-Based Oracle→PostgreSQL Migration*
(EPAM, arXiv 2605.28557). Trunkit is a PostgreSQL SDD substrate and its probes /
plpgsql are LLM-generated, so the paper's findings apply directly to *us*.

## Apply (safe token optimization)
- **Context pruning** — strip comments and irrelevant schema when prompting for a probe.
- **Minification** — collapse whitespace; the paper shows it's near-free on quality.
- **Adaptive routing** — pick the verification tier by the claim's structure
  (comp_sql for in-DB checks, formal_external for hash-pinned proofs). We already
  do this (step 90 equip + tier-dependent qualification).

## Never (paper-confirmed hazards)
- **Identifier masking** — the paper measures **−30 pp Semantic Match**. Trunkit's
  entire cert layer keys on stable identifiers (`subject_kind`, claim `statement`,
  function names). NEVER alias/shorten identifiers in generated cert SQL.
- **Schema distillation / aggressive compression** — −44.5 pp Semantic Match.
  A probe that "looks efficient" but drops semantics is a false green.

## Principle
Token Efficiency alone is misleading. A probe is good only if it is **syntactically
valid AND semantically faithful** — the same multi-metric, no-fake-green discipline
the cert ledger enforces (valid / refuted / unverified, never a single optimized number).
