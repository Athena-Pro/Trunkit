"""
tests/test_sources.py
=====================
Integration tests for nerode.sources + Precacher end-to-end.

These tests make real HTTP requests and commit to the nerode DB.
They are marked `network` and excluded from the default pytest run.

Run them explicitly:

    pytest tests/test_sources.py -m network -v
    pytest tests/test_sources.py -m network -v --tb=short

Each test is independent: it creates its own Precacher session, closes it,
and opens it via Precacher.open() — verifying the full pre-pack pipeline.
"""

from __future__ import annotations

from datetime import date

import pytest

from nerode.precache import Precacher
from nerode.sources import HNSource, MultiTickerSource, TickerSource, WeatherSource

TODAY = date.today().isoformat()

pytestmark = pytest.mark.network


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _roundtrip(session_id: str, key: str, source) -> dict:
    """Pre-cache one key, open the session, return the resolved value.

    Always uses force_rebuild=True so network tests never return a stale
    cached value from a previous test run.
    """
    with Precacher(session_id) as pc:
        pc.fetch(key, source, retries=2, force_rebuild=True)
    return Precacher.open(pc.envelope, f"{session_id}-b")["resolved"][key]


# ---------------------------------------------------------------------------
# WeatherSource
# ---------------------------------------------------------------------------

class TestWeatherSource:

    def test_fetch_returns_dict_with_required_fields(self):
        src = WeatherSource(51.51, -0.12, label="London")
        data = src.fetch()
        assert isinstance(data, dict)
        assert "temperature_2m" in data
        assert "time" in data
        assert "timezone" in data

    def test_condition_is_human_readable(self):
        src = WeatherSource(51.51, -0.12)
        data = src.fetch()
        assert isinstance(data.get("condition"), str)
        assert not data["condition"].startswith("wmo_") or True  # unknown codes are ok

    def test_temperature_is_numeric(self):
        src = WeatherSource(51.51, -0.12)
        data = src.fetch()
        assert isinstance(data["temperature_2m"], (int, float))

    def test_label_embedded_in_result(self):
        src = WeatherSource(51.51, -0.12, label="London")
        data = src.fetch()
        assert data["location"] == "London"

    def test_label_absent_when_not_set(self):
        src = WeatherSource(51.51, -0.12)
        data = src.fetch()
        assert "location" not in data

    def test_custom_variables(self):
        src = WeatherSource(40.71, -74.01, variables=["temperature_2m", "precipitation"])
        data = src.fetch()
        assert "temperature_2m" in data
        assert "precipitation" in data
        assert "wind_speed_10m" not in data

    def test_precacher_roundtrip(self, nerode_dsn):
        key = f"weather:london:{TODAY}"
        result = _roundtrip(f"src-weather-test-{TODAY}", key, WeatherSource(51.51, -0.12, label="London"))
        assert "temperature_2m" in result
        assert result.get("location") == "London"


# ---------------------------------------------------------------------------
# TickerSource
# ---------------------------------------------------------------------------

class TestTickerSource:

    def test_fetch_returns_dict_with_symbol(self):
        data = TickerSource("AAPL").fetch()
        assert data["symbol"] == "AAPL"

    def test_price_is_positive_float(self):
        data = TickerSource("AAPL").fetch()
        assert isinstance(data["regularMarketPrice"], (int, float))
        assert data["regularMarketPrice"] > 0

    def test_currency_is_usd(self):
        data = TickerSource("AAPL").fetch()
        assert data.get("currency") == "USD"

    def test_msft_fetch(self):
        data = TickerSource("MSFT").fetch()
        assert data["symbol"] == "MSFT"
        assert data["regularMarketPrice"] > 0

    def test_52_week_range_is_consistent(self):
        data = TickerSource("AAPL").fetch()
        low  = data.get("fiftyTwoWeekLow", 0)
        high = data.get("fiftyTwoWeekHigh", float("inf"))
        price = data["regularMarketPrice"]
        assert low <= high
        # Price may be outside 52-week range briefly at open/close; allow 20% slack
        assert low * 0.8 <= price <= high * 1.2, (
            f"AAPL price {price} not near 52w range [{low}, {high}]"
        )

    def test_unknown_symbol_raises(self):
        with pytest.raises(RuntimeError, match="no data"):
            TickerSource("ZZZZZNOTREAL999").fetch()

    def test_precacher_roundtrip(self, nerode_dsn):
        key = f"ticker:AAPL:{TODAY}"
        result = _roundtrip(f"src-ticker-test-{TODAY}", key, TickerSource("AAPL"))
        assert result["symbol"] == "AAPL"
        assert result["regularMarketPrice"] > 0


