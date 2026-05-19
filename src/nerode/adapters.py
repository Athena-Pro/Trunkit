"""
nerode.adapters — Source adapters for Precacher.fetch().

A *source* is anything that resolves to a JSON-serialisable value.
Three forms are accepted:

    callable   sync or async function called with no arguments
    str        HTTP GET URL; response body parsed as JSON
    Source     any object with a .fetch() → Any method

The top-level helpers are:

    resolve(source)                      → value  (single attempt)
    with_retry(source, retries, backoff) → value  (retry on exception)

Built-in source classes:

    HttpSource(url, *, headers, params, method, timeout)
    CallableSource(fn, *, args, kwargs)

Async callables are handled via asyncio.run().  If you are already inside
a running event loop (Jupyter, FastAPI), await the coroutine yourself and
pass the result directly to Precacher.store().
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Source protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class Source(Protocol):
    def fetch(self) -> Any: ...


# ---------------------------------------------------------------------------
# HttpSource
# ---------------------------------------------------------------------------

class HttpSource:
    """Fetch a JSON resource over HTTP/HTTPS using only stdlib urllib."""

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        method: str = "GET",
        timeout: float = 10.0,
    ) -> None:
        self.url = url
        self.headers = headers or {}
        self.params = params or {}
        self.method = method.upper()
        self.timeout = timeout

    def _full_url(self) -> str:
        if not self.params:
            return self.url
        sep = "&" if "?" in self.url else "?"
        return self.url + sep + urllib.parse.urlencode(self.params)

    def fetch(self) -> Any:
        req = urllib.request.Request(
            self._full_url(),
            headers=self.headers,
            method=self.method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"HTTP {exc.code} fetching {self.url}: {exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"URL error fetching {self.url}: {exc.reason}"
            ) from exc


# ---------------------------------------------------------------------------
# CallableSource
# ---------------------------------------------------------------------------

class CallableSource:
    """Wrap a sync or async callable, called with optional args/kwargs."""

    def __init__(
        self,
        fn,
        *,
        args: tuple = (),
        kwargs: dict | None = None,
    ) -> None:
        self._fn = fn
        self._args = args
        self._kwargs = kwargs or {}

    def fetch(self) -> Any:
        if inspect.iscoroutinefunction(self._fn):
            try:
                return asyncio.run(self._fn(*self._args, **self._kwargs))
            except RuntimeError as exc:
                if "cannot be called when another event loop" in str(exc):
                    raise RuntimeError(
                        "Cannot use an async callable inside a running event loop. "
                        "Await the coroutine yourself and pass the result to "
                        "Precacher.store() directly."
                    ) from exc
                raise
        return self._fn(*self._args, **self._kwargs)


# ---------------------------------------------------------------------------
# resolve() — normalise any source form to a value
# ---------------------------------------------------------------------------

def resolve(source: Any) -> Any:
    """Resolve a source spec to a JSON-serialisable value.

    Accepts:
      Source instance  — calls .fetch()
      callable         — wraps in CallableSource and calls .fetch()
      str              — treats as HTTP GET URL via HttpSource
    """
    if isinstance(source, Source):
        return source.fetch()
    if callable(source):
        return CallableSource(source).fetch()
    if isinstance(source, str):
        return HttpSource(source).fetch()
    raise TypeError(
        f"Cannot resolve source of type {type(source).__name__}. "
        "Pass a callable, a URL string, or a Source instance."
    )


# ---------------------------------------------------------------------------
# with_retry() — retry wrapper around resolve()
# ---------------------------------------------------------------------------

def with_retry(
    source: Any,
    *,
    retries: int = 2,
    backoff: float = 1.0,
) -> Any:
    """Resolve *source*, retrying up to *retries* times on exception.

    Backoff between attempts is backoff * 2^attempt seconds
    (1 s, 2 s, 4 s, … by default).
    """
    last_exc: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            return resolve(source)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt))
    raise last_exc  # type: ignore[misc]
