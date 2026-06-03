# Federation Status Board

*One board for everything. Generated 2026-06-03 from the Trunkit cert ledger (`cert.board`). Green = checked & true, red = checked & false, grey = not yet checkable.*

## Headline

**✅ 184 verified · ❌ 0 failed · ❓ 22 unknown** &nbsp;(of 206 tracked claims)

> 89% of everything tracked is independently verified right now.

## By area

| Area | ✅ verified | ❌ failed | ❓ unknown |
|---|---:|---:|---:|
| Cross-lab structures | 3 | 0 | 1 |
| Curry (provenance) | 4 | 0 | 1 |
| Math: homology | 1 | 0 | 3 |
| Math: kan engines | 13 | 0 | 9 |
| Methods & self-checks | 7 | 0 | 0 |
| Other | 134 | 0 | 8 |
| TEL behavior | 3 | 0 | 0 |
| TEL builds | 8 | 0 | 0 |
| TEL constants | 1 | 0 | 0 |
| TEL graphics | 5 | 0 | 0 |
| TEL results & hygiene | 5 | 0 | 0 |

## What the colours mean

- **✅ verified** — a probe ran and confirmed it (re-runnable; not an assertion).
- **❌ failed** — a probe ran and it did *not* hold. A real, surfaced contradiction.
- **❓ unknown** — honestly not yet checkable (no data, open problem, or tooling missing). *Never* shown as green.

*Backed by one Postgres database; the `.curry`/`.lace`/per-method dotfiles are gone — methods are lenses (schemas) in the one ledger, not files.*