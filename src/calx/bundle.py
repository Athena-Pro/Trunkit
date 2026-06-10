"""Consumer-side verification of exported proof bundles.

``trunkit export`` produces a self-contained JSONB bundle (claims + latest
certificates + witnesses + derivations + artifact specs). This module is the
consumer half: re-verify a received bundle without trusting the producer and
without write access to any ledger.

Verification is three-valued, matching the cert layer everywhere else:

- ``True``  (valid)      — probe re-ran successfully here, or witness present
                           and artifact hash matches
- ``False`` (refuted)    — probe returned false / errored, artifact hash
                           mismatch, or a premise in the bundle is refuted
- ``None``  (unverified) — probe needs a database and none is reachable,
                           or a derivation premise is missing from the bundle

Probe replay executes the bundle's embedded ``probe_sql`` against the
*consumer's* database and rolls back afterwards, so no state escapes. Run a
bundle from an untrusted producer only against an instance you are willing
to let its probes read.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import dataclass, field

from psycopg import Connection

BUNDLE_VERSION = 1


@dataclass
class ClaimResult:
    claim_id: int | None
    statement: str
    method: str
    ok: bool | None
    notes: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "VALID" if self.ok is True else "REFUTED" if self.ok is False else "UNVERIFIED"

    @property
    def mark(self) -> str:
        return "✓" if self.ok is True else "✗" if self.ok is False else "?"


def load_bundle(path: str | pathlib.Path) -> dict:
    """Parse and shape-check a bundle file. Raises ValueError on a bad bundle."""
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read bundle: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("bundle is not a JSON object")
    version = data.get("trunk_bundle_version")
    if version != BUNDLE_VERSION:
        raise ValueError(
            f"unsupported trunk_bundle_version: {version!r} (expected {BUNDLE_VERSION})"
        )
    claims = data.get("claims")
    if not isinstance(claims, list) or not claims:
        raise ValueError("bundle has no claims")
    for i, entry in enumerate(claims):
        if not isinstance(entry, dict) or "claim" not in entry:
            raise ValueError(f"claims[{i}] is missing the 'claim' object")
    return data


def _replay_probe(conn: Connection, probe_sql: str) -> tuple[bool | None, str]:
    """Run an embedded probe in a transaction and roll back. Errors refute."""
    try:
        with conn.cursor() as cur:
            cur.execute(probe_sql)
            row = cur.fetchone()
        if row is None or not isinstance(row[0], bool):
            return False, "probe returned no boolean verdict"
        verdict = row[0]
        return verdict, "probe re-ran in local DB" if verdict else "probe returned false"
    except Exception as exc:  # any SQL failure refutes, like cert.verify
        return False, f"probe failed: {exc}"
    finally:
        conn.rollback()


def _check_artifact(artifact: dict, base_dir: pathlib.Path) -> tuple[bool | None, str]:
    """Compare the artifact's recorded sha256 against the local file, if present."""
    recorded = (artifact.get("sha256") or "").lower()
    rel_path = artifact.get("path")
    if not recorded or not rel_path:
        return None, "artifact entry lacks sha256 or path"
    candidate = pathlib.Path(rel_path)
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    if not candidate.is_file():
        return None, f"artifact file not found locally: {rel_path}"
    actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if actual == recorded:
        return True, f"artifact sha256 verified: {rel_path}"
    return False, f"artifact sha256 MISMATCH: {rel_path}"


def _own_verdict(
    entry: dict, conn: Connection | None, base_dir: pathlib.Path
) -> ClaimResult:
    claim = entry["claim"]
    result = ClaimResult(
        claim_id=claim.get("id"),
        statement=claim.get("statement", "<no statement>"),
        method=claim.get("method", "?"),
        ok=None,
    )

    probe_sql = claim.get("probe_sql")
    witness = entry.get("witness")

    if probe_sql:
        if conn is None:
            result.ok = None
            result.notes.append(
                "probe requires a database (none reachable; structural checks only)"
            )
        else:
            result.ok, note = _replay_probe(conn, probe_sql)
            result.notes.append(note)
    else:
        # Formal/empirical tier: witness presence is the verdict (cert.verify parity).
        result.ok = witness is not None
        result.notes.append(
            f"witness present (kind={witness.get('kind')})" if witness
            else "no probe_sql and no witness"
        )

    artifact = entry.get("artifact")
    if artifact:
        art_ok, art_note = _check_artifact(artifact, base_dir)
        result.notes.append(art_note)
        if art_ok is False:
            result.ok = False
        elif art_ok is None and result.ok is True:
            result.notes.append("artifact unchecked; verdict rests on witness/probe only")

    cert_status = (entry.get("certificate") or {}).get("status")
    if cert_status and cert_status != "valid":
        result.notes.append(f"producer certificate status was '{cert_status}'")

    return result


def _apply_derivations(bundle: dict, results: list[ClaimResult]) -> None:
    """Degrade conclusions whose bundled premises are missing or not valid."""
    by_id = {r.claim_id: r for r in results if r.claim_id is not None}
    for entry, result in zip(bundle["claims"], results, strict=True):
        derivation = entry.get("derivation")
        if not derivation:
            continue
        rule = derivation.get("rule", "?")
        for pid in derivation.get("premise_ids") or []:
            premise = by_id.get(pid)
            if premise is None:
                result.notes.append(f"derivation ({rule}): premise {pid} not in bundle")
                if result.ok is True:
                    result.ok = None
            elif premise.ok is False:
                result.notes.append(f"derivation ({rule}): premise {pid} refuted")
                result.ok = False
            elif premise.ok is None and result.ok is True:
                result.notes.append(f"derivation ({rule}): premise {pid} unverified")
                result.ok = None


def verify_bundle(
    bundle: dict,
    conn: Connection | None = None,
    base_dir: str | pathlib.Path = ".",
) -> list[ClaimResult]:
    """Verify every claim in a parsed bundle. Read-only; rolls back probe state."""
    base = pathlib.Path(base_dir)
    results = [_own_verdict(entry, conn, base) for entry in bundle["claims"]]
    _apply_derivations(bundle, results)
    return results
