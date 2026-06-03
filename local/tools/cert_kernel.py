#!/usr/bin/env python3
"""cert_kernel.py — untrusted-certificate attestation harness.

For every claim using the `cert_kernel` method with a submitted proof object
(cert.proof_obligation), run the independent in-DB kernel and append a
certificate. Mirrors tools/cert_formal.py, but the verdict comes from an
independent checker re-examining the proof object — not from re-running the
producer.

Usage:
    CALX_DSN=postgresql://... python tools/cert_kernel.py [--write]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
src_dir_str = str(SRC_DIR)
if src_dir_str not in sys.path:
    sys.path.insert(0, src_dir_str)

from calx.db import connect


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="record certificates")
    args = ap.parse_args()

    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT c.id, c.statement, (o.claim_id IS NOT NULL) AS has_obligation
          FROM cert.claim c
          LEFT JOIN cert.proof_obligation o ON o.claim_id = c.id
         WHERE c.method = 'cert_kernel'
         ORDER BY c.id
    """)
    rows = cur.fetchall()

    for claim_id, statement, has_obligation in rows:
        if args.write:
            cur.execute("SELECT (cert.check_kernel(%s)).status", (claim_id,))
            status = cur.fetchone()[0]
            conn.commit()
        else:
            # dry-run: re-verify the obligation without appending a certificate
            cur.execute("SELECT ok FROM cert.verify(%s)", (claim_id,))
            ok = cur.fetchone()[0]
            status = {True: "valid", False: "refuted", None: "unverified"}[ok]
        flag = {"valid": "✓", "refuted": "✗"}.get(status, "?")
        suffix = "" if has_obligation else "  (no proof object submitted)"
        print(f"[{flag} {status}] claim {claim_id}: {statement}{suffix}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
