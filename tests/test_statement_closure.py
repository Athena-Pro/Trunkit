"""Tests for statement-closure binding (117) and the comparator checker kind.

The fixtures in tests/fixtures/lean_closure/ are REAL AxiomAudit output (Lean
v4.27) for one theorem before and after its definition was weakened:

    def Strong (c : Cfg) : Prop := c.n = c.n + 0     -- strong_bound.json
    def Strong (c : Cfg) : Prop := True              -- strong_weakened.json
    theorem target (c : Cfg) : Strong c := ...       -- same text in both

The whole point of 117 is the first test below: everything 106 can see is
identical across that edit, and the closure is not.

Unit tests are DB-free and Lean-free. DB-backed tests skip without a test DSN.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from calx import goalhash  # noqa: E402
from calx import leanbridge as lb  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "lean_closure"
BOUND = json.loads((FIX / "strong_bound.json").read_text(encoding="utf-8"))
WEAKENED = json.loads((FIX / "strong_weakened.json").read_text(encoding="utf-8"))

SRC_BOUND = """namespace Drift
structure Cfg where
  n : Nat
def Strong (c : Cfg) : Prop := c.n = c.n + 0
theorem target (c : Cfg) : Strong c := rfl
end Drift
"""
SRC_WEAKENED = (FIX / "Drift_weakened.lean").read_text(encoding="utf-8")


def _load_harness():
    spec = importlib.util.spec_from_file_location(
        "cert_formal_closure_test", REPO / "tools" / "cert_formal.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HARNESS = _load_harness()


# --- unit: the gap, demonstrated -------------------------------------------

def test_weakening_a_definition_is_invisible_to_the_syntactic_digest():
    before, _ = goalhash.extract_declaration(SRC_BOUND, "Drift.target")
    after, _ = goalhash.extract_declaration(SRC_WEAKENED, "Drift.target")
    assert goalhash.statement_digest(before) == goalhash.statement_digest(after)
    # ...and even Lean's elaborated, pretty-printed type does not move.
    assert BOUND["type"] == WEAKENED["type"]


def test_the_closure_digest_sees_it_and_names_the_definition():
    b, w = lb.statement_closure(BOUND), lb.statement_closure(WEAKENED)
    assert lb.closure_manifest_digest(b) != lb.closure_manifest_digest(w)
    diff = lb.closure_diff(b, w)
    assert diff["changed"] == ["Drift.Strong"]
    assert "True" in diff["added"]


def test_closure_walk_skips_theorems_and_keeps_the_root():
    names = set(lb.statement_closure(BOUND))
    assert "Drift.target" in names          # the root's TYPE is part of it
    assert "Drift.helper" not in names      # unrelated, and a theorem anyway
    assert not any(n.endswith(".proof_1") for n in names)


def test_digest_ignores_manifest_order():
    m = lb.statement_closure(BOUND)
    rev = dict(reversed(list(m.items())))
    assert lb.closure_manifest_digest(m) == lb.closure_manifest_digest(rev)


def test_absent_closure_is_none_not_empty():
    assert lb.statement_closure({"decl": "x", "axioms": []}) is None
    assert lb.statement_closure({"statement_closure": []}) == {}


# --- unit: harness ------------------------------------------------------------

def _project(tmp_path: Path):
    (tmp_path / "lean-toolchain").write_text("leanprover/lean4:v4.27.0\n")
    (tmp_path / "lakefile.lean").write_text("-- lake\n")
    (tmp_path / "Drift.lean").write_text(SRC_BOUND)
    rels = lb.discover_closure(tmp_path)
    fds = lb.compute_file_digests(tmp_path, rels)
    return str(tmp_path), fds, lb.closure_digest(fds)


def _stub(tmp_path: Path, verdict: dict, exit_code: int) -> str:
    stub = tmp_path / "stub.py"
    stub.write_text("import sys\n"
                    f"print({json.dumps(json.dumps(verdict))})\n"
                    f"sys.exit({exit_code})\n", encoding="utf-8")
    return f'"{sys.executable}" "{stub}"'


def test_verify_lean_fills_the_closure_sink(tmp_path):
    root, fds, trusted = _project(tmp_path)
    sink: dict = {}
    status, ev, _ = HARNESS.verify_lean(root, fds, trusted, _stub(tmp_path, BOUND, 0),
                                        {}, closure_sink=sink)
    assert status == "valid"
    assert ev["statement_closure"]["n_constants"] == len(BOUND["statement_closure"])
    assert sink["sha256"] == ev["statement_closure"]["sha256"]
    assert sink["type"] == BOUND["type"]
    # The manifest goes to the sink, not into the certificate.
    assert "manifest" not in ev["statement_closure"]


def test_exit_2_is_error_not_refuted(tmp_path):
    root, fds, trusted = _project(tmp_path)
    verdict = {"checker": "comparator", "ok": False,
               "error": "comparator prerequisites missing: landrun"}
    status, ev, _ = HARNESS.verify_lean(root, fds, trusted, _stub(tmp_path, verdict, 2), {})
    assert status == "error"
    assert "landrun" in ev["checker_error"]


def test_comparator_verdict_is_gated_like_any_other(tmp_path):
    root, fds, trusted = _project(tmp_path)
    verdict = {"decl": "NS.breakdown", "type": "…", "uses_sorry": False,
               "checker": "comparator", "kernels": ["lean", "nanoda"], "ok": True,
               "axioms": ["propext", "Quot.sound", "Classical.choice", "Lean.ofReduceBool"]}
    status, ev, _ = HARNESS.verify_lean(root, fds, trusted, _stub(tmp_path, verdict, 0), {})
    # comparator accepted, but the challenge permitted native_decide's trust root
    assert status == ("valid" if HARNESS.LEAN_ALLOW_NATIVE else "refuted")
    assert ev["checker"] == "comparator" and ev["kernels"] == ["lean", "nanoda"]


def _bash_that_sees(path: Path) -> str | None:
    """A bash that can read ``path``. On Windows the `bash` on PATH is often
    WSL's, which cannot see Windows paths; Git Bash can."""
    for cand in (shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe"):
        if not cand or not Path(cand).exists():
            continue
        try:
            ok = subprocess.run([cand, "-c", 'test -f "$1"', "_", path.as_posix()],
                                capture_output=True, timeout=30).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            continue
        if ok:
            return cand
    return None


def test_comparator_wrapper_without_prerequisites_is_an_error(tmp_path):
    script = REPO / "tools" / "comparator_check.sh"
    bash = _bash_that_sees(script)
    if bash is None:
        pytest.skip("no bash that can read the repository")
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"challenge_module": "C", "solution_module": "S",
                               "theorem_names": ["t"], "permitted_axioms": []}))
    env = dict(os.environ, COMPARATOR_LANDRUN="definitely-not-landrun-xyz",
               PYTHON=Path(sys.executable).as_posix())
    proc = subprocess.run([bash, script.as_posix(), tmp_path.as_posix(), "c.json"],
                          capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode == 2
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["ok"] is False and "landrun" in out["error"]


# --- DB-backed ---------------------------------------------------------------

def _dsn():
    dsn = os.environ.get("CALX_TEST_DSN") or os.environ.get("ARITHMETIC_DB_TEST_DSN")
    if not dsn:
        pytest.skip("No test DSN provided. Refusing to write to default/production ledger.")
    return dsn


@pytest.fixture()
def conn():
    try:
        c = psycopg.connect(_dsn(), connect_timeout=3)
    except psycopg.Error as exc:
        pytest.skip(f"test DB not reachable: {exc}")
    c.autocommit = True
    try:
        with c.cursor() as cur:
            cur.execute("SELECT to_regclass('cert.closure_manifest')")
            if cur.fetchone()[0] is None:
                pytest.skip("cert.closure_manifest missing — apply 117 first")
        yield c
    finally:
        c.close()


def _mj(audit):
    m = lb.statement_closure(audit)
    return lb.closure_manifest_digest(m), Jsonb({k: list(v) for k, v in m.items()})


def _claim(cur) -> int:
    cur.execute(
        "INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind,"
        " method, probe_sql) VALUES ('closure_test','{}'::jsonb,%s,'computational',"
        " 'comp_sql','SELECT TRUE, ''{}''::jsonb') RETURNING id",
        (f"closure test claim {uuid.uuid4()}",))
    return cur.fetchone()[0]


def _bind(cur, claim, audit=BOUND, tc="tc-1"):
    sha, mj = _mj(audit)
    cur.execute("SELECT (cert.bind_statement_closure(%s,'Drift.target',%s,%s,%s,%s)).id",
                (claim, audit["type"], sha, mj, tc))
    return cur.fetchone()[0]


def _observe(cur, claim, audit, tc="tc-1"):
    sha, mj = _mj(audit)
    cur.execute("SELECT cert.observe_statement_closure(%s,%s,%s,%s,'',%s)",
                (claim, sha, mj, audit["type"], tc))
    return cur.fetchone()[0]


def _bound(cur, claim):
    cur.execute("SELECT ok, evidence FROM cert.statement_bound(%s)", (claim,))
    return cur.fetchone()


def test_sql_recipe_matches_python(conn):
    sha, mj = _mj(BOUND)
    with conn.cursor() as cur:
        cur.execute("SELECT cert.closure_manifest_digest(%s)", (mj,))
        assert cur.fetchone()[0] == sha


def test_a_manifest_that_does_not_hash_to_its_digest_is_refused(conn):
    sha, _ = _mj(BOUND)
    _, weakened = _mj(WEAKENED)
    with conn.cursor() as cur, pytest.raises(psycopg.errors.RaiseException):
        cur.execute("SELECT cert.record_closure_manifest(%s,%s)", (sha, weakened))


def test_manifests_are_append_only(conn):
    sha, mj = _mj(BOUND)
    with conn.cursor() as cur:
        cur.execute("SELECT cert.record_closure_manifest(%s,%s)", (sha, mj))
        with pytest.raises(psycopg.Error):
            cur.execute("UPDATE cert.closure_manifest SET toolchain='x' WHERE sha256=%s",
                        (sha,))


def test_unchanged_closure_is_valid(conn):
    with conn.cursor() as cur:
        claim = _claim(cur)
        _bind(cur, claim)
        assert _bound(cur, claim)[0] is None          # bound, never observed
        assert _observe(cur, claim, BOUND) is not None
        ok, ev = _bound(cur, claim)
        assert ok is True and ev["digest_kind"] == "closure"


def test_weakened_definition_is_refuted_and_named(conn):
    with conn.cursor() as cur:
        claim = _claim(cur)
        _bind(cur, claim)
        _observe(cur, claim, WEAKENED)
        ok, ev = _bound(cur, claim)
        assert ok is False
        assert ev["reason"].startswith("DEFINITION DRIFT")
        assert ev["closure_diff"]["changed"] == ["Drift.Strong"]
        # the texts a reader sees are identical -- which is the point
        assert ev["bound_text"] == ev["observed_text"]


def test_cross_toolchain_observation_is_not_comparable_not_drift(conn):
    with conn.cursor() as cur:
        claim = _claim(cur)
        _bind(cur, claim, tc="lean-4.27")
        _observe(cur, claim, WEAKENED, tc="lean-4.34")
        ok, ev = _bound(cur, claim)
        assert ok is None and ev["reason"].startswith("NOT COMPARABLE")


def test_observation_without_a_closure_binding_binds_nothing(conn):
    with conn.cursor() as cur:
        claim = _claim(cur)
        assert _observe(cur, claim, BOUND) is None
        cur.execute("SELECT count(*) FROM cert.statement_binding WHERE claim_id=%s", (claim,))
        assert cur.fetchone()[0] == 0


def test_harness_records_and_reads_back_the_verdict(conn):
    sink = {"manifest": lb.statement_closure(WEAKENED),
            "sha256": lb.closure_manifest_digest(lb.statement_closure(WEAKENED)),
            "type": WEAKENED["type"]}
    tc = {"lean": "leanprover/lean4:v4.27.0"}
    with conn.cursor() as cur:
        claim = _claim(cur)
        unbound = HARNESS.record_closure_observation(cur, claim, sink, "d" * 64, tc)
        assert unbound["ok"] is None and "bind-statement" in unbound["reason"]
        _bind(cur, claim, tc=json.dumps(tc, sort_keys=True))
        verdict = HARNESS.record_closure_observation(cur, claim, sink, "d" * 64, tc)
        assert verdict["ok"] is False
        assert verdict["closure_diff"]["changed"] == ["Drift.Strong"]


def test_navier_stokes_anchor_keeps_the_interpretation_underived(conn):
    spec = importlib.util.spec_from_file_location(
        "anchor_ns_test", REPO / "tools" / "anchor_navier_stokes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for _ in range(2):  # idempotent
        assert mod.main(["--dsn", _dsn()]) == 0
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM cert.claim WHERE statement = %s", (mod.INTERPRETATION,))
        interp = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM cert.derivation WHERE conclusion_id=%s", (interp,))
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM cert.statement_binding sb JOIN cert.claim c"
                    " ON c.id = sb.claim_id WHERE c.subject_ref->>'problem' = 'navier_stokes'"
                    " AND sb.digest_kind = 'closure'")
        assert cur.fetchone()[0] >= 2
