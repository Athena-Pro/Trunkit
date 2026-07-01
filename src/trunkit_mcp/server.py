"""trunkit_mcp.server — MCP server for the Trunkit proof-carrying ledger.

Consumer tools (read-only, always available):
  kernel_verify      — re-check a proof witness with the dependency-free kernel
  ledger_chain       — re-verify hash-chain entanglement in a bundle JSON string
  bundle_verify      — full bundle verification (kernel + chain + probes if DB live)
  standing           — query cert.standing (status/method filter)
  claim_verify       — run cert.verify(claim_id) against the live DB
  claim_export       — export a bundle for one or more claim_ids

Prover tools (require TRUNKIT_ALLOW_WRITE=1 in env):
  claim_check        — re-run and record a certificate
  witness_attach     — attach a kernel-checkable proof witness to a claim
  attest_run         — run the formal-tier attestation pass (cert_formal.py)

Configuration via environment variables:
  CALX_DSN              — Postgres DSN (default postgresql://trunk:trunk@localhost:5434/trunk)
  TRUNKIT_ALLOW_WRITE   — set to "1" to enable prover tools (default: off)
  TRUNKIT_SRC           — path prepended to sys.path; use to prefer local dev tree over
                          the installed PyPI version (e.g. C:\\AI-Local\\Trunk\\src)
"""
from __future__ import annotations

import json
import os
import sys

# ── Local-first import: honour TRUNKIT_SRC so the server runs against the
#    checked-out tree without reinstalling every time. ──────────────────────
_src = os.environ.get("TRUNKIT_SRC")
if _src and _src not in sys.path:
    sys.path.insert(0, _src)

import pathlib
import textwrap
import traceback
from typing import Any

from mcp.server.fastmcp import FastMCP

# ── Import trunkit layers (soft-fail for optional DB layer) ─────────────────
from calx.kernel import verify_witness, verify_bundle as kernel_verify_bundle
from calx.ledger import verify_chain

try:
    import psycopg as _psycopg  # noqa: F401
    import calx.db as _db
    import calx.bundle as _bundle_mod
    _HAS_DB = True
except ImportError:
    _HAS_DB = False
    _db = None          # type: ignore[assignment]
    _bundle_mod = None  # type: ignore[assignment]

_ALLOW_WRITE = os.environ.get("TRUNKIT_ALLOW_WRITE", "0").strip() == "1"
_DSN: str | None = os.environ.get("CALX_DSN")

# ── Server ──────────────────────────────────────────────────────────────────
mcp = FastMCP(
    "trunkit",
    instructions=textwrap.dedent("""\
        Trunkit MCP — proof-carrying ledger tools.

        Consumer tools (always available):
          kernel_verify   — re-check a proof witness locally, no DB needed
          ledger_chain    — re-verify hash entanglement in a bundle
          bundle_verify   — full bundle verification (kernel + chain + DB probes)
          standing        — query the cert ledger standing view
          claim_verify    — verify a single claim against the live DB
          claim_export    — export a portable proof bundle for claim IDs

        Prover tools (only when TRUNKIT_ALLOW_WRITE=1):
          claim_check     — re-run and record a certificate
          witness_attach  — attach a kernel-checkable proof witness
          attest_run      — run the formal attestation pass

        Three-valued verdicts everywhere: "valid" | "refuted" | "unverified".
    """),
)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _ok_label(v: bool | None) -> str:
    return "valid" if v is True else "refuted" if v is False else "unverified"


def _db_connect():
    """Return a live psycopg connection context or raise RuntimeError."""
    if not _HAS_DB:
        raise RuntimeError("psycopg not installed — DB tools unavailable")
    return _db.connect(_DSN)


# ═══════════════════════════════════════════════════════════════════════════
# Consumer tools
# ═══════════════════════════════════════════════════════════════════════════

@mcp.tool()
def kernel_verify(witness_json: str) -> dict[str, Any]:
    """Re-check a proof witness using the dependency-free kernel.

    Pass the JSON string of a witness body (the ``body`` field from a
    cert.export_bundle entry). Supported schemas: factorization, crt,
    unit_fraction, matrix_word, dfa_betti, knot_alexander.

    Returns verdict ("valid" | "refuted" | "unverified") and evidence dict.
    No database required.
    """
    try:
        witness = json.loads(witness_json)
    except json.JSONDecodeError as exc:
        return {"verdict": "unverified", "error": f"invalid JSON: {exc}"}

    ok, evidence = verify_witness(witness)
    return {
        "verdict": _ok_label(ok),
        "schema": witness.get("schema"),
        "evidence": evidence,
    }


