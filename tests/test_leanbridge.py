"""Unit tests for the Lean bridge helpers (calx.leanbridge).

DB-free and Lean-free: these exercise the closure-digest recipe, the
axiom/sorry gate, and project introspection in plain CI.
"""

from __future__ import annotations

from calx import leanbridge as lb


# --- axiom / sorry gate (the T1 G2 correctness gate) ------------------------

def test_gate_accepts_trusted_axioms():
    assert lb.audit_ok(["propext", "Classical.choice", "Quot.sound"], False) is True
    assert lb.audit_ok([], False) is True


def test_gate_rejects_sorry_even_with_clean_axioms():
    # `lake build` can exit 0 with a sorry; the gate must still refuse.
    assert lb.audit_ok(["propext"], True) is False


def test_gate_rejects_unknown_axiom():
    assert lb.audit_ok(["propext", "EvilAxiom"], False) is False


def test_gate_native_decide_opt_in():
    assert lb.audit_ok([lb.NATIVE_DECIDE_AXIOM], False) is False
    assert lb.audit_ok([lb.NATIVE_DECIDE_AXIOM], False, allow_native=True) is True


# --- closure digest (drift gate foundation) ---------------------------------

def test_closure_digest_is_order_independent():
    a = lb.closure_digest({"A.lean": "11", "B.lean": "22"})
    b = lb.closure_digest({"B.lean": "22", "A.lean": "11"})
    assert a == b


def test_closure_digest_changes_on_any_file_change():
    base = lb.closure_digest({"A.lean": "11", "B.lean": "22"})
    drift = lb.closure_digest({"A.lean": "11", "B.lean": "23"})
    assert base != drift


# --- project introspection --------------------------------------------------

def test_discover_closure_and_digests(tmp_path):
    (tmp_path / "lean-toolchain").write_text("leanprover/lean4:v4.99.0")
    (tmp_path / "lakefile.lean").write_text("-- lake")
    (tmp_path / "Proof.lean").write_text("theorem t : True := trivial")
    lake = tmp_path / ".lake" / "build"
    lake.mkdir(parents=True)
    (lake / "junk.lean").write_text("should be excluded")

    rels = lb.discover_closure(tmp_path)
    assert "lean-toolchain" in rels
    assert "lakefile.lean" in rels
    assert "Proof.lean" in rels
    assert not any(".lake" in r for r in rels)  # build dir excluded

    digests = lb.compute_file_digests(tmp_path, rels)
    assert set(digests) == set(rels)
    assert all(len(h) == 64 for h in digests.values())


def test_read_toolchain(tmp_path):
    (tmp_path / "lean-toolchain").write_text("leanprover/lean4:v4.99.0\n")
    (tmp_path / "lake-manifest.json").write_text(
        '{"packages":[{"name":"mathlib","rev":"deadbeef"}]}'
    )
    tc = lb.read_toolchain(tmp_path)
    assert tc["lean"] == "leanprover/lean4:v4.99.0"
    assert tc["mathlib_rev"] == "deadbeef"
