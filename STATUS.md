# Federation Status Board

*One board for everything. Generated 2026-06-10 from the Trunkit cert ledger (`cert.board`). Green = checked & true, red = checked & false, grey = not yet checkable.*

## Headline

**✅ 203 verified · ❌ 3 failed · ❓ 29 unknown** &nbsp;(of 235 tracked claims)

> 86% of everything tracked is independently verified right now.

## By area

| Area | ✅ verified | ❌ failed | ❓ unknown |
|---|---:|---:|---:|
| Cross-lab structures | 3 | 0 | 1 |
| Curry (provenance) | 4 | 0 | 2 |
| Math: homology | 1 | 0 | 3 |
| Math: kan engines | 26 | 0 | 10 |
| Methods & self-checks | 7 | 0 | 0 |
| Other | 140 | 3 | 13 |
| TEL behavior | 3 | 0 | 0 |
| TEL builds | 8 | 0 | 0 |
| TEL constants | 1 | 0 | 0 |
| TEL graphics | 5 | 0 | 0 |
| TEL results & hygiene | 5 | 0 | 0 |

## ❌ Needs attention (checked & failed)

- **[Other]** agent LabOrderAgent session sess-001 proposed OrderDefinitiveTest — decision: rejected
- **[Other]** AXIOM REFUTED: tau_mersenne_exponential in divisor_growth_lemmas.lean (sha 3556b0bc31a
- **[Other]** INTERNAL INCONSISTENCY: lemma eligible_count=tau vs validator data: eligible_count(2,5

## What the colours mean

- **✅ verified** — a probe ran and confirmed it (re-runnable; not an assertion).
- **❌ failed** — a probe ran and it did *not* hold. A real, surfaced contradiction.
- **❓ unknown** — honestly not yet checkable (no data, open problem, or tooling missing). *Never* shown as green.

*Backed by one Postgres database; the `.curry`/`.lace`/per-method dotfiles are gone — methods are lenses (schemas) in the one ledger, not files.*