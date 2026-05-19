"""Tier-3 compose_index unit tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch


def _load_compose():
    path = Path(__file__).resolve().parents[1] / "tools" / "compose_match.py"
    spec = importlib.util.spec_from_file_location("compose_match", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["compose_match"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_compose_index_multiset():
    cm = _load_compose()
    base = {1: 2, 2: 3, 3: 5, 4: 7, 5: 11, 8: 19}
    stream = cm.compose_index_stream(base, [1, 1, 2, 3, 5])
    assert stream == [2, 2, 3, 5, 11]


def test_identity_indices_skipped():
    cm = _load_compose()
    assert cm.is_identity_indices([1, 2, 3, 4, 5])
    assert not cm.is_identity_indices([1, 1, 2, 3, 5])


def test_catalog_tautology_detected():
    cm = _load_compose()
    catalog = {
        "A000040": [2, 3, 5, 7, 11],
        "A000045": [1, 1, 2, 3, 5],
    }
    assert cm.catalog_stream_exists([2, 3, 5], catalog) == "A000040"
    assert cm.catalog_stream_exists([2, 3, 5], catalog, exclude_seq="A000040") is None


def test_compose_index_primes_at_fib_positions_mock_oeis():
    cm = _load_compose()
    base_idx = {1: 2, 2: 3, 3: 5, 4: 7, 5: 11, 6: 13, 7: 17, 8: 19}
    stream = cm.compose_index_stream(base_idx, [1, 1, 2, 3, 5, 8])
    assert stream == [2, 2, 3, 5, 11, 19]

    spec = cm.ComposeSpec(
        composite_id="idx|A000040|sequence|A000045",
        base_seq_id="A000040",
        selector_kind="sequence",
        selector_ref="A000045",
        selector_start=None,
        stream=stream,
    )
    om_path = Path(__file__).resolve().parents[1] / "tools" / "oeis_match.py"
    om_spec = importlib.util.spec_from_file_location("oeis_match", om_path)
    om = importlib.util.module_from_spec(om_spec)
    sys.modules["oeis_match"] = om
    om_spec.loader.exec_module(om)

    fake_payload = {
        "results": [
            {
                "number": 30426,
                "offset": "1",
                "data": "2,2,3,5,11,19,41,73,127,211",
                "name": "Primes at positions indexed by Fibonacci numbers.",
            }
        ],
        "prefix": stream[:8],
        "prefix_hash": om.prefix_hash(stream[:8]),
    }
    ranked = om.score_hits(stream[:8], fake_payload["results"], orbit_start=stream[0])
    assert ranked[0]["oeis_id"] == "A030426"
    assert ranked[0]["match_kind"] == "identification"
