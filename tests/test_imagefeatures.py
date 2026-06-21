"""Unit tests for calx.imagefeatures (DB-free, Pillow-free)."""

from __future__ import annotations

import math

import pytest

from calx import imagefeatures as imf


def test_cosine_identical_is_one():
    v = [1.0, -2.0, 3.0, 0.5]
    assert imf.cosine(v, v) == pytest.approx(1.0)


def test_cosine_opposite_is_minus_one():
    v = [1.0, -2.0, 3.0, 0.5]
    w = [-x for x in v]
    assert imf.cosine(v, w) == pytest.approx(-1.0)


def test_cosine_orthogonal_is_zero():
    assert imf.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_zero_vector():
    assert imf.cosine([0.0, 0.0], [1.0, 2.0]) == 0.0


def test_cosine_length_mismatch_raises():
    with pytest.raises(ValueError):
        imf.cosine([1.0], [1.0, 2.0])


def test_cosine_known_value():
    # cos between (1,1) and (1,0) = 1/sqrt(2)
    assert imf.cosine([1.0, 1.0], [1.0, 0.0]) == pytest.approx(1 / math.sqrt(2))


def test_finalize_descriptor_is_mean_centred():
    out = imf.finalize_descriptor([10.0, 20.0, 30.0])
    assert sum(out) == pytest.approx(0.0)
    assert out == pytest.approx([-10.0, 0.0, 10.0])


def test_downscale_box_dims_and_determinism():
    # 32x32 ramp matrix → 16x16 descriptor of length 256
    mat = [[float((x + y) % 256) for x in range(32)] for y in range(32)]
    d1 = imf.descriptor_from_matrix(mat, grid=16)
    d2 = imf.descriptor_from_matrix(mat, grid=16)
    assert len(d1) == 16 * 16
    assert d1 == d2  # deterministic


def test_descriptor_distinguishes_different_images():
    left_bright = [[(255.0 if x < 16 else 0.0) for x in range(32)] for _ in range(32)]
    top_bright = [[(255.0 if y < 16 else 0.0) for _ in range(32)] for y in range(32)]
    da = imf.descriptor_from_matrix(left_bright)
    db = imf.descriptor_from_matrix(top_bright)
    # same image matches itself; different layout scores clearly lower
    assert imf.cosine(da, da) == pytest.approx(1.0)
    assert imf.cosine(da, db) < 0.5