@mcp.tool()
def ledger_chain(bundle_json: str) -> dict[str, Any]:
    """Re-verify the hash-chain entanglement in a proof bundle.

    Pass the full bundle JSON string (from cert.export_bundle or
    ``trunkit export``). Recomputes each certificate's row_hash and
    checks premise entanglement. No database required.

    Returns chain_ok (bool), claim count, and per-claim results with
    content_ok, premises_ok, witness_ok, and recomputed vs stored hashes.
    """
    try:
        bundle = json.loads(bundle_json)
    except json.JSONDecodeError as exc:
        return {"error": f"invalid JSON: {exc}"}

    results = verify_chain(bundle)
    all_ok = all(r["content_ok"] and r.get("premises_ok", True) for r in results)
    return {
        "chain_ok": all_ok,
        "claim_count": len(results),
        "claims": results,
    }


@mcp.tool()
def bundle_verify(bundle_json: str, offline: bool = False) -> dict[str, Any]:
    """Full bundle verification: kernel witnesses + hash chain + DB probes.

    Combines three independent verification passes:
      1. Kernel re-check     — dependency-free, always runs
      2. Hash-chain check    — dependency-free, always runs
      3. DB probe replay     — skipped when offline=True or DB unreachable

    ``bundle_json`` — full JSON string of an exported bundle.
    ``offline``     — if True, skip DB probe replay even if a DB is reachable.

    Returns aggregated verdict and per-claim breakdown.
    """
    if not _HAS_DB:
        offline = True

    try:
        try:
            data = json.loads(bundle_json)
        except json.JSONDecodeError as exc:
            return {"error": f"invalid JSON: {exc}"}

        # load_bundle expects a path; write a temp file
        import tempfile
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", encoding="utf-8", delete=False
        ) as tf:
            tf.write(bundle_json)
            tmp_path = tf.name

        try:
            bundle_obj = _bundle_mod.load_bundle(tmp_path)
        except ValueError as exc:
            return {"error": str(exc)}
        finally:
            pathlib.Path(tmp_path).unlink(missing_ok=True)

        base_dir = pathlib.Path(".")

        if offline or not _HAS_DB:
            probe_results = _bundle_mod.verify_bundle(bundle_obj, None, base_dir)
        else:
            try:
                with _db_connect() as conn:
                    probe_results = _bundle_mod.verify_bundle(bundle_obj, conn, base_dir)
            except Exception:
                probe_results = _bundle_mod.verify_bundle(bundle_obj, None, base_dir)

        # Independent kernel and chain passes
        kernel_results = kernel_verify_bundle(bundle_obj)
        chain_results = verify_chain(bundle_obj)

        claims_out = []
        for pr, kr, cr in zip(probe_results, kernel_results, chain_results):
            claims_out.append({
                "claim_id": pr.claim_id,
                "statement": pr.statement,
                "method": pr.method,
                "probe_verdict": pr.status,
                "probe_notes": pr.notes,
                "kernel_verdict": (
                    kr.get("independent_verdict") if kr.get("checkable")
                    else "not_checkable"
                ),
                "chain_content_ok": cr["content_ok"],
                "chain_premises_ok": cr.get("premises_ok", True),
                "witness_ok": cr.get("witness_ok"),
            })

        n_valid = sum(1 for r in probe_results if r.ok is True)
        return {
            "summary": f"{n_valid}/{len(probe_results)} valid",
            "all_valid": n_valid == len(probe_results),
            "exported_at": data.get("exported_at"),
            "claims": claims_out,
        }

    except Exception as exc:
        return {"error": str(exc), "traceback": traceback.format_exc(limit=8)}


