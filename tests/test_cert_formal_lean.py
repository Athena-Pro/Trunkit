"""Lean-bridge harness tests (T1).

Two layers:
  * verdict logic (DB-free, Lean-free): drive `verify_lean` with a STUB checker
    that emits auditor-style JSON, covering the four required cases
    valid / sorry / disallowed-axiom / closure-drift.
  * DB-backed: register a Lean artifact via cert.register_lean_artifact, confirm
    cert.lean_standing reflects it, and append a real certificate.

The real `lake`-backed build is intentionally NOT exercised here (no toolchain in
CI); it is validated by the gate unit tests plus a manual/CI run on a machine
with `elan`. Stubbing the checker isolates the harness plumbing from Lean itself.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path

import psycopg
import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from calx import leanbridge as lb  # noqa: E402


def _load_harness():
    spec = importlib.util.spec_from_file_location(
        "cert_formal_under_test", REPO / "tools" / "cert_formal.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HARNESS = _load_harness()


def _make_project(tmp_path: Path) -> tuple[str, dict, str]:
    """A minimal Lean-shaped project; returns (root, file_digests, trusted)."""
    (tmp_path / "lean-toolchain").write_text("leanprover/lean4:v4.99.0\n")
    (tmp_path / "lakefile.lean").write_text("-- lake\n")
    (tmp_path / "Proof.lean").write_text("theorem t : True := trivial\n")
    rels = lb.discover_closure(tmp_path)
    fds = lb.compute_file_digests(tmp_path, rels)
    return str(tmp_path), fds, lb.closure_digest(fds)


def _stub_checker(tmp_path: Path, *, axioms, uses_sorry, exit_code) -> str:
    """A checker_cmd that prints auditor JSON and exits with exit_code."""
    verdict = {
        "decl": "Proof.t",
        "type": "True",
        "axioms": axioms,
        "uses_sorry": uses_sorry,
        "ok": exit_code == 0,
    }
    stub = tmp_path / "stub_checker.py"
    stub.write_text(
        "import sys\n"
        f"print({json.dumps(json.dumps(verdict))})\n"
        f"sys.exit({exit_code})\n"
    )
    return f'{sys.executable} "{stub}"'


# --- verdict logic (DB-free) -------------------------------------------------

def test_lean_valid(tmp_path):
    root, fds, trusted = _make_project(tmp_path)
    cmd = _stub_checker(tmp_path, axioms=["propext", "Classical.choice"],
                        uses_sorry=False, exit_code=0)
    status, evidence, vu = HARNESS.verify_lean(root, fds, trusted, cmd, {"lean": "v4.99.0"})
    assert status == "valid"
    assert evidence["axioms"] == ["propext", "Classical.choice"]
    assert vu["artifact_sha256"] == trusted


def test_lean_sorry_refuted(tmp_path):
    root, fds, trusted = _make_project(tmp_path)
    cmd = _stub_checker(tmp_path, axioms=["propext"], uses_sorry=True, exit_code=1)
    status, evidence, _ = HARNESS.verify_lean(root, fds, trusted, cmd, {})
    assert status == "refuted"
    assert evidence["uses_sorry"] is True


def test_lean_bad_axiom_refuted_even_if_checker_exits_zero(tmp_path):
    # Defense in depth: even a checker that returns 0 must not pass a proof that
    # rests on a disallowed axiom — the Python gate re-checks.
    root, fds, trusted = _make_project(tmp_path)
    cmd = _stub_checker(tmp_path, axioms=["propext", "EvilAxiom"],
                        uses_sorry=False, exit_code=0)
    status, evidence, _ = HARNESS.verify_lean(root, fds, trusted, cmd, {})
    assert status == "refuted"
    assert "EvilAxiom" in evidence["axioms"]


def test_lean_closure_drift_refuted(tmp_path):
    root, fds, trusted = _make_project(tmp_path)
    # tamper with a registered file AFTER computing the trusted digest
    (tmp_path / "Proof.lean").write_text("theorem t : True := trivial -- tampered\n")
    cmd = _stub_checker(tmp_path, axioms=["propext"], uses_sorry=False, exit_code=0)
    status, evidence, _ = HARNESS.verify_lean(root, fds, trusted, cmd, {})
    assert status == "refuted"
    assert "drift" in evidence["reason"]


# --- DB-backed: registration + certificate ----------------------------------

def _calx_dsn():
    """A DEDICATED test DSN, or skip.  Never the resolved default.

    This test registers a claim and mints certificates.  It used to fall back
    to ``calx_db.resolve_dsn()``, which meant that running the suite without
    CALX_TEST_DSN set wrote straight into whatever ledger the package resolves
    to.  It did: 16 ``Erdős test claim <uuid> (lean bridge)`` claims and 32
    certificates reached the canonical ledger between 2026-07-01 and 07-13.

    Thirteen of them read ``error`` because they point at pytest tmp_path
    directories that have since been collected -- and the other three read
    ``valid`` only because ``pytest-17`` happens to still be on disk. pytest
    keeps the last three temp roots, so the next run of this suite deletes it
    and flips those three from valid to error. A ledger verdict must not depend
    on a temp-directory garbage-collection policy.

    The fallback was survivable while ``calx.db.DEFAULT_DSN`` pointed at the
    stopped 5434 docker instance -- the connect failed and the test skipped.
    That default was corrected to the canonical 5432 on 2026-07-25, which turns
    the same code path into a silent write against the real ledger. Hence the
    hard skip, matching tests/test_cert_lifecycle.py.
    """
    try:
        from calx import db as calx_db  # noqa: F401  (import-guard only)
    except ImportError:  # pragma: no cover
        pytest.skip("calx package not installed")
    dsn = (os.environ.get("CALX_TEST_DSN")
           or os.environ.get("ARITHMETIC_DB_TEST_DSN"))
    if not dsn:
        pytest.skip("No test DSN provided. Refusing to write to default/production ledger.")
    return dsn


def test_register_lean_artifact_and_certificate(tmp_path):
    dsn = _calx_dsn()
    try:
        conn = psycopg.connect(dsn, connect_timeout=3)
    except psycopg.Error as exc:
        pytest.skip(f"calx DB not reachable: {exc}")

    root, fds, trusted = _make_project(tmp_path)
    cmd = _stub_checker(tmp_path, axioms=["propext"], uses_sorry=False, exit_code=0)
    stmt = f"Erdős test claim {uuid.uuid4()} (lean bridge)"

    with conn:
        with conn.cursor() as cur:
            # method row must exist (idempotent upsert)
            cur.execute(
                "INSERT INTO cert.method (name, claim_kind, checker_kind, description) "
                "VALUES ('formal_external','formal','external_cmd','ext') "
                "ON CONFLICT (name) DO NOTHING"
            )
            cur.execute(
                "INSERT INTO cert.claim "
                "(subject_kind, subject_ref, statement, claim_kind, method, probe_sql) "
                "VALUES ('erdos_problem', %s, %s, 'formal', 'formal_external', NULL) "
                "RETURNING id",
                (json.dumps({"id": 728}), stmt),
            )
            claim_id = cur.fetchone()[0]

            cur.execute(
                "SELECT (cert.register_lean_artifact"
                "(%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)).id",
                (claim_id, root, "Proof.t", json.dumps(fds),
                 json.dumps({"lean": "v4.99.0"}), trusted, cmd),
            )
            art_id = cur.fetchone()[0]
            assert art_id is not None

            # lean_standing view reflects the registration
            cur.execute(
                "SELECT project_root, target_decl, registered_digest "
                "FROM cert.lean_standing WHERE claim_id=%s",
                (claim_id,),
            )
            pr, decl, regdig = cur.fetchone()
            assert pr == root and decl == "Proof.t" and regdig == trusted

            # run the harness verify + append a real certificate
            status, evidence, vu = HARNESS.verify_lean(
                root, fds, trusted, cmd, {"lean": "v4.99.0"}
            )
            assert status == "valid"
            HARNESS.append_certificate(cur, claim_id, status, evidence, vu, stmt, "lean")

            cur.execute(
                "SELECT status FROM cert.standing WHERE claim_id=%s", (claim_id,)
            )
            assert cur.fetchone()[0] == "valid"
