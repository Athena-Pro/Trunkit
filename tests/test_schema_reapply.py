"""Full schema replay must preserve lifecycle view contracts and dependents."""

import uuid

from calx import db


def test_unified_reapply_preserves_lifecycle_and_dependent_views(calx_dsn):
    with db.connect(calx_dsn) as conn:
        try:
            db.apply_unified(conn)
            standing_oid = conn.execute("SELECT 'cert.standing'::regclass::oid").fetchone()[0]
            conn.execute(
                "CREATE TEMP VIEW reapply_standing AS "
                "SELECT claim_id, status, effective_status, revoked_at, valid_until, signer_id "
                "FROM cert.standing"
            )
            claims = {}
            for state in ("valid", "revoked", "expired"):
                claim_id = conn.execute(
                    "INSERT INTO cert.claim "
                    "(subject_kind, subject_ref, statement, claim_kind, method, probe_sql) "
                    "VALUES ('reapply_test', '{}'::jsonb, %s, 'computational', 'comp_sql', "
                    "'SELECT TRUE, ''{}''::jsonb') RETURNING id",
                    (f"schema reapply {state} {uuid.uuid4()}",),
                ).fetchone()[0]
                ttl = "-1 hour" if state == "expired" else ""
                conn.execute("SELECT set_config('trunkit.cert_ttl', %s, true)", (ttl,))
                conn.execute("SELECT (cert.check(%s)).id", (claim_id,))
                if state == "revoked":
                    conn.execute(
                        "SELECT cert.revoke_claim(%s, 'schema reapply regression')", (claim_id,),
                    )
                claims[claim_id] = state

            for _ in range(2):
                db.apply_unified(conn)
                assert conn.execute(
                    "SELECT 'cert.standing'::regclass::oid"
                ).fetchone()[0] == standing_oid
                rows = conn.execute(
                    "SELECT claim_id, status, effective_status FROM reapply_standing "
                    "WHERE claim_id = ANY(%s)", (list(claims),),
                ).fetchall()
                assert {claim_id: effective for claim_id, _, effective in rows} == claims
                assert all(status == "valid" for _, status, _ in rows)
                for claim_id, state in claims.items():
                    assert conn.execute(
                        "SELECT cert.effective_status(%s)", (claim_id,),
                    ).fetchone()[0] == state
                # This dependent recursive view was installed after lifecycle.
                conn.execute("SELECT count(*) FROM cert.tainted_closure").fetchone()
        finally:
            conn.rollback()