@mcp.tool()
def standing(
    method: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Query the cert.standing view for current ledger status.

    ``method`` — filter to a specific proof method (e.g. "probe_sql", "witness_carry")
    ``status`` — filter to "valid", "refuted", or "unverified"
    ``limit``  — max rows to return (default 100)

    Returns headline counts (valid/refuted/unverified totals) and the matching rows.
    """
    try:
        where, params = [], []
        if method:
            where.append("method = %s")
            params.append(method)
        if status:
            where.append("status = %s")
            params.append(status)

        sql = ("SELECT claim_id, statement, method, status, checked_at "
               "FROM cert.standing")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY claim_id LIMIT {int(limit)}"

        with _db_connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        with _db_connect() as conn2, conn2.cursor() as cur2:
            cur2.execute(
                "SELECT status, COUNT(*) FROM cert.standing GROUP BY status"
            )
            counts = {s: int(n) for s, n in cur2.fetchall()}

        claims = [
            {
                "claim_id": cid,
                "statement": stmt,
                "method": m,
                "status": s,
                "checked_at": ts.isoformat() if ts else None,
            }
            for cid, stmt, m, s, ts in rows
        ]
        return {
            "headline": counts,
            "total_matched": len(claims),
            "claims": claims,
        }
    except Exception as exc:
        return {"error": str(exc), "traceback": traceback.format_exc(limit=6)}


@mcp.tool()
def claim_verify(claim_id: int) -> dict[str, Any]:
    """Verify a single claim against the live database.

    Runs cert.verify(claim_id) and returns the three-valued verdict,
    evidence JSON, witness body, and an automatic kernel re-check when
    the witness carries a recognised schema.
    """
    try:
        with _db_connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT ok, evidence, witness FROM cert.verify(%s)",
                (claim_id,),
            )
            row = cur.fetchone()

        if row is None:
            return {"error": f"claim {claim_id} not found"}

        ok, evidence, witness = row
        result: dict[str, Any] = {
            "claim_id": claim_id,
            "verdict": _ok_label(ok),
            "evidence": evidence,
        }
        if witness:
            result["witness"] = witness
            body = (witness or {}).get("body")
            if isinstance(body, dict) and "schema" in body:
                k_ok, k_ev = verify_witness(body)
                result["kernel_recheck"] = {
                    "verdict": _ok_label(k_ok),
                    "evidence": k_ev,
                }
        return result
    except Exception as exc:
        return {"error": str(exc), "traceback": traceback.format_exc(limit=6)}


@mcp.tool()
def claim_export(claim_ids: list[int]) -> dict[str, Any]:
    """Export a portable proof bundle for one or more claim IDs.

    Calls cert.export_bundle and returns the bundle as a parsed dict.
    The bundle is self-contained: claims + latest certificates + witnesses
    + derivations. Pass it to bundle_verify or ledger_chain to re-verify
    without trusting the producer database.
    """
    try:
        with _db_connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT cert.export_bundle(%s::bigint[])",
                ([int(i) for i in claim_ids],),
            )
            bundle = cur.fetchone()[0]
        return {"bundle": bundle}
    except Exception as exc:
        return {"error": str(exc), "traceback": traceback.format_exc(limit=6)}


# ═══════════════════════════════════════════════════════════════════════════
# Prover tools (TRUNKIT_ALLOW_WRITE=1 required)
# ═══════════════════════════════════════════════════════════════════════════

def _require_write() -> dict[str, Any] | None:
    if not _ALLOW_WRITE:
        return {
            "error": "write operations are disabled",
            "hint": "Set TRUNKIT_ALLOW_WRITE=1 in the server environment to enable prover tools.",
        }
    return None


@mcp.tool()
def claim_check(claim_id: int, dry_run: bool = True) -> dict[str, Any]:
    """Re-run and (optionally) record a certificate for a claim.

    Dispatches to cert.check, cert.check_kernel, or cert.check_with_witness
    based on the claim's method.

    ``dry_run=True``  (default) — reads current cert.verify without writing.
    ``dry_run=False``            — records a new certificate.
    Requires TRUNKIT_ALLOW_WRITE=1 when dry_run=False.
    """
    if not dry_run:
        guard = _require_write()
        if guard:
            return guard

    try:
        if dry_run:
            with _db_connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT ok, evidence FROM cert.verify(%s)", (claim_id,)
                )
                row = cur.fetchone()
            if row is None:
                return {"error": f"claim {claim_id} not found"}
            ok, evidence = row
            return {
                "claim_id": claim_id,
                "dry_run": True,
                "would_attest": _ok_label(ok),
                "evidence": evidence,
            }

        with _db_connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT method FROM cert.claim WHERE id = %s", (claim_id,)
            )
            row = cur.fetchone()
            if row is None:
                return {"error": f"claim {claim_id} not found"}
            method = row[0]
            fn = (
                "cert.check_with_witness" if method == "witness_carry"
                else "cert.check_kernel"  if method == "cert_kernel"
                else "cert.check"
            )
            cur.execute(f"SELECT {fn}(%s)", (claim_id,))
            cur.execute(
                "SELECT status, seq FROM cert.certificate "
                "WHERE claim_id = %s ORDER BY seq DESC LIMIT 1",
                (claim_id,),
            )
            status, seq = cur.fetchone()

        return {
            "claim_id": claim_id,
            "dry_run": False,
            "status": status,
            "cert_seq": seq,
        }
    except Exception as exc:
        return {"error": str(exc), "traceback": traceback.format_exc(limit=6)}


@mcp.tool()
def witness_attach(
    claim_id: int,
    kind: str,
    body_json: str,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Attach a proof witness to a claim and optionally record it.

    ``claim_id``  — the claim to attach to
    ``kind``      — witness kind string (e.g. "kernel", "lean", "gap")
    ``body_json`` — JSON string of the witness body; must contain "schema"
                    for kernel-checkable witnesses
    ``dry_run``   — if True (default), kernel-checks but does not write to DB

    Kernel check always runs if the body has a "schema" field. If the kernel
    rejects the witness (verdict="refuted"), the call returns an error
    regardless of dry_run. Requires TRUNKIT_ALLOW_WRITE=1 when dry_run=False.
    """
    try:
        body = json.loads(body_json)
    except json.JSONDecodeError as exc:
        return {"error": f"invalid body JSON: {exc}"}

    kernel_result: dict[str, Any] = {}
    if isinstance(body, dict) and "schema" in body:
        k_ok, k_ev = verify_witness(body)
        kernel_result = {"verdict": _ok_label(k_ok), "evidence": k_ev}
        if k_ok is False:
            return {
                "error": "kernel refused the witness — will not attach",
                "kernel": kernel_result,
            }

    if dry_run:
        return {
            "claim_id": claim_id,
            "kind": kind,
            "dry_run": True,
            "kernel": kernel_result or "not_applicable",
            "note": "pass dry_run=False with TRUNKIT_ALLOW_WRITE=1 to record",
        }

    guard = _require_write()
    if guard:
        return guard

    try:
        with _db_connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT cert.attach_witness(%s, %s, %s::jsonb)",
                (claim_id, kind, json.dumps(body)),
            )
            witness_id = cur.fetchone()[0]
        return {
            "claim_id": claim_id,
            "witness_id": witness_id,
            "kind": kind,
            "kernel": kernel_result or "not_applicable",
        }
    except Exception as exc:
        return {"error": str(exc), "traceback": traceback.format_exc(limit=6)}


