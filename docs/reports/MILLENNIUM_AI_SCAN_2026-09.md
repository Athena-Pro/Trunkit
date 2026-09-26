# Millennium problems × AI × Lean — scan, 2026-09-26

*Companion to `Trunkit_Erdos_AI_Capability_Fit.md`. That report's build queue
(T1–T5) shipped in 0.5.0. This scan asked what the next standard of evidence is,
now that AI results reach Millennium problems. It records what was built in
response (117, the comparator checker kind, and the Navier–Stokes and #1196
anchors).*

## What happened

| Problem | Result | How it was found | How it was checked |
|---|---|---|---|
| **Navier–Stokes** | OpenAI, 2026-09-08: finite-time blow-up on ℝ³ (and the torus) **under a smooth, decaying external force**. That is Clay alternatives (C)/(D). | About 10,000 agents for 88 h, building on Córdoba–Martínez-Zoroa's infinite-cascade constructions. | Lean 4 plus leanprover/comparator (`ComparatorChallenges/NavierStokes.json`). The challenge statement was adapted from Formal Conjectures. |
| **Riemann ζ** | Anthropic, 2026-08: at least 67.2% of nontrivial zeros are on the critical line (was 41.6%). | About 60 subagents with separated roles (2 developed the key ideas, 13 validated) over roughly 1.5 days. | Lean, passes comparator. Anthropic says it does not expect the method to reach RH. |
| **Yang–Mills** | No AI result. The human work is partial: a 3D outline (arXiv:2608.10133) and deconfinement for SO(3) lattice YM at strong coupling (2605.16162). | ML use is confined to lattice simulation. | Mathlib has no gauge-theory foundations to state the problem on, so Formal Conjectures has no Yang–Mills file. |

**Navier–Stokes caveats.**
- The result answers Clay's text, which admits forcing. The field's question is unforced, and the construction is reported not to survive removing the force.
- Formal Conjectures now labels (A)/(B) "(unforced)" and leaves them open.
- There is a public priority dispute.

The ledger records the result, the interpretation and the dispute as separate
things (`tools/anchor_navier_stokes.py`).

## The common method, and the standard it set

1. **A fixed challenge statement and a replayable proof, judged by comparator.**
   Challenge and solution are built in separate sandboxes. Every declaration the
   statement uses must be identical in both. Only permitted axioms are allowed,
   and the proof is replayed through Lean's kernel and independent ones.
2. **Numerics precise enough to prove.** PINN and Gauss–Newton training
   reaches near machine precision, so the result can feed a computer-assisted
   proof (arXiv:2509.14185, 2511.22819).
3. **Many agents with separated roles**, with progress kept in a shared
   blueprint (LeanMarathon 2606.05400, the Station 2608.23691).
4. **Honest labelling of hybrid proofs.** A kernel-checked structure plus a
   hash-receipted search, explicitly "not an end-to-end Lean proof" (Leech
   tree, 2609.20492).

## What it exposed in Trunkit, and what was done

| Gap | Done |
|---|---|
| 106 binds statement **text**. Weakening a definition the statement depends on leaves text and pretty-printed type unchanged, so `valid` survives. | **117**: AxiomAudit emits the statement closure. `bind-statement` pins it, and the harness observes it on every attest, refuting on definition drift and naming the constant. Real before/after fixtures are in `tests/fixtures/lean_closure/`. |
| No way to run the check the field now uses. | `tools/comparator_check.sh` and `register-lean --comparator`. Exit 2 (e.g. no landrun) is now `error`, not `refuted`. |
| The Navier–Stokes result had no ledger representation. | Claims for (C) and (D), bound to their Formal Conjectures closures (3,106 and 2,799 constants), with the proof pinned at `openai/NavierStokesAndEuler@8937a8f`. The interpretation is a separate claim with 0 premises, plus a credit graph. |
| The #1196 anchor cited arXiv:2601.07421, which is #728's writeup. | Now cites arXiv:2605.00301, with LeanMarathon pinned at `e2febe2`. |
| The vendored Formal Conjectures snapshot is behind upstream. | The upstream (C)/(D) statements were diffed and are **unchanged**; only docs, attributes, module syntax and helper-lemma proofs moved. The snapshot was deliberately not refreshed. Upstream has moved to the Lean module system and renamed `Millenium/` to `Millennium/`, so a refresh is a toolchain migration and would change the registered A080170 artifact's trusted digest. |

## Still open (honestly)

- Nothing here was *checked* end to end against the real proofs. Comparator's
  sandbox is Linux-only, and neither proof repository is vendored. The
  Navier–Stokes and #1196 anchors stay `unchecked` until someone runs them on
  Linux, which is what the anchors say.
- The closure bound for Navier–Stokes (C)/(D) was taken under Lean v4.27, and
  the proof builds under v4.34. The ledger reports that pairing as not
  comparable (`unverified`) until the closure is rebound in the proof's own
  environment. Comparator is the check that bridges the two.

## Sources

- Quanta, "AI has solved one of math's $1 million Millennium Prize problems" (2026-09-08)
- Scientific American, "Did OpenAI solve the wrong Navier-Stokes problem?"
- github.com/openai/NavierStokesAndEuler · github.com/leanprover/comparator
- anthropic.com/research/riemann-zeta
- google-deepmind/formal-conjectures (`Millennium/NavierStokes.lean`, history since 641ff32)
- arXiv: 2509.14185, 2511.22819, 2609.26790, 2605.13827, 2608.10133, 2605.16162,
  2606.05400, 2608.23691, 2609.20492, 2604.05984, 2606.13925, 2605.16407, 2509.06902
