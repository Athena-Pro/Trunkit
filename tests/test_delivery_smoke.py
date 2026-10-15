"""Run the public installed-package smoke workflows on dedicated test DBs."""

from calx.smoke import check_nerode, check_trunkit


def test_trunkit_public_smoke(calx_conn):
    check_trunkit(calx_conn.info.dsn)


def test_nerode_public_smoke(conn):
    counts_sql = (
        "SELECT (SELECT count(*) FROM cert.claim),"
        " (SELECT count(*) FROM cert.certificate),"
        " (SELECT count(*) FROM nerode.sequence_cache),"
        " (SELECT count(*) FROM nerode.automata)"
    )
    before = conn.execute(counts_sql).fetchone()
    check_nerode(conn.info.dsn)
    assert conn.execute(counts_sql).fetchone() == before