@mcp.tool()
def attest_run(dry_run: bool = True) -> dict[str, Any]:
    """Run the formal-tier attestation pass (tools/cert_formal.py).

    Finds all formal-tier claims without a current valid certificate.

    ``dry_run=True``  (default) — returns candidates without running.
    ``dry_run=False``            — runs cert_formal.py; requires TRUNKIT_ALLOW_WRITE=1.
    """
    try:
        with _db_connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.statement, c.method
                FROM cert.claim c
                LEFT JOIN LATERAL (
                    SELECT status FROM cert.certificate
                    WHERE claim_id = c.id ORDER BY seq DESC LIMIT 1
                ) latest ON TRUE
                WHERE c.method IN ('formal', 'lean', 'gap_homology', 'comp_sql')
                  AND (latest.status IS NULL OR latest.status != 'valid')
                ORDER BY c.id
                """
            )
            candidates = [
                {"claim_id": r[0], "statement": r[1], "method": r[2]}
                for r in cur.fetchall()
            ]

        if dry_run:
            return {
                "dry_run": True,
                "candidates": candidates,
                "count": len(candidates),
                "note": "pass dry_run=False with TRUNKIT_ALLOW_WRITE=1 to run",
            }

        guard = _require_write()
        if guard:
            return guard

        import subprocess
        # cert_formal.py lives under tools/ two levels up from src/trunkit_mcp/
        tool_path = (
            pathlib.Path(__file__).resolve().parents[2] / "tools" / "cert_formal.py"
        )
        if not tool_path.exists():
            return {"error": f"cert_formal.py not found at {tool_path}"}

        result = subprocess.run(
            [sys.executable, str(tool_path)],
            capture_output=True, text=True, timeout=300,
        )
        return {
            "dry_run": False,
            "returncode": result.returncode,
            "stdout": result.stdout[-4000:] if result.stdout else "",
            "stderr": result.stderr[-2000:] if result.stderr else "",
        }
    except Exception as exc:
        return {"error": str(exc), "traceback": traceback.format_exc(limit=6)}


# ═══════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
