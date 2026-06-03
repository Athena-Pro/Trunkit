# Federation Status Board

*One board for everything. Generated 2026-06-03 from the Trunkit cert ledger (`cert.board`). Green = checked & true, red = checked & false, grey = not yet checkable.*

## Headline

**✅ 172 verified · ❌ 8 failed · ❓ 21 unknown** &nbsp;(of 201 tracked claims)

> 86% of everything tracked is independently verified right now.

## By area

| Area | ✅ verified | ❌ failed | ❓ unknown |
|---|---:|---:|---:|
| Cross-lab structures | 3 | 0 | 1 |
| Curry (provenance) | 0 | 0 | 2 |
| Math: homology | 1 | 0 | 3 |
| Math: kan engines | 12 | 1 | 9 |
| Methods & self-checks | 7 | 0 | 0 |
| Other | 129 | 7 | 6 |
| TEL behavior | 3 | 0 | 0 |
| TEL builds | 8 | 0 | 0 |
| TEL constants | 1 | 0 | 0 |
| TEL graphics | 3 | 0 | 0 |
| TEL results & hygiene | 5 | 0 | 0 |

## ❌ Needs attention (checked & failed)

- **[Math: kan engines]** kan functor curry_to_calx maps exactly 19 objects
- **[Other]** n=12 has stratified valuation decomposition {2:2, 3:1} (p-adic level structure)
- **[Other]** feigenbaum_report precision_dps=60: delta_5 should match true Feigenbaum δ=4.669201609
- **[Other]** mdl_renorm_experiment: median delta_codelength > 0 near mu_inf (renorm model compresse
- **[Other]** bic_comparison_experiment: near.mean_delta is a finite real number
- **[Other]** bic_comparison_experiment: far.median_delta is a well-defined finite real (not NaN)
- **[Other]** corpus contains lit_displayed_type_theory (Kolomatskaia–Shulman)
- **[Other]** invariance_harness_mdl_FIXED: all_codelengths_positive=true (negative codelengths are 

## What the colours mean

- **✅ verified** — a probe ran and confirmed it (re-runnable; not an assertion).
- **❌ failed** — a probe ran and it did *not* hold. A real, surfaced contradiction.
- **❓ unknown** — honestly not yet checkable (no data, open problem, or tooling missing). *Never* shown as green.

*Backed by one Postgres database; the `.curry`/`.lace`/per-method dotfiles are gone — methods are lenses (schemas) in the one ledger, not files.*