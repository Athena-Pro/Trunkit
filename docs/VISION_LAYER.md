# Vision / image layer — design & status

*Prepared 2026-06-20. Scope chosen: image **anchoring** + perceptual **similarity** (cosine), plus a **figure→data extraction** starting point. Companion to `docs/reports/Trunkit_Erdos_AI_Capability_Fit.md` (the §2(c) "scientific images" cluster).*

## Principle: minimality preserved

The reference approach for image similarity (e.g. the cosine-similarity write-up that motivated this) embeds images with a ~2 GB Vision Transformer. That is the opposite of Trunkit's stance. So we keep the **cosine idea** but replace the heavy model with a **deterministic, dependency-free descriptor**, and follow the same split the Lean bridge uses: the heavy/optional part lives **outside** the ~1.5 MB core.

- **Core (psycopg-only, unchanged footprint):** descriptor math (`src/calx/imagefeatures.py`) and the SQL layer (`src/calx/sql/91_cert_image.sql`). No new core dependency.
- **Out-of-package tools (optional `[image]` extra = Pillow):** `tools/image_features.py` (decode → sha256 + descriptor → register) and `tools/figure_extract.py` (plot → data series).

## What it does

**Anchoring.** `cert.image_artifact` registers an image by `sha256` (exact integrity) plus a small descriptor vector and metadata. The row is the carried, hash-pinned artifact — the direct fit for the Erdős "secondary artifacts / scientific images" cluster (generated figures, explicit constructions).

**Similarity.** The descriptor is a downscaled, mean-centred grayscale vector (`gray16c`, 16×16 = 256 dims). `cert.image_cosine(a,b)` computes cosine in **pure SQL** — no `pgvector`, no extension. Cosine ≈ Pearson correlation of layout/intensity: good for "is this the same figure, possibly re-rendered/rescaled," and honestly **not** semantic understanding.

**Attestation reuses everything.** A match is a standard `comp_sql` claim built by `cert.image_match_claim(candidate, reference, threshold)`: the probe is self-contained, so it re-verifies via `cert.check` and travels in export bundles with **zero** changes to the cert/bundle machinery. Three-valued by design: cosine ≥ τ → `valid`; < τ → `refuted`; descriptor-kind/length mismatch → `unverified`.

**Extraction (starting point).** `tools/figure_extract.py` turns a clean single-curve plot into an (x,y) series via threshold + per-column dark-row mean + a 2-point calibration you supply, so the *data* can be attested with calx's existing OEIS-match / comp_sql. Documented limits: one single-valued curve, no gridline/legend masking (crop first), no automatic axis OCR (that would pull in a heavy dep this layer avoids).

## Verification status

All green against a real PostgreSQL (sandbox instance, since the local Docker engine was flapping during this session):

- `tests/test_imagefeatures.py` — 9 unit tests (cosine identities/known values, mean-centring, downscale determinism, layout discrimination).
- `tests/test_cert_image.py` — 4 DB-backed (SQL cosine == Python cosine; identical→valid; different→refuted; kind-mismatch→unverified).
- Real-image pipeline: `assets/logo.png` registered (sha256 + 256-dim `gray16c`), self-match attested `valid`, cosine ≈ 1.0.
- Extraction: synthetic y=x plot recovered with max error 0.000 over 200 points.

**To confirm on Docker once the engine is stable:**

```
psql postgresql://trunk:trunk@localhost:5434/trunk -f src/calx/sql/91_cert_image.sql
set CALX_TEST_DSN=postgresql://trunk:trunk@localhost:5434/trunk
pytest tests/test_imagefeatures.py tests/test_cert_image.py -q
pip install "trunkit[image]"   # Pillow, for the tools only
python tools/image_features.py register assets/logo.png --label logo
```

## Deliberately deferred

- **Vision-as-external-witness tier** (an external vision model's claim anchored three-valued) — sketched in the capability review; not built, to keep the model out of the package.
- **Bundle row for `image_artifact` itself** — not needed: match *claims* already export; the registry is local storage. Add only if raw descriptors must travel.
