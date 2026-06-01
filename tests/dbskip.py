from __future__ import annotations

import psycopg
import pytest


def connect_or_skip(dsn: str, *, autocommit: bool = False) -> psycopg.Connection:
    try:
        return psycopg.connect(dsn, autocommit=autocommit, connect_timeout=3)
    except psycopg.Error as exc:
        pytest.skip(f"database not reachable: {exc}")
