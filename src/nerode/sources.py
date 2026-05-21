"""
nerode.sources — Pre-built Source adapters for common public APIs.

All sources are key-free and use only stdlib urllib.  Each class implements
the nerode.adapters.Source protocol (.fetch() → JSON-serialisable value).

    WeatherSource(lat, lon, variables=...)   open-meteo current conditions
    TickerSource(symbol)                     Yahoo Finance quote (unofficial)
    MultiTickerSource(symbols)               multiple tickers in one fetch
    HNSource(n, tags=...)                    Hacker News via Algolia search API

Usage with Precacher:

    from nerode.sources import WeatherSource, TickerSource, HNSource
    from nerode.precache import Precacher
    from datetime import date

    today = date.today().isoformat()

    with Precacher(f"morning-brief-{today}") as pc:
        pc.fetch(f"weather:london:{today}",   WeatherSource(51.51, -0.12))
        pc.fetch(f"ticker:AAPL:{today}",      TickerSource("AAPL"))
        pc.fetch(f"ticker:MSFT:{today}",      TickerSource("MSFT"))
        pc.fetch(f"news:hn:top10:{today}",    HNSource(10))

    ctx = Precacher.open(pc.envelope, "model-b-001")
"""

from __future__ import annotations

from typing import Any

from nerode.adapters import HttpSource

# ---------------------------------------------------------------------------
# WeatherSource — open-meteo.com (no key, free)
# ---------------------------------------------------------------------------

# WMO weather interpretation codes → short descriptions
_WMO: dict[int, str] = {
    0: "clear sky",
    1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "icy fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow",
    80: "light showers", 81: "showers", 82: "violent showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm",
}

_DEFAULT_VARS = [
    "temperature_2m",
    "apparent_temperature",
    "wind_speed_10m",
    "precipitation",
    "weather_code",
]


class WeatherSource:
    """Fetch current weather conditions from open-meteo.com.

    No API key required.  Resolution is ~1 km; updates every 15 minutes.

    Args:
        lat, lon:    WGS-84 coordinates.
        variables:   open-meteo 'current' variable names.  Defaults to
                     temperature, apparent temperature, wind, precipitation,
                     and WMO weather code.
        label:       Optional human-readable location name embedded in the
                     result dict (not used for API routing).
    """

    def __init__(
        self,
        lat: float,
        lon: float,
        *,
        variables: list[str] | None = None,
        label: str | None = None,
    ) -> None:
        self.lat = lat
        self.lon = lon
        self.variables = variables or _DEFAULT_VARS
        self.label = label

    def fetch(self) -> dict[str, Any]:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={self.lat}&longitude={self.lon}"
            f"&current={','.join(self.variables)}"
            "&forecast_days=1"
        )
        raw = HttpSource(url).fetch()
        current = raw.get("current", {})

        result: dict[str, Any] = {
            "time":     current.get("time"),
            "timezone": raw.get("timezone_abbreviation", "UTC"),
        }
        if self.label:
            result["location"] = self.label

        for var in self.variables:
            if var in current:
                result[var] = current[var]

        # Annotate WMO code with human-readable description
        code = current.get("weather_code")
        if code is not None:
            result["condition"] = _WMO.get(int(code), f"wmo_{code}")

        return result


# ---------------------------------------------------------------------------
# TickerSource — Yahoo Finance unofficial v8 chart API (no key)
# ---------------------------------------------------------------------------

_TICKER_FIELDS = [
    "symbol",
    "regularMarketPrice",
    "previousClose",
    "regularMarketChangePercent",
    "regularMarketVolume",
    "fiftyTwoWeekHigh",
    "fiftyTwoWeekLow",
    "currency",
    "exchangeName",
    "marketState",
    "regularMarketTime",
]


class TickerSource:
    """Fetch a single equity quote from Yahoo Finance (unofficial API).

    No API key required.  Data is delayed ~15 minutes outside market hours.
    Yahoo may rate-limit aggressive polling — use with_retry(retries=2).

    Args:
        symbol:    Ticker symbol, e.g. "AAPL", "MSFT", "^GSPC".
        interval:  Chart bar size; '1d' is sufficient for a quote snapshot.
        range_:    Lookback period; '1d' returns today's data.
    """

    _HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; nerode/0.1)"}

    def __init__(
        self,
        symbol: str,
        *,
        interval: str = "1d",
        range_: str = "1d",
    ) -> None:
        self.symbol  = symbol.upper()
        self.interval = interval
        self.range_   = range_

    def fetch(self) -> dict[str, Any]:
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{self.symbol}"
            f"?interval={self.interval}&range={self.range_}"
        )
        try:
            raw = HttpSource(url, headers=self._HEADERS).fetch()
        except RuntimeError as exc:
            raise RuntimeError(
                f"Yahoo Finance returned no data for {self.symbol!r}: {exc}"
            ) from exc
        results = raw.get("chart", {}).get("result") or []
        if not results:
            error = raw.get("chart", {}).get("error", {})
            raise RuntimeError(
                f"Yahoo Finance returned no data for {self.symbol!r}: {error}"
            )
        meta = results[0].get("meta", {})
        return {k: meta[k] for k in _TICKER_FIELDS if k in meta}


