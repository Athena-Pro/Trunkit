"""Run the public installed-package smoke workflows on dedicated test DBs."""

from calx.smoke import check_nerode, check_trunkit


def test_trunkit_public_smoke(calx_conn, calx_dsn):
    check_trunkit(calx_dsn)


def test_nerode_public_smoke(conn, nerode_dsn):
    counts_sql = (
        "SELECT (SELECT count(*) FROM cert.claim),"
        " (SELECT count(*) FROM cert.certificate),"
        " (SELECT count(*) FROM nerode.sequence_cache),"
        " (SELECT count(*) FROM nerode.automata)"
    )
    before = conn.execute(counts_sql).fetchone()
    check_nerode(nerode_dsn)
    assert conn.execute(counts_sql).fetchone() == before
