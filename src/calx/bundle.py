"""Consumer verification shared by the CLI, MCP tools, and offline verifier.

A carried witness is evidence only after its registered kernel checks it.
Lifecycle, bundle integrity, and every reachable derivation premise constrain
the final three-valued verdict. Embedded SQL is replayed only when the caller
supplies a database connection; use a database appropriate for that producer.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .kernel import verify_witness
from .ledger import claim_binding_ok, verify_chain

if TYPE_CHECKING:
    from psycopg import Connection

BUNDLE_VERSION = 1
ACCEPTED_BUNDLE_VERSIONS = frozenset({1, 2})


@dataclass
class ClaimResult:
    claim_id: int | None
    statement: str
    method: str
    ok: bool | None
    notes: list[str] = field(default_factory=list)
    kernel_checked: bool = False
    kernel_ok: bool | None = None
    kernel_evidence: dict[str, Any] = field(default_factory=dict)
    chain: dict[str, Any] | None = None
    claim_bound: bool | None = None

    @property
    def status(self) -> str:
        return "VALID" if self.ok is True else "REFUTED" if self.ok is False else "UNVERIFIED"

    @property
    def mark(self) -> str:
        return "✓" if self.ok is True else "✗" if self.ok is False else "?"


def validate_bundle(data: Any) -> dict:
    """Validate portable input before interpreting proofs or replaying SQL."""
    if not isinstance(data, dict):
        raise ValueError("bundle is not a JSON object")
    version = data.get("trunk_bundle_version")
    if type(version) is not int or version not in ACCEPTED_BUNDLE_VERSIONS:
        raise ValueError(f"unsupported trunk_bundle_version: {version!r}")
    claims = data.get("claims")
    if not isinstance(claims, list) or not claims:
        raise ValueError("bundle has no claims")
    seen = {}
    normalized = []
    for i, entry in enumerate(claims):
        if not isinstance(entry, dict) or not isinstance(entry.get("claim"), dict):
            raise ValueError(f"claims[{i}] is missing the 'claim' object")
        claim = entry["claim"]
        cid = claim.get("id")
        if type(cid) is not int or cid <= 0:
            raise ValueError(f"claims[{i}] has an invalid claim id")
        if not isinstance(claim.get("statement", ""), str):
            raise ValueError(f"claims[{i}] statement must be text")
        if claim.get("probe_sql") is not None and not isinstance(claim["probe_sql"], str):
            raise ValueError(f"claims[{i}] probe_sql must be text")
        for key in ("certificate", "witness", "derivation", "artifact", "revocation"):
            if entry.get(key) is not None and not isinstance(entry[key], dict):
                raise ValueError(f"claims[{i}] {key} must be an object")
        derivation = entry.get("derivation")
        if derivation is not None:
            premises = derivation.get("premise_ids", [])
            if not isinstance(premises, list) or any(
                type(pid) is not int or pid <= 0 for pid in premises
            ):
                raise ValueError(f"claims[{i}] derivation premise_ids must be claim ids")
            if "rules" in derivation and (
                not isinstance(derivation["rules"], list) or not derivation["rules"]
                or any(not isinstance(rule, dict) for rule in derivation["rules"])
            ):
                raise ValueError(f"claims[{i}] derivation rules must be nonempty objects")
        certificate = entry.get("certificate") or {}
        if (version >= 2 or "status" in certificate) and not isinstance(
            certificate.get("status"), str
        ):
            raise ValueError(f"claims[{i}] certificate status must be text")
        for key in ("row_hash", "prev_hash", "inference_hash"):
            if certificate.get(key) is not None and not isinstance(certificate[key], str):
                raise ValueError(f"claims[{i}] certificate {key} must be text")
        hashes = certificate.get("premise_hashes")
        if hashes is not None and (
            not isinstance(hashes, list) or any(not isinstance(h, str) for h in hashes)
        ):
            raise ValueError(f"claims[{i}] certificate premise_hashes must be text hashes")
        witness = entry.get("witness") or {}
        for key in ("kind", "row_hash"):
            if witness.get(key) is not None and not isinstance(witness[key], str):
                raise ValueError(f"claims[{i}] witness {key} must be text")
        if certificate.get("valid_under") is not None and not isinstance(
            certificate["valid_under"], dict
        ):
            raise ValueError(f"claims[{i}] valid_under must be an object")
        if cid not in seen:
            seen[cid] = len(normalized)
            normalized.append(dict(entry))
            continue
        previous = normalized[seen[cid]]
        # Older exporters joined one identical claim/certificate to each
        # derivation row. Accept only that shape, never conflicting evidence.
        previous_identity = {key: value for key, value in previous.items() if key != "derivation"}
        current_identity = {key: value for key, value in entry.items() if key != "derivation"}
        if json.dumps(previous_identity, sort_keys=True) != json.dumps(
            current_identity, sort_keys=True
        ):
            raise ValueError(f"claims[{i}] has conflicting duplicate claim id {cid}")
        normalized[seen[cid]] = {
            **previous, "derivation": _merge_derivations(previous.get("derivation"), derivation),
        }
    return {**data, "claims": normalized}


def _merge_derivations(left: dict | None, right: dict | None) -> dict | None:
    """Preserve legacy exporter rules while requiring all their premises."""
    if left is None and right is None:
        return None
    premises = set()
    sources = []
    seen = set()
    for derivation in (left, right):
        if derivation is None:
            continue
        premises.update(derivation.get("premise_ids", []))
        for source in derivation.get("rules", [derivation]):
            encoded = json.dumps(source, sort_keys=True)
            if encoded not in seen:
                seen.add(encoded)
                sources.append(source)
    timestamps = [source["asserted_at"] for source in sources
                  if isinstance(source, dict) and isinstance(source.get("asserted_at"), str)]
    return {
        "premise_ids": sorted(premises),
        "rule": "all_derivations" if len(sources) > 1 else sources[0].get("rule", "?"),
        "combination": "all", "rules": sources,
        **({"asserted_at": min(timestamps)} if timestamps else {}),
    }


def load_bundle(path: str | pathlib.Path) -> dict:
    """Parse and validate a bundle file. Raises ValueError on bad input."""
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read bundle: {exc}") from exc
    return validate_bundle(data)


def _replay_probe(conn: Connection, probe_sql: str) -> tuple[bool | None, str]:
    """Replay one statement in a bounded, read-only transaction, then roll back.

    An idle connection is required so transaction-control SQL cannot commit
    the caller's pending work. Extended protocol rejects multiple statements.
    This limits ordinary SQL writes; it is not a role sandbox. Callers still
    choose a role/database whose readable data and callable functions they
    trust this producer to access.
    """
    from psycopg.pq import TransactionStatus

    if conn.info.transaction_status != TransactionStatus.IDLE:
        return None, "probe requires an idle database connection (unverified)"
    try:
        with conn.transaction(force_rollback=True), conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SET LOCAL statement_timeout = '5s'")
            cur.execute(probe_sql, prepare=True)
            row = cur.fetchone()
        if not row or type(row[0]) is not bool:
            return None, "probe returned no boolean verdict (unverified)"
        verdict = row[0]
        return verdict, "probe re-ran in local DB" if verdict else "probe returned false"
    except Exception as exc:
        # A missing table/extension or unavailable checker does not disprove a claim.
        return None, f"probe failed (unverified): {exc}"
    finally:
        conn.rollback()


def _check_artifact(artifact: dict, base_dir: pathlib.Path) -> tuple[bool | None, str]:
    """Compare the recorded artifact hash with a locally available file."""
    recorded = artifact.get("sha256")
    rel_path = artifact.get("path")
    if not isinstance(recorded, str) or not isinstance(rel_path, str):
        return None, "artifact entry lacks sha256 or path"
    candidate = pathlib.Path(rel_path)
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    if not candidate.is_file():
        return None, f"artifact file not found locally: {rel_path}"
    try:
        actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
    except OSError as exc:
        return None, f"artifact unavailable locally: {exc}"
    if actual == recorded.lower():
        return True, f"artifact sha256 verified: {rel_path}"
    return False, f"artifact sha256 MISMATCH: {rel_path}"


def _combine(left: bool | None, right: bool | None) -> bool | None:
    if left is False or right is False:
        return False
    return True if left is True and right is True else None


def _own_verdict(
    entry: dict, conn: Connection | None, base_dir: pathlib.Path,
    chain: dict[str, Any] | None,
) -> ClaimResult:
    claim = entry["claim"]
    result = ClaimResult(claim["id"], claim.get("statement", "<no statement>"),
                         claim.get("method", "?"), None, chain=chain)
    witness = entry.get("witness")
    if witness is not None:
        result.kernel_checked = True
        result.kernel_ok, result.kernel_evidence = verify_witness(witness.get("body"))
        result.ok = result.kernel_ok
        result.notes.append(
            "kernel witness re-checked" if result.kernel_ok is not None
            else f"witness unverified: {result.kernel_evidence.get('error', 'no checker verdict')}"
        )

    certificate = entry.get("certificate") or {}
    result.claim_bound = claim_binding_ok(claim, certificate)
    chain_bad = result.claim_bound is False or (chain is not None and (
        not chain["content_ok"] or chain.get("witness_ok") is False
    ))
    probe_sql = claim.get("probe_sql")
    fresh_probe = False
    if probe_sql:
        if chain_bad or result.kernel_ok is False:
            result.notes.append("probe skipped: carried proof or bundle integrity failed")
        elif conn is None:
            result.notes.append("probe requires a database (none supplied)")
        else:
            probe_ok, note = _replay_probe(conn, probe_sql)
            result.notes.append(note)
            result.ok = _combine(result.ok, probe_ok) if witness is not None else probe_ok
            fresh_probe = probe_ok is True
    elif witness is None:
        result.notes.append("no probe_sql and no witness (unverified)")

    artifact = entry.get("artifact")
    if artifact:
        art_ok, art_note = _check_artifact(artifact, base_dir)
        result.notes.append(art_note)
        if art_ok is False:
            result.ok = False
        elif art_ok is None and result.ok is True:
            result.notes.append("artifact unchecked; verdict rests on the checked kernel witness")

    if certificate.get("status") not in (None, "valid"):
        result.notes.append(f"producer certificate status was '{certificate['status']}'")

    # A fresh successful local probe supplies its own evidence. Lifecycle
    # degrades verdicts that depend on the stored certificate's witness.
    revocation = entry.get("revocation")
    life_invalid = revocation is not None
    if revocation is not None:
        result.notes.append(
            f"certificate REVOKED by {revocation.get('revoked_by', '?')}: "
            f"{revocation.get('reason', '?')}"
        )
    now = datetime.datetime.now(datetime.UTC)
    for key, relation, label in (
        ("valid_from", lambda ts: ts > now, "not yet valid"),
        ("valid_until", lambda ts: ts <= now, "expired"),
    ):
        value = (certificate.get("valid_under") or {}).get(key)
        if value is None:
            continue
        try:
            ts = datetime.datetime.fromisoformat(str(value))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=datetime.UTC)
        except ValueError:
            life_invalid = True
            result.notes.append(f"certificate {key} is malformed (unverified)")
            continue
        if relation(ts):
            life_invalid = True
            result.notes.append(f"certificate validity window {label} at {value}")
    if life_invalid and not fresh_probe and result.ok is True:
        result.ok = None

    if chain_bad:
        result.ok = False
        result.notes.append("bundle integrity check failed")
    if result.claim_bound is None:
        result.notes.append("legacy certificate does not commit to claim content")
    if chain is not None and not chain_bad and not chain.get("premises_ok", True):
        result.ok = _combine(result.ok, None)
        result.notes.append("bundle premise hashes could not be verified")
    return result


def _apply_derivations(bundle: dict, results: list[ClaimResult]) -> None:
    """Evaluate the DAG from its leaves; unresolved nodes have circular support."""
    by_id = {r.claim_id: r for r in results}
    entries = {e["claim"]["id"]: e for e in bundle["claims"]}
    parents: dict[int, set[int]] = {}
    consumers: dict[int, list[int]] = {cid: [] for cid in entries}
    pending = {}
    for cid, entry in entries.items():
        premises = set((entry.get("derivation") or {}).get("premise_ids", []))
        parents[cid] = premises
        pending[cid] = len(premises & entries.keys())
        for pid in premises & entries.keys():
            consumers[pid].append(cid)
    ready = deque(cid for cid, count in pending.items() if count == 0)
    processed = set()
    while ready:
        cid = ready.popleft()
        result = by_id[cid]
        for pid in sorted(parents[cid]):
            premise = by_id.get(pid)
            if premise is None:
                result.notes.append(f"derivation: premise {pid} not in bundle")
                result.ok = _combine(result.ok, None)
            elif premise.ok is not True:
                result.notes.append(f"derivation: premise {pid} {premise.status.lower()}")
                result.ok = _combine(result.ok, premise.ok)
        processed.add(cid)
        for consumer in consumers[cid]:
            pending[consumer] -= 1
            if pending[consumer] == 0:
                ready.append(consumer)
    for cid in entries.keys() - processed:
        by_id[cid].ok = False
        by_id[cid].notes.append("cycle_detected: derivation has circular support")


def verify_bundle(
    bundle: dict, conn: Connection | None = None, base_dir: str | pathlib.Path = ".",
) -> list[ClaimResult]:
    """Check kernels, integrity, lifecycle, and composition through one pipeline."""
    bundle = validate_bundle(bundle)
    # Do not let a version-label downgrade bypass hashes that still travel.
    has_chain = bundle["trunk_bundle_version"] >= 2 or any(
        "row_hash" in (entry.get("certificate") or {}) for entry in bundle["claims"]
    )
    chains = verify_chain(bundle) if has_chain else [None] * len(bundle["claims"])
    base = pathlib.Path(base_dir)
    results = [_own_verdict(entry, conn, base, chain)
               for entry, chain in zip(bundle["claims"], chains, strict=True)]
    _apply_derivations(bundle, results)
    return results
