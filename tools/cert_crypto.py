"""Cryptographic-tier harness for the cert pillar (arXiv:2606.23768).

Drives `crypto_succinct` claims. For each known crypto claim it:
  1. builds the agreed predicate phi and the prover's interpretation s (witness),
  2. recomputes the polynomial residual [phi]_s via calx.arith (the arith_recheck
     path -- the compiler-correctness condition, no crypto back end required),
  3. registers the Cert tuple (cert.register_crypto_artifact) with the claim's
     declared private/public symbols,
  4. appends a certificate through the SAME append-only cert.certificate +
     curry.inferences provenance that cert.check / cert_formal use, and
  5. stores an `arith_constraint` witness carrying the residual so a consumer can
     re-verify with cert.verify_crypto (or, offline, calx.arith) without trusting
     the producer.

Before attesting more than one claim it runs the compartmentalisation invariant
(cert.bundle_admits / calx.arith.bundle_admits): a combined bundle is refused if
two sealed methods share a private witness register (I1) or a symbol is private
in one claim and public in another (I2).

Trust model mirrors the formal tier: idempotent, append-only; a real deployment
would additionally shell out to the backend `verifier_cmd` (Halo2/Zinc) to check
the succinct proof pi. That step is intentionally left as the backend boundary.

The module imports DB-free (psycopg is loaded lazily) so the arithmetisation and
admission logic can be exercised without a database. SQL: src/calx/sql/97_cert_crypto.sql.

Run:  CALX_DSN=... python tools/cert_crypto.py [--write]
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))

from calx import arith  # noqa: E402  (needs src on path, set above)
from calx.arith import Const, Divides, Interp, bundle_admits, evaluate  # noqa: E402

PG_DSN = os.environ.get("CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk")


def _div_predicate():
    """7 | 28  ==  exists q. 28 = 7*q, with private quotient register q (= 4)."""
    q = arith.Lookup("q", 1, Const(1))
    phi = Divides(Const(7), Const(28), q)
    s = Interp({"q": [[4]]})
    return phi, s


# Known crypto claims: statement (seeded in 97_cert_crypto.sql) -> spec.
CRYPTO_CLAIMS = {
    "7 divides 28 (quotient-witnessed, crypto tier)": {
        "build": _div_predicate,
        "policy_id": "divides@v1",
        "backend": "halo2",
        "pub": {"d": 7, "n": 28},
        "zero_knowledge": False,
        "private_registers": ["q"],
        "public_symbols": ["d", "n"],
    },
}


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def append_certificate(cur, claim_id, status, evidence, valid_under, statement):
    """Append-only certificate + curry.inferences provenance (cf. cert_formal)."""
    from psycopg.types.json import Jsonb

    inf_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO curry.inferences "
        "(inference_id, model_name, model_version, input_tokens, "
        " output_tokens, temperature_used, seed, metadata) "
        "VALUES (%s,'cert-checker-model',1,%s,%s,0.0,0,%s)",
        (inf_id, json.dumps({"claim_id": claim_id, "statement": statement}),
         status.encode("utf-8"),
         Jsonb({"tier": "cryptographic", "kind": "crypto_succinct"})),
    )
    cur.execute(
        "SELECT COALESCE(MAX(seq),0)+1 FROM cert.certificate WHERE claim_id=%s",
        (claim_id,),
    )
    seq = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cert.certificate "
        "(claim_id, seq, status, evidence, valid_under, checker_inference_id) "
        "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
        (claim_id, seq, status, Jsonb(evidence), Jsonb(valid_under), inf_id),
    )
    return seq, cur.fetchone()[0]


def attest_claim(cur, claim_id, statement, spec, write):
    """Compute the arith verdict for one crypto claim and (optionally) record it."""
    from psycopg.types.json import Jsonb

    phi, s = spec["build"]()
    r = evaluate(phi, s)
    status = "valid" if r == 0 else "refuted"
    evidence = {"policy_id": spec["policy_id"], "backend": spec["backend"],
                "residual": str(r), "recheck": "arithmetisation [phi]_s(x) vanishing"}
    valid_under = {"arith_module": "calx.arith", "paper": "arXiv:2606.23768"}
    witness_body = {"policy_id": spec["policy_id"], "residual": str(r),
                    "interpretation": s.m}
    print(f"  {statement!r}\n    residual={r}  ->  {status.upper()}")
    if not write:
        return status

    cur.execute(
        "SELECT cert.register_crypto_artifact(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (claim_id, spec["policy_id"], spec["backend"],
         _hash(["vk", spec["policy_id"]]), _hash(["params", spec["backend"]]),
         Jsonb(spec["pub"]), f'"{sys.executable}" -m calx.arith --verify',
         spec["zero_knowledge"], spec["private_registers"], spec["public_symbols"]),
    )
    seq, cert_id = append_certificate(cur, claim_id, status, evidence,
                                      valid_under, statement)
    cur.execute(
        "INSERT INTO cert.witness (certificate_id, kind, body, schema_version) "
        "VALUES (%s,'arith_constraint',%s,%s)",
        (cert_id, Jsonb(witness_body), Jsonb({"arith": 1})),
    )
    print(f"    recorded certificate seq={seq} (+ arith_constraint witness)")
    return status


def check_bundle_admission():
    """Run the compartmentalisation invariant over all known crypto claims."""
    claims = [{"name": stmt, "private": set(spec["private_registers"]),
               "public": set(spec["public_symbols"])}
              for stmt, spec in CRYPTO_CLAIMS.items()]
    admit, viol = bundle_admits(claims)
    print(f"bundle admission (I1+I2): {'ADMIT' if admit else 'REFUSE'}")
    for v in viol:
        print(f"    - {v[0]}: symbol {v[1]!r} ({v[2]} vs {v[3]})")
    return admit


def main(write: bool) -> int:
    import psycopg

    if not check_bundle_admission():
        print("Refusing to attest: bundle violates compartmentalisation invariant.")
        return 1
    conn = psycopg.connect(PG_DSN)
    try:
        with conn.cursor() as cur:
            for statement, spec in CRYPTO_CLAIMS.items():
                cur.execute("SELECT id FROM cert.claim WHERE statement=%s",
                            (statement,))
                row = cur.fetchone()
                if row is None:
                    print(f"  (claim not seeded, skipping): {statement!r}")
                    continue
                attest_claim(cur, row[0], statement, spec, write)
        if write:
            conn.commit()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(write="--write" in sys.argv))
