"""DB-free unit tests for the Scott / domain-theory engine (kan step 98).

Exercises the pure enumeration math in local/tools/build_scott.py against
posets with known closed-form answers: chains, antichains, and the V poset.
Ground truths used:

  * n-chain:  n+1 upper sets; closure systems = subsets containing the top,
    so 2^(n-1) closures forming a Boolean 2^(n-1) lattice; monotone
    self-maps = C(2n-1, n).
  * n-antichain: every subset is open (2^n); the only closure system is P
    itself (identity closure), a trivial (vacuously Boolean) lattice;
    all n^n self-maps are monotone.
  * V poset (x<=z, y<=z): 5 opens; closure systems are the subsets of
    {x,y} unioned with {z}, giving a Boolean 2^2 closure lattice.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

_BUILD_SCOTT = (
    pathlib.Path(__file__).parent.parent / "local" / "tools" / "build_scott.py"
)
if not _BUILD_SCOTT.is_file():
    pytest.skip("local/tools/build_scott.py not present", allow_module_level=True)
_spec = importlib.util.spec_from_file_location("build_scott", _BUILD_SCOTT)
_scott = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_scott)


def make_leq(points, pairs):
    """Reflexive relation over *points* plus the given strict pairs."""
    leq = {(a, b): a == b for a in points for b in points}
    for a, b in pairs:
        leq[(a, b)] = True
    return leq


def chain(n):
    pts = [f"c{i}" for i in range(n)]
    return pts, {(a, b): pts.index(a) <= pts.index(b) for a in pts for b in pts}


UNIVERSAL_LAWS = ("poset_valid", "closures_lattice", "scott_alexandrov", "spec_is_order")


# ── poset validity ───────────────────────────────────────────────────────────

def test_rejects_non_antisymmetric():
    pts = ["a", "b"]
    leq = make_leq(pts, [("a", "b"), ("b", "a")])
    assert not _scott.poset_is_valid(pts, leq)


def test_rejects_non_transitive():
    pts = ["a", "b", "c"]
    leq = make_leq(pts, [("a", "b"), ("b", "c")])  # missing (a, c)
    assert not _scott.poset_is_valid(pts, leq)


# ── chains ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_chain_closed_forms(n):
    pts, leq = chain(n)
    f = _scott.attest_poset(pts, leq)
    assert all(f[law] for law in UNIVERSAL_LAWS)
    assert f["n_scott_opens"] == n + 1
    assert f["n_closures"] == 2 ** (n - 1)
    # order dual of a chain is a chain, so interiors mirror closures
    assert f["n_interiors"] == 2 ** (n - 1)
    assert f["n_atoms"] == n - 1
    assert f["closures_boolean"] and f["closures_two_pow_k"]
    assert f["n_monotone"] == _binom(2 * n - 1, n)


def _binom(n, k):
    import math
    return math.comb(n, k)


# ── antichains ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n", [2, 3])
def test_antichain_closed_forms(n):
    pts = [f"a{i}" for i in range(n)]
    leq = make_leq(pts, [])
    f = _scott.attest_poset(pts, leq)
    assert all(f[law] for law in UNIVERSAL_LAWS)
    assert f["n_scott_opens"] == 2 ** n          # every subset is an upper set
    assert f["n_closures"] == 1                  # identity only
    assert f["n_atoms"] == 0
    assert f["closures_two_pow_k"]               # 2^0 == 1
    assert f["n_monotone"] == n ** n             # no order to preserve


# ── the V poset (smallest genuine non-chain with a join) ────────────────────

def test_v_poset():
    pts = ["x", "y", "z"]
    leq = make_leq(pts, [("x", "z"), ("y", "z")])
    f = _scott.attest_poset(pts, leq)
    assert all(f[law] for law in UNIVERSAL_LAWS)
    assert f["n_scott_opens"] == 5               # {}, z, xz, yz, xyz
    assert f["n_closures"] == 4                  # {z} ∪ any subset of {x,y}
    assert f["n_atoms"] == 2
    assert f["closures_boolean"] and f["closures_two_pow_k"]


# ── monotone-map cap ─────────────────────────────────────────────────────────

def test_monotone_count_capped_above_mono_cap():
    n = _scott.MONO_CAP + 1
    pts, leq = chain(n)
    f = _scott.attest_poset(pts, leq)
    assert f["n_monotone"] is None               # capped, recorded as uncounted


# ── incidence window selection ───────────────────────────────────────────────

def test_incidence_window_takes_largest_fitting_prefix():
    cells = [(0, 0), (0, 1), (1, 1), (0, 2), (1, 2), (2, 2)]
    assert _scott.incidence_window(cells, 3) == [(0, 0), (0, 1), (1, 1)]
    assert _scott.incidence_window(cells, 6) == cells
    assert _scott.incidence_window(cells, 1) == [(0, 0)]
