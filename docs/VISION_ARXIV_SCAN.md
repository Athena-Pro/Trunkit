# Pre-launch arXiv scan — machine vision vs Trunkit's vision layer

*2026-06-20. Quick fit-scan (abstracts) before closing the vision expansion and pushing live. Lens: does anything change our minimal anchoring + cosine + figure-extraction design, or reveal a missing capability that should block release?*

## Verdict: ship.

The literature **validates** Trunkit's design choices and offers only **optional, post-launch** refinements. No blocker, no missing capability that holds the release.

## What validates the current design

- **Perceptual Hash Registry for provenance** ([2602.02412](https://arxiv.org/abs/2602.02412)). A registry of perceptual hashes + similarity search to verify an image's provenance after *benign transforms* — essentially `cert.image_artifact` (sha256 + descriptor) minus the blockchain. Notably it "does not aim to detect all synthetic images, only verify registered ones" — the same three-valued honesty we adopted. Strong confirmation of the anchoring+similarity pattern.
- **"Charts Are Not Images" / FigEdit** ([2512.00752](https://arxiv.org/abs/2512.00752)). A chart is structured data under a graphical grammar; pixel metrics (SSIM/PSNR) miss semantic correctness. This **confirms our split**: pixel cosine is right for *figure provenance* but the wrong tool for *chart identity* — which is exactly why chart content goes through `figure_extract → data → OEIS/comp_sql`, not through cosine.

## Optional refinements (post-launch, all minimality-compatible)

- **pHash/dHash descriptor option** (PhishSnap, on-device pHash, [2512.02243](https://arxiv.org/abs/2512.02243); lightweight statistical hashing, [2510.27127](https://arxiv.org/abs/2510.27127)). A proper perceptual hash is more transform-robust than our downscaled mean-centred gray, and still dependency-light. Add as another `vector_kind` if/when needed — not required for v1.
- **BK-tree / indexed similarity search** ([2602.02412](https://arxiv.org/abs/2602.02412)). Our cosine match is an O(N) scan; fine for now. If the registry grows large, swap in an indexed nearest-neighbour. Pure scaling concern.
- **Grid-overlay spatial priming** ([2605.08220](https://arxiv.org/abs/2605.08220)). Overlaying a coordinate grid before extraction cut error materially (SMAPE 25.5→19.5%). A cheap accuracy trick for `figure_extract` (or any external VLM path).
- **Stronger extractor reference** — **ChartZero** ([2605.05820](https://arxiv.org/abs/2605.05820)) trains zero-shot on *synthetic math functions only* (no real annotations) and handles thin/intersecting curves; **PlotPick** ([2605.06021](https://arxiv.org/abs/2605.06021)) shows VLMs beat dedicated chart-to-table models. Both are the upgrade path beyond our single-curve scaffold — but they are trained/heavy models, so they belong in the deferred **vision-as-external-witness** tier, not the psycopg-only core.

## Caveat to record (not a build)

- **Integrity Clash** ([2603.02378](https://arxiv.org/abs/2603.02378)). C2PA provenance and watermarks can each verify yet contradict each other. *If* Trunkit ever ingests externally-signed/provenanced images, audit provenance and content **jointly**, never in isolation.

## Bottom line

The vision layer's scope and honesty match the current research frontier. The exact-integrity anchor + scale-invariant cosine pre-filter + extraction-feeds-attestation split is the right shape; everything else the field offers is an optional enhancement that can land later without redesign. **Close the expansion and push live.**

Sources: arXiv [2602.02412](https://arxiv.org/abs/2602.02412), [2512.00752](https://arxiv.org/abs/2512.00752), [2605.05820](https://arxiv.org/abs/2605.05820), [2605.06021](https://arxiv.org/abs/2605.06021), [2605.08220](https://arxiv.org/abs/2605.08220), [2512.02243](https://arxiv.org/abs/2512.02243), [2510.27127](https://arxiv.org/abs/2510.27127), [2603.02378](https://arxiv.org/abs/2603.02378).
