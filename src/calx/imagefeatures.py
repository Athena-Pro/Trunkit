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