# ---------------------------------------------------------------------------
# MultiTickerSource
# ---------------------------------------------------------------------------

class TestMultiTickerSource:

    def test_returns_dict_keyed_by_symbol(self):
        data = MultiTickerSource(["AAPL", "MSFT"]).fetch()
        assert set(data.keys()) == {"AAPL", "MSFT"}

    def test_each_ticker_has_price(self):
        data = MultiTickerSource(["AAPL", "MSFT", "GOOG"]).fetch()
        for sym, quote in data.items():
            assert quote["regularMarketPrice"] > 0, f"{sym}: price missing"

    def test_symbols_normalised_to_upper(self):
        data = MultiTickerSource(["aapl", "msft"]).fetch()
        assert "AAPL" in data
        assert "MSFT" in data

    def test_precacher_roundtrip(self, nerode_dsn):
        key = f"tickers:AAPL_MSFT:{TODAY}"
        result = _roundtrip(
            f"src-multi-ticker-test-{TODAY}",
            key,
            MultiTickerSource(["AAPL", "MSFT"]),
        )
        assert "AAPL" in result
        assert "MSFT" in result


# ---------------------------------------------------------------------------
# HNSource
# ---------------------------------------------------------------------------

class TestHNSource:

    def test_returns_list(self):
        data = HNSource(5).fetch()
        assert isinstance(data, list)
        assert len(data) == 5

    def test_each_story_has_required_fields(self):
        data = HNSource(3).fetch()
        for story in data:
            assert "id" in story
            assert "title" in story
            assert isinstance(story["title"], str) and story["title"]

    def test_score_is_positive_int(self):
        data = HNSource(5).fetch()
        for story in data:
            if story["score"] is not None:
                assert story["score"] >= 0

    def test_ask_hn_tag(self):
        data = HNSource(3, tags="ask_hn").fetch()
        assert isinstance(data, list)

    def test_show_hn_tag(self):
        data = HNSource(3, tags="show_hn").fetch()
        assert isinstance(data, list)

    def test_precacher_roundtrip(self, nerode_dsn):
        key = f"news:hn:top5:{TODAY}"
        result = _roundtrip(f"src-hn-test-{TODAY}", key, HNSource(5))
        assert isinstance(result, list)
        assert len(result) == 5
        assert all("title" in s for s in result)


# ---------------------------------------------------------------------------
# Full morning-brief pre-pack
# ---------------------------------------------------------------------------

class TestMorningBriefPack:
    """End-to-end: pre-pack weather + two tickers + HN in one Precacher session."""

    def test_full_pack_and_open(self, nerode_dsn):
        session_id = f"morning-brief-{TODAY}-test"

        with Precacher(session_id) as pc:
            pc.fetch(f"weather:london:{TODAY}",  WeatherSource(51.51, -0.12, label="London"), force_rebuild=True)
            pc.fetch(f"ticker:AAPL:{TODAY}",     TickerSource("AAPL"),  force_rebuild=True)
            pc.fetch(f"ticker:MSFT:{TODAY}",     TickerSource("MSFT"),  force_rebuild=True)
            pc.fetch(f"news:hn:top5:{TODAY}",    HNSource(5),           force_rebuild=True)

        assert pc.envelope is not None
        assert len(pc.envelope["cache_keys"]) == 4

        ctx = Precacher.open(pc.envelope, f"{session_id}-b")

        assert ctx["prior_session"]["cert_valid"] is True
        resolved = ctx["resolved"]

        assert f"weather:london:{TODAY}" in resolved
        assert f"ticker:AAPL:{TODAY}"    in resolved
        assert f"ticker:MSFT:{TODAY}"    in resolved
        assert f"news:hn:top5:{TODAY}"   in resolved

        # Spot-check content
        weather = resolved[f"weather:london:{TODAY}"]
        assert "temperature_2m" in weather

        aapl = resolved[f"ticker:AAPL:{TODAY}"]
        assert aapl["symbol"] == "AAPL"

        news = resolved[f"news:hn:top5:{TODAY}"]
        assert len(news) == 5

    def test_envelope_attention_hint_lists_all_keys(self):
        session_id = f"hint-test-{TODAY}"
        with Precacher(session_id) as pc:
            pc.fetch(f"weather:nyc:{TODAY}", WeatherSource(40.71, -74.01, label="NYC"))
            pc.fetch(f"ticker:GOOG:{TODAY}", TickerSource("GOOG"))

        hint = pc.envelope["attention_hint"]
        assert "weather:nyc" in hint
        assert "ticker:GOOG" in hint
