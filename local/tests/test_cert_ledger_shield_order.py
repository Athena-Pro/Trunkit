"""local/tests/test_cert_ledger_shield_order.py
==============================================
Regression: the exact-domain shield must fire BEFORE the ledger hash trigger.

Lives beside the layer it guards: the hash chain is a local overlay
(local/sql/95_cert_ledger.sql), while the shield is packaged
(src/calx/sql/94_cert_exactness.sql). The defect below is precisely a
cross-layer interaction, which is why neither layer's own tests caught it.

The defect this pins (observed on the canonical ledger 2026-07-25):

  * ``cert_certificate_hash``  (local/sql/95_cert_ledger.sql) computes
    ``NEW.row_hash`` over ``status`` + ``evidence``;
  * ``exactness_shield_trg``   (src/calx/sql/94_cert_exactness.sql) rewrites
    ``NEW.status`` 'valid' -> 'unverified' and appends ``{shield,
    original_status}`` to ``NEW.evidence``.

PostgreSQL fires BEFORE triggers in ALPHABETICAL name order, and
``cert_certificate_hash`` < ``exactness_shield_trg``. So the row was hashed and
only then rewritten: the stored ``row_hash`` committed to content that never
reached disk, and ``cert.verify_chain()`` reported

    content hash mismatch at certificate id N (row altered)

for every float_heuristic claim whose probe returned TRUE -- accusing tampering
where there was none. Because ``cert.certificate`` is append-only, such a row
can never be repaired; only prevented. Fixed at source in
``src/calx/sql/94_cert_exactness.sql``, which installs the shield as
``aa_exactness_shield_trg`` so it sorts first;
``local/sql/95a_cert_ledger_shield_order.sql`` asserts the ordering after the
overlay is applied, so a re-apply from an unpatched checkout fails loudly.

Write-heavy: refuses to run without a dedicated test DSN (same guard as
tests/test_cert_lifecycle.py) -- certificate appends cannot be cleaned up.
The content assertion is deliberately LOCAL to the row this test writes, so it
still passes on a ledger that carries historical breaks from before the fix.
"""

from __future__ import annotations

import json
import os
import uuid

import psycopg
import pytest


def _test_dsn() -> str:
    dsn = os.environ.get("CALX_TEST_DSN") or os.environ.get("ARITHMETIC_DB_TEST_DSN")
    if not dsn:
        pytest.skip("No test DSN provided. Refusing to write to default/production ledger.")
    return dsn


@pytest.fixture()
def conn():
    try:
        c = psycopg.connect(_test_dsn(), connect_timeout=3)
    except psycopg.Error as exc:
        pytest.skip(f"test DB not reachable: {exc}")
    try:
        with c.cursor() as cur:
            cur.execute("SELECT to_regclass('cert.certificate')")
            if cur.fetchone()[0] is None:
                pytest.skip("cert.certificate missing — apply the core schema first")
            cur.execute("SELECT to_regprocedure('cert.verify_chain()')")
            if cur.fetchone()[0] is None:
                pytest.skip("cert.verify_chain missing — apply local/sql/95_cert_ledger.sql first")
        yield c
    finally:
        c.close()


def _before_insert_triggers(conn) -> list[str]:
    """BEFORE INSERT row triggers on cert.certificate, in firing order.

    PostgreSQL fires them alphabetically by name, so sorted order IS firing
    order. (tgtype bit 0x04 = BEFORE-ish row trigger; 0x08 = INSERT.)
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT tgname FROM pg_trigger
                WHERE tgrelid = 'cert.certificate'::regclass
                  AND NOT tgisinternal
                  AND (tgtype::int & 4) = 4      -- BEFORE
                  AND (tgtype::int & 4) <> 0
                ORDER BY tgname""")
        return [r[0] for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Structural: ordering is what actually prevents the defect
# ---------------------------------------------------------------------------

def test_shield_trigger_sorts_before_hash_trigger(conn):
    names = _before_insert_triggers(conn)
    shields = [n for n in names if "exactness_shield" in n]
    hashes = [n for n in names if n == "cert_certificate_hash"]
    if not hashes:
        pytest.skip("ledger hash trigger not installed")
    assert shields, "exactness shield trigger is not installed on cert.certificate"
    # At least one shield must fire before the hash. A stale post-hash copy of
    # the shield is harmless (it no-ops once status is already 'unverified'),
    # so we require the *earliest* shield to precede the hash.
    assert min(shields) < hashes[0], (
        f"exactness shield fires AFTER the hash trigger: {names}. "
        f"Re-apply the patched src/calx/sql/94_cert_exactness.sql."
    )


# ---------------------------------------------------------------------------
# Behavioural: a shielded certificate must still hash to its own content
# ---------------------------------------------------------------------------

def test_shielded_certificate_content_hash_reproduces(conn):
    stmt = f"shield/hash order regression {uuid.uuid4()}"
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO cert.claim
                   (subject_kind, subject_ref, statement, claim_kind, method,
                    domain, probe_sql)
               VALUES ('regression', %s, %s, 'computational', 'comp_sql',
                       'float_heuristic',
                       'SELECT TRUE AS ok, jsonb_build_object(''cosine'', 0.97) AS evidence')
               RETURNING id""",
            (json.dumps({"test": "shield_order"}), stmt))
        claim_id = cur.fetchone()[0]
        conn.commit()

        cur.execute("SELECT status FROM cert.check(%s)", (claim_id,))
        status = cur.fetchone()[0]
        conn.commit()

        # The shield must still do its job.
        assert status == "unverified", (
            "exactness shield failed to downgrade a float_heuristic verdict")

        cur.execute(
            """SELECT id, evidence ? 'shield',
                      row_hash = cert.certificate_row_hash(
                          claim_id, seq, status, evidence, valid_under,
                          (SELECT row_hash FROM curry.inferences
                            WHERE inference_id = c.checker_inference_id),
                          prev_hash, premise_hashes)
                 FROM cert.certificate c
                WHERE claim_id = %s
                ORDER BY seq DESC LIMIT 1""",
            (claim_id,))
        cert_id, has_shield, content_ok = cur.fetchone()

    assert has_shield, "shield did not stamp its evidence key"
    assert content_ok, (
        f"certificate {cert_id} does not hash to its own stored content — the "
        f"shield mutated the row after cert_certificate_hash committed to it. "
        f"Re-apply the patched src/calx/sql/94_cert_exactness.sql."
    )


def test_exact_domain_claim_still_reads_valid(conn):
    """The fix must not blunt the shield's counterpart: exact claims still pass."""
    stmt = f"shield/hash order control {uuid.uuid4()}"
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO cert.claim
                   (subject_kind, subject_ref, statement, claim_kind, method,
                    domain, probe_sql)
               VALUES ('regression', '{}'::jsonb, %s, 'computational', 'comp_sql',
                       'exact_int',
                       'SELECT TRUE AS ok, ''{}''::jsonb AS evidence')
               RETURNING id""",
            (stmt,))
        claim_id = cur.fetchone()[0]
        conn.commit()
        cur.execute("SELECT status FROM cert.check(%s)", (claim_id,))
        status = cur.fetchone()[0]
        conn.commit()
    assert status == "valid"
