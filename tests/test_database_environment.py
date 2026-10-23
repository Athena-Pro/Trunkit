"""External database roles must not depend on the maintainer's role settings."""
from unittest.mock import Mock

import psycopg

from calx import db, generate


def test_connection_sets_database_local_path_before_any_consumer_query(monkeypatch):
    connection = Mock()
    monkeypatch.setattr(psycopg, "connect", Mock(return_value=connection))
    with db.connect("postgresql://example.invalid/test") as actual:
        assert actual is connection
        connection.execute.assert_called_once_with(f"SET search_path = {db.SEARCH_PATH}")
    connection.commit.assert_called_once()
    connection.close.assert_called_once()


def test_qualified_arithmetic_probe_works_from_an_unconfigured_client(calx_conn):
    connection = calx_conn  # fixture provides empty arithmetic tables
    try:
        db.apply_unified(connection)
        db.apply_schema(connection)  # generate CLI's core-schema refresh
        generate.generate_pure(connection, 28)
        connection.execute("SET LOCAL search_path = pg_catalog")
        assert connection.execute("SELECT calx.aliquot_step(28)").fetchone() == (28,)
        ok, evidence = connection.execute(
            "SELECT ok, evidence FROM cert.verify(2)"
        ).fetchone()
        assert ok is True, evidence
    finally:
        connection.rollback()



def test_fresh_configured_connection_remains_idle_for_bundle_replay(calx_dsn):
    for autocommit in (False, True):
        with db.connect(calx_dsn, autocommit=autocommit) as connection:
            assert connection.autocommit is autocommit
            assert connection.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
            assert connection.execute("SHOW search_path").fetchone()[0] == db.SEARCH_PATH
