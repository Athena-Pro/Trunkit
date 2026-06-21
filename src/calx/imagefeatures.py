"""Deterministic, dependency-free image descriptors for the cert vision layer.

Design stance (mirrors leanbridge): the *core* stays psycopg-only. This module
has NO third-party dependency — it computes the descriptor math in pure Python
and is fully unit-testable without Pillow or a database. Actual image *decoding*
(PNG/JPEG → pixels) needs Pillow and lives in the out-of-package tool
tools/image_features.py, declared as the optional [image] extra.

The descriptor is a downscaled, mean-centred grayscale vector — deterministic and
tiny. Cosine over it ≈ Pearson correlation of layout/intensity: useful for "is
this the same figure, possibly re-rendered/rescaled", and honestly NOT a semantic
embedding. The scheme name travels with every vector as `vector_kind`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

GRID = 16
VECTOR_KIND = "gray16c"  # grayscale, 16x16, mean-centred


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity in [-1, 1]; 0 if either vector is all-zero.

    Mirrors cert.image_cosine() so Python-side and SQL-side agree.
    """
    if len(a) != len(b):
        raise ValueError(f"length mismatch: {len(a)} != {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def finalize_descriptor(values: Sequence[float]) -> list[float]:
    """Mean-centre a flat grayscale vector. Centring makes cosine discriminative
    (otherwise uniformly bright images all correlate near 1)."""
    n = len(values)
    if n == 0:
        return []
    mean = sum(values) / n
    return [float(v) - mean for v in values]


def downscale_box(matrix: Sequence[Sequence[float]], grid: int = GRID) -> list[float]:
    """Box-average downscale a 2-D grayscale matrix to grid×grid, row-major flat.

    Pure-Python fallback (and the reference used by tests). The Pillow tool may
    instead use a high-quality resampling filter; both feed finalize_descriptor.
    """
    h = len(matrix)
    w = len(matrix[0]) if h else 0
    if h == 0 or w == 0:
        raise ValueError("empty matrix")
    out: list[float] = []
    for gy in range(grid):
        y0 = (gy * h) // grid
        y1 = max(y0 + 1, ((gy + 1) * h) // grid)
        for gx in range(grid):
            x0 = (gx * w) // grid
            x1 = max(x0 + 1, ((gx + 1) * w) // grid)
            s = 0.0
            cnt = 0
            for y in range(y0, min(y1, h)):
                row = matrix[y]
                for x in range(x0, min(x1, w)):
                    s += row[x]
                    cnt += 1
            out.append(s / cnt if cnt else 0.0)
    return out


def descriptor_from_matrix(matrix: Sequence[Sequence[float]], grid: int = GRID) -> list[float]:
    """Full pipeline from a grayscale matrix: downscale → mean-centre."""
    return finalize_descriptor(downscale_box(matrix, grid))


# ---------------------------------------------------------------------------
# Fourier descriptor — radial-binned magnitude spectrum (texture / frequency).
#
# Complements gray16c (spatial layout) with a frequency-content descriptor.
# Built from the magnitude of the 2-D DFT, which is TRANSLATION-invariant
# (|DFT| discards phase), then averaged into radial rings, which makes it
# (approximately) ROTATION-invariant (rotation permutes points within a ring).
# It answers "same texture / periodicity, regardless of position or orientation"
# — a different question than spatial cosine, so it is genuinely complementary
# rather than a low-passed shadow of the spatial grid.
#
# Pure Python, no numpy: the 2-D DFT is computed SEPARABLY (rows then columns),
# O(grid^3) not O(grid^4), so a 32x32 spectrum is fast. The [image] tool feeds
# the same function, so reference and tool agree exactly.
# ---------------------------------------------------------------------------

FOURIER_GRID = 32
FOURIER_RINGS = 16
FOURIER_VECTOR_KIND = "fourier_ring16"  # 32x32 |DFT|, log, 16 radial rings, mean-centred


def _dft1d(vec: Sequence[float]) -> list[complex]:
    n = len(vec)
    out: list[complex] = []
    for k in range(n):
        s = 0j
        for j, val in enumerate(vec):
            angle = -2.0 * math.pi * k * j / n
            s += val * complex(math.cos(angle), math.sin(angle))
        out.append(s)
    return out


def dft2d(matrix: Sequence[Sequence[float]]) -> list[list[complex]]:
    """Separable 2-D DFT (rows then columns). Input square-ish grid of floats."""
    rows = [_dft1d(row) for row in matrix]
    h = len(rows)
    w = len(rows[0]) if h else 0
    cols_t: list[list[complex]] = []
    for v in range(w):
        col = [rows[y][v] for y in range(h)]
        cols_t.append(_dft1d(col))         # cols_t[v][u] = F[u][v]
    return [[cols_t[v][u] for v in range(w)] for u in range(h)]


def _radial_profile(mag: Sequence[Sequence[float]], rings: int) -> list[float]:
    """Average a centre-shifted (DC-in-middle) magnitude grid into radial rings."""
    h = len(mag)
    w = len(mag[0]) if h else 0
    cy, cx = h // 2, w // 2
    maxr = math.hypot(max(cy, h - 1 - cy), max(cx, w - 1 - cx)) or 1.0
    sums = [0.0] * rings
    cnts = [0] * rings
    for y in range(h):
        for x in range(w):
            r = math.hypot(y - cy, x - cx)
            b = min(rings - 1, int(r / maxr * rings))
            sums[b] += mag[y][x]
            cnts[b] += 1
    return [sums[b] / cnts[b] if cnts[b] else 0.0 for b in range(rings)]


def fourier_descriptor_from_matrix(
    matrix: Sequence[Sequence[float]], grid: int = FOURIER_GRID, rings: int = FOURIER_RINGS
) -> list[float]:
    """grayscale matrix → grid×grid → |DFT| (log) → DC-centred → radial rings → mean-centre."""
    flat = downscale_box(matrix, grid)
    m = [flat[i * grid:(i + 1) * grid] for i in range(grid)]
    spec = dft2d(m)
    mag = [[math.log1p(abs(spec[y][x])) for x in range(grid)] for y in range(grid)]
    shifted = [[mag[(y + grid // 2) % grid][(x + grid // 2) % grid] for x in range(grid)]
               for y in range(grid)]
    return finalize_descriptor(_radial_profile(shifted, rings))