class MultiTickerSource:
    """Fetch multiple tickers and return a dict keyed by symbol.

    Args:
        symbols:  Sequence of ticker symbols, e.g. ["AAPL", "MSFT", "GOOG"].
        **kwargs: Forwarded to each TickerSource (interval, range_).

    Returns a dict ``{"AAPL": {...}, "MSFT": {...}, ...}``.
    """

    def __init__(self, symbols: list[str], **kwargs) -> None:
        self._sources = {s.upper(): TickerSource(s, **kwargs) for s in symbols}

    def fetch(self) -> dict[str, dict[str, Any]]:
        return {sym: src.fetch() for sym, src in self._sources.items()}


# ---------------------------------------------------------------------------
# TickerHistorySource — daily OHLCV + metric direction encoding
# ---------------------------------------------------------------------------

_DIRECTION_THRESHOLD = 0.001  # 0.1% change treated as stable (S)


class TickerHistorySource:
    """Fetch daily closing prices and encode direction as metric symbols {U,D,S}.

    Returns a list of dicts ordered oldest→newest:
        [{"date": "2026-05-15", "close": 213.45, "direction": "U"}, ...]

    The first entry always has direction="S" (no prior day to compare).
    Subsequent entries: U if close rose >0.1%, D if fell >0.1%, S otherwise.

    Args:
        symbol:  Ticker symbol, e.g. "AAPL".
        range_:  Yahoo Finance lookback period.  "5d" gives the past 5 trading days.
    """

    _HEADERS = TickerSource._HEADERS

    def __init__(self, symbol: str, *, range_: str = "5d") -> None:
        self.symbol = symbol.upper()
        self.range_ = range_

    def fetch(self) -> list[dict[str, Any]]:
        from datetime import datetime as _dt

        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{self.symbol}"
            f"?interval=1d&range={self.range_}"
        )
        try:
            raw = HttpSource(url, headers=self._HEADERS).fetch()
        except RuntimeError as exc:
            raise RuntimeError(
                f"Yahoo Finance returned no history for {self.symbol!r}: {exc}"
            ) from exc

        results = raw.get("chart", {}).get("result") or []
        if not results:
            raise RuntimeError(f"Yahoo Finance returned no history for {self.symbol!r}")

        r = results[0]
        timestamps = r.get("timestamp") or []
        closes = r.get("indicators", {}).get("quote", [{}])[0].get("close") or []

        out: list[dict[str, Any]] = []
        for _i, (ts, close) in enumerate(zip(timestamps, closes, strict=False)):
            if close is None:
                continue
            day = _dt.utcfromtimestamp(ts).date().isoformat()
            if not out:
                direction = "S"
            else:
                prev = out[-1]["close"]
                change = (close - prev) / prev if prev else 0.0
                if change > _DIRECTION_THRESHOLD:
                    direction = "U"
                elif change < -_DIRECTION_THRESHOLD:
                    direction = "D"
                else:
                    direction = "S"
            out.append({"date": day, "close": round(close, 2), "direction": direction})

        if not out:
            raise RuntimeError(f"No OHLCV data returned for {self.symbol!r}")
        return out


# ---------------------------------------------------------------------------
# HNSource — Hacker News via Algolia search API (no key, free)
# ---------------------------------------------------------------------------

class HNSource:
    """Fetch top Hacker News stories via the Algolia HN search API.

    No API key required.

    Args:
        n:     Number of stories to return (max ~100 per request).
        tags:  Algolia filter tag.  Common values:
                 'front_page'  — current front page
                 'top_story'   — all-time top
                 'new_story'   — newest
                 'ask_hn'      — Ask HN posts
                 'show_hn'     — Show HN posts
    """

    _URL = "https://hn.algolia.com/api/v1/search"

    def __init__(self, n: int = 10, *, tags: str = "front_page") -> None:
        self.n    = n
        self.tags = tags

    def fetch(self) -> list[dict[str, Any]]:
        url = f"{self._URL}?tags={self.tags}&hitsPerPage={self.n}"
        raw = HttpSource(url).fetch()
        return [
            {
                "id":     h["objectID"],
                "title":  h.get("title") or h.get("story_title", ""),
                "url":    h.get("url"),
                "score":  h.get("points"),
                "author": h.get("author"),
                "comments": h.get("num_comments", 0),
            }
            for h in raw.get("hits", [])
        ]
