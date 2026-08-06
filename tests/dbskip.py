from __future__ import annotations

import psycopg
import pytest

# Cache per-DSN reachability so a down database only pays the connect_timeout
# once. Without this, tests that call connect_or_skip many times per test (or
# across many tests) each retry the full timeout, turning a single unreachable
# DB into a multi-minute hang.
_unreachable: dict[str, str] = {}


def connect_or_skip(dsn: str, *, autocommit: bool = False) -> psycopg.Connection:
    if dsn in _unreachable:
        pytest.skip(f"database not reachable: {_unreachable[dsn]}")
    try:
        return psycopg.connect(dsn, autocommit=autocommit, connect_timeout=3)
    except psycopg.Error as exc:
        _unreachable[dsn] = str(exc)
        pytest.skip(f"database not reachable: {exc}")
