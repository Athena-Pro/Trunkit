"""Fourier (radial-binned magnitude-spectrum) descriptor tests.

The descriptor is translation-invariant (|DFT| discards phase) and approximately
rotation-invariant (radial averaging) — a frequency/texture view that complements
the spatial gray16c layout descriptor. Pure-Python, no numpy/DB for the unit
tests; the DB-backed test reuses the existing cosine + image_match_claim with
zero SQL changes (just a new vector_kind).
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from calx import imagefeatures as imf


def _stripes(n: int, period: int, vertical: bool = True) -> list[list[float]]:
    m = []
    for y in range(n):
        row = []
        for x in range(n):
            t = x if vertical else y
            row.append(255.0 if (t // (period // 2)) % 2 == 0 else 0.0)
        m.append(row)
    return m


def _roll(m, k):
    return [row[k:] + row[:k] for row in m]


def _transpose(m):
    return [list(r) for r in zip(*m, strict=False)]


# --- unit (DB-free, numpy-free) ---------------------------------------------

def test_dft1d_impulse():
    # DFT of a unit impulse is all-ones magnitude
    out = imf._dft1d([1.0, 0.0, 0.0, 0.0])
    assert all(abs(abs(z) - 1.0) < 1e-9 for z in out)


def test_descriptor_shape_and_determinism():
    v = imf.fourier_descriptor_from_matrix(_stripes(64, 4))
    assert len(v) == imf.FOURIER_RINGS
    assert v == imf.fourier_descriptor_from_matrix(_stripes(64, 4))  # deterministic


def test_translation_invariant():
    base = imf.fourier_descriptor_from_matrix(_stripes(64, 4))
    shifted = imf.fourier_descriptor_from_matrix(_roll(_stripes(64, 4), 5))
    assert imf.cosine(base, shifted) > 0.99          # |DFT| ignores position


def test_rotation_approximately_invariant():
    base = imf.fourier_descriptor_from_matrix(_stripes(64, 4))
    rot = imf.fourier_descriptor_from_matrix(_transpose(_stripes(64, 4)))  # 90° → H stripes
    assert imf.cosine(base, rot) > 0.99              # radial averaging ignores orientation


def test_discriminates_frequency():
    fine = imf.fourier_descriptor_from_matrix(_stripes(64, 4))
    coarse = imf.fourier_descriptor_from_matrix(_stripes(64, 16))
    same_shifted = imf.cosine(fine, imf.fourier_descriptor_from_matrix(_roll(_stripes(64, 4), 5)))
    diff = imf.cosine(fine, coarse)
    assert diff < 0.98                               # different periodicity is distinguishable
    assert diff < same_shifted                       # and less similar than a mere translation


def test_complements_spatial_on_rotation():
    """Headline: spatial cosine sees a rotated texture as different; Fourier sees it as same."""
    s = _stripes(64, 4)
    sr = _transpose(s)
    spatial = imf.cosine(imf.descriptor_from_matrix(s), imf.descriptor_from_matrix(sr))
    fourier = imf.cosine(imf.fourier_descriptor_from_matrix(s),
                         imf.fourier_descriptor_from_matrix(sr))
    assert spatial < 0.5      # spatial: different layout
    assert fourier > 0.95     # fourier: same texture


# --- DB-backed: new vector_kind rides existing cosine + match-claim ----------

def _calx_dsn():
    dsn = os.environ.get("CALX_TEST_DSN") or os.environ.get("ARITHMETIC_DB_TEST_DSN")
    if not dsn:
        pytest.skip("No test DSN provided. Refusing to write to default/production ledger.")
    return dsn


@pytest.fixture()
def conn():
    try:
        c = psycopg.connect(_calx_dsn(), connect_timeout=3)
    except psycopg.Error as exc:
        pytest.skip(f"calx DB not reachable: {exc}")
    with c:
        yield c


def _register(cur, vec, label):
    cur.execute(
        "SELECT (cert.register_image(%s,%s,%s,%s,%s,%s,%s::jsonb)).id",
        (f"sha-{uuid.uuid4()}", imf.FOURIER_VECTOR_KIND, vec, 32, 32, label, "{}"),
    )
    return cur.fetchone()[0]


def test_fourier_match_claim_valid_and_refuted(conn):
    base = imf.fourier_descriptor_from_matrix(_stripes(64, 4))
    shifted = imf.fourier_descriptor_from_matrix(_roll(_stripes(64, 4), 5))   # ~same texture
    coarse = imf.fourier_descriptor_from_matrix(_stripes(64, 16))             # different freq
    with conn.cursor() as cur:
        ref = _register(cur, base, "fourier-ref")
        same = _register(cur, shifted, "fourier-translated")
        diff = _register(cur, coarse, "fourier-coarse")
        # threshold 0.99: translated texture confirms, different frequency refutes
        cur.execute("SELECT cert.image_match_claim(%s,%s,%s,%s)", (same, ref, 0.99, "texture"))
        c_same = cur.fetchone()[0]
        cur.execute("SELECT cert.image_match_claim(%s,%s,%s,%s)", (diff, ref, 0.99, "texture"))
        c_diff = cur.fetchone()[0]
        cur.execute("SELECT (cert.check(%s)).status", (c_same,))
        assert cur.fetchone()[0] == "valid"
        cur.execute("SELECT (cert.check(%s)).status", (c_diff,))
        assert cur.fetchone()[0] == "refuted"
