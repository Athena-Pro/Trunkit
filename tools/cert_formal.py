"""Formal-tier harness for the cert pillar.

Drives `formal` claims: ensures each has a registered artifact, verifies the
artifact's sha256 against the *trusted* registered hash (drift/tamper gate),
runs the external checker command, and appends a certificate through the same
append-only cert.certificate table + curry.inferences provenance that
cert.check uses for in-DB claims.

Trust model:
  - First run for a claim with no artifact row: trust-on-first-register (TOFU)
    — the current file hash becomes the trusted baseline.
  - Subsequent runs: current hash MUST equal the registered hash, else the
    certificate is 'refuted' (the formal-proof analogue of stale detection).
  - A legitimate artifact change is an explicit cert.register_artifact() call,
    not something this harness does silently.

Idempotent: every run appends a new certificate seq (immutable audit).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

PROJECT_DIR = Path(__file__).resolve().parent.parent
PG_DSN = os.environ.get(
    "CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk"
)

# Lean bridge (T1): shared digest recipe + axiom gate. Importing calx requires
# src/ on the path when this harness is run as a standalone script.
sys.path.insert(0, str(PROJECT_DIR / "src"))
try:
    from calx import leanbridge
except Exception:  # pragma: no cover - lean branch simply disabled if unavailable
    leanbridge = None

# Known formal claims -> their backing artifact. (Worked example; extend here
# or register artifacts directly via cert.register_artifact.)
FORMAL_ARTIFACTS = {
    "28 is a perfect number (independently verified by external checker)": {
        "kind": "python",
        "path": "proofs/perfect_28.py",
        "checker_cmd": f'"{sys.executable}" proofs/perfect_28.py',
    },
    "prime gap-pattern complex H1 strictly grows (3,8,30,59,128) with d1.d2=0": {
        "kind": "python",
        "path": "proofs/gap_homology_primes.py",
        "checker_cmd": f'"{sys.executable}" proofs/gap_homology_primes.py',
    },
    "OEIS difference-tower H1 signatures hold; Catalan/Bell/Motzkin are a prefix-invisible depth class": {
        "kind": "python",
        "path": "proofs/seq_homology_signature.py",
        "checker_cmd": f'"{sys.executable}" proofs/seq_homology_signature.py',
    },
    "factorial-homology signatures hold; primes pairwise coprime; squares == cubes": {
        "kind": "python",
        "path": "proofs/factorial_homology_signature.py",
        "checker_cmd": f'"{sys.executable}" proofs/factorial_homology_signature.py',
    },
    "combined difference+factorial signature is a complete invariant; each lens resolves the other class": {
        "kind": "python",
        "path": "proofs/combined_signature.py",
        "checker_cmd": f'"{sys.executable}" proofs/combined_signature.py',
    },
    "combined invariant complete at N=9, degrades to 20/23 at N=23; kernel is the prime-power class": {
        "kind": "python",
        "path": "proofs/combined_scale.py",
        "checker_cmd": f'"{sys.executable}" proofs/combined_scale.py',
    },
    "shared-prime flag complex is acyclic above H0 (b1=b2=0); rich 1-cycles are all triangle-filled": {
        "kind": "python",
        "path": "proofs/shared_prime_h2.py",
        "checker_cmd": f'"{sys.executable}" proofs/shared_prime_h2.py',
    },
    "system-developed Aliquot-Recaman Z000001 is deterministic, unique vs the 23 corpus, and unpredictable (non-collapsing difference tower)": {
        "kind": "python",
        "path": "proofs/developed_sequence.py",
        "checker_cmd": f'"{sys.executable}" proofs/developed_sequence.py',
    },
    "system-developed omega-relation family: 6 algorithmically generated members with exact small-omega/big-Omega relations to the Z000001 generative set": {
        "kind": "python",
        "path": "proofs/omega_family.py",
        "checker_cmd": f'"{sys.executable}" proofs/omega_family.py',
    },
    "successor-kernel omega family is the canonical arithmetic strata, and the omega/Omega family is provably generative-kernel-dependent": {
        "kind": "python",
        "path": "proofs/omega_family_succ.py",
        "checker_cmd": f'"{sys.executable}" proofs/omega_family_succ.py',
    },
    "prime_members is a total idempotent endofunctor on sequences; the omega=1 stratum is functorial and yields the prime members of any sequence": {
        "kind": "python",
        "path": "proofs/prime_members_functor.py",
        "checker_cmd": f'"{sys.executable}" proofs/prime_members_functor.py',
    },
    "the strata tower is a complete system of orthogonal idempotent endofunctors; prime_members is its bottom rung and the omega-tower refines the Omega-tower": {
        "kind": "python",
        "path": "proofs/strata_tower.py",
        "checker_cmd": f'"{sys.executable}" proofs/strata_tower.py',
    },
    "each strata rung W_k is a coreflector (i_k -| W_k) and the sequence object is the coproduct of its rungs (a Z-graded decomposition)": {
        "kind": "python",
        "path": "proofs/grading.py",
        "checker_cmd": f'"{sys.executable}" proofs/grading.py',
    },
    "Id_seq is naturally isomorphic to the coproduct of the strata rungs: a strong-monoidal resolution of the identity (the grading IS the identity, decomposed)": {
        "kind": "python",
        "path": "proofs/identity_decomposition.py",
        "checker_cmd": f'"{sys.executable}" proofs/identity_decomposition.py',
    },
    "the omega x Omega bigrading unifies both strata towers as commuting idempotents with triangular support, marginals recovering each tower, and a Mobius/inclusion-exclusion inverse": {
        "kind": "python",
        "path": "proofs/bigrading.py",
        "checker_cmd": f'"{sys.executable}" proofs/bigrading.py',
    },
    "the chromatic height tower is a smashing filtration of idempotent localizations with monochromatic layers, convergence, and compatibility with the omega x Omega bigrading": {
        "kind": "python",
        "path": "proofs/chromatic.py",
        "checker_cmd": f'"{sys.executable}" proofs/chromatic.py',
    },
    "lithon is a concrete splitting of the value map: within its 15-prime adelic horizon it realises the chromatic ht / prime-power data exactly, and F_1 (row-0) glues the unit to Spec(Z) -- the same W_0 the identity capstone required": {
        "kind": "python",
        "path": "proofs/lithon.py",
        "checker_cmd": f'"{sys.executable}" proofs/lithon.py',
    },
    "the static adelic shadow factors as the F_1 binomial kernel convolved with the prime-power subset-sum count, and is the orthogonal axis that resolves the residual collision kernel the multiplicative tower could not": {
        "kind": "python",
        "path": "proofs/shadow.py",
        "checker_cmd": f'"{sys.executable}" proofs/shadow.py',
    },
    "the greedy self-syzygy terminates via the F_1 closer and reconstructs exactly; the leading digit is a bounded growth-readout for finite-geometric sequences and diverges for super-exponential ones -- the chestnut cracked regardless of size": {
        "kind": "python",
        "path": "proofs/self_syzygy.py",
        "checker_cmd": f'"{sys.executable}" proofs/self_syzygy.py',
    },
    "the self-shadow multiplicity rho_self is well-defined (>=2 for n>=2) and the F_1 unit is the summatory/zeta operator rho_self(n) = SUM over m<=a_n of rho_hat(m); the self-shadow is a relative invariant that pairwise-separates the recursive corpus -- the chestnut counted regardless of size": {
        "kind": "python",
        "path": "proofs/self_shadow.py",
        "checker_cmd": f'"{sys.executable}" proofs/self_shadow.py',
    },
    "the F_1 radix axis: row-0 read in binary place-value is a bijection (multiplicity 1, dual to the unary C(16,s) zeta kernel) that collapses explosive-term depth from the magnitude a_n to O(log a_n), reconciles exactly on a_n, and carries on the 16-column horizon -- the explosive depth collapsed": {
        "kind": "python",
        "path": "proofs/f1_radix.py",
        "checker_cmd": f'"{sys.executable}" proofs/f1_radix.py',
    },
    "Monstrous Moonshine is F_1 glued to the Monster: the McKay +1 in every graded dimension of V-natural is the F_1 point (the trivial representation), the primes dividing |M| are exactly the 15 supersingular genus-zero primes mirroring the lithon horizon, and the j-coefficients are self-syzygy-crackable (Fibonacci class) and radix-collapsible": {
        "kind": "python",
        "path": "proofs/moonshine.py",
        "checker_cmd": f'"{sys.executable}" proofs/moonshine.py',
    },
    "the strata category is (co)limit-closed: the omega x Omega bigrading cell is the pullback W_i x_S B_j (limit), its dual pushout W_i +_{C_ij} B_j recovers the union with the certified coproduct as the empty-gluing case, and the omega/Omega towers commute via a Mobius-invertible distributive law": {
        "kind": "python",
        "path": "proofs/colimit_closure.py",
        "checker_cmd": f'"{sys.executable}" proofs/colimit_closure.py',
    },
    "the kan layer is a proarrow equipment: every tight arrow has a companion f_! and conjoint f^* satisfying the zig-zag identities, the loose double category is fibrant (restriction = base change via companion/conjoint), and companions/conjoints are pseudofunctorial": {
        "kind": "python",
        "path": "proofs/equipment.py",
        "checker_cmd": f'"{sys.executable}" proofs/equipment.py',
    },
}

CHECKER_TIMEOUT = 60
# Lean+Mathlib builds are minutes, not seconds.
LEAN_CHECKER_TIMEOUT = int(os.environ.get("LEAN_CHECKER_TIMEOUT", "1200"))
LEAN_ALLOW_NATIVE = os.environ.get("LEAN_AUDIT_ALLOW_NATIVE") is not None


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def calx_schema_version(cur) -> object:
    cur.execute(
        "SELECT convert_from(value,'UTF8')::jsonb FROM curry.constants "
        "WHERE id='calx_schema_version' ORDER BY version DESC LIMIT 1"
    )
    row = cur.fetchone()
    return row[0] if row else None


def append_certificate(cur, claim_id, status, evidence, valid_under, statement, kind):
    inf_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO curry.inferences "
        "(inference_id, model_name, model_version, input_tokens, "
        " output_tokens, temperature_used, seed, metadata) "
        "VALUES (%s,'cert-checker-model',1,%s,%s,0.0,0,%s)",
        (
            inf_id,
            json.dumps({"claim_id": claim_id, "statement": statement}),
            status.encode("utf-8"),
            Jsonb({"tier": "formal", "kind": kind}),
        ),
    )
    cur.execute(
        "SELECT COALESCE(MAX(seq),0)+1 FROM cert.certificate WHERE claim_id=%s",
        (claim_id,),
    )
    seq = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cert.certificate "
        "(claim_id, seq, status, evidence, valid_under, checker_inference_id) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (claim_id, seq, status, Jsonb(evidence), Jsonb(valid_under), inf_id),
    )
    return seq


def verify_lean(project_root, file_digests, trusted, checker_cmd, toolchain):
    """Re-check a Lean artifact: closure-drift gate, then build+axiom audit.

    Returns (status, evidence, extra_valid_under). Pure of DB side effects.
    """
    root = (PROJECT_DIR / project_root).resolve()
    extra_vu = {"toolchain": toolchain}
    if leanbridge is None:
        return "error", {"reason": "calx.leanbridge unavailable"}, extra_vu
    if not root.is_dir():
        return "error", {"reason": "lean project_root missing", "path": str(root)}, extra_vu

    # Drift gate: recompute the closure digest over the registered file set.
    rels = list((file_digests or {}).keys())
    try:
        current = leanbridge.closure_digest(
            leanbridge.compute_file_digests(root, rels)
        )
    except FileNotFoundError as exc:
        return "refuted", {"reason": "closure file missing", "detail": str(exc)}, extra_vu
    extra_vu["artifact_sha256"] = current
    if trusted is not None and current != trusted:
        return (
            "refuted",
            {"reason": "lean closure drift (untrusted change)",
             "expected": trusted, "got": current},
            extra_vu,
        )

    # Build + axiom/sorry audit via the registered checker command.
    try:
        proc = subprocess.run(
            checker_cmd, cwd=str(PROJECT_DIR), shell=True,
            capture_output=True, text=True, timeout=LEAN_CHECKER_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "error", {"reason": "lean checker timeout", "checker_cmd": checker_cmd}, extra_vu

    audit = None
    for line in reversed(proc.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                audit = json.loads(line)
                break
            except ValueError:
                continue

    evidence = {
        "checker_cmd": checker_cmd,
        "exit_code": proc.returncode,
        "stdout_tail": proc.stdout.strip()[-600:],
        "stderr_tail": proc.stderr.strip()[-200:],
        "artifact_sha256": current,
        "kind": "lean",
    }
    if audit is None:
        evidence["reason"] = "no JSON verdict from lean checker"
        return "error", evidence, extra_vu

    axioms = audit.get("axioms", [])
    uses_sorry = bool(audit.get("uses_sorry", False))
    evidence.update({
        "decl": audit.get("decl"),
        "type": audit.get("type"),
        "axioms": axioms,
        "uses_sorry": uses_sorry,
        "allow_native": LEAN_ALLOW_NATIVE,
    })
    ok = (proc.returncode == 0) and leanbridge.audit_ok(
        axioms, uses_sorry, allow_native=LEAN_ALLOW_NATIVE
    )
    return ("valid" if ok else "refuted"), evidence, extra_vu


def main() -> int:
    rc = 0
    with psycopg.connect(PG_DSN) as conn:
        conn.execute("SET search_path = calx, curry, kan, cert, public")
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, statement FROM cert.claim WHERE claim_kind='formal' ORDER BY id"
            )
            claims = cur.fetchall()
            if not claims:
                print("no formal claims registered")
                return 0

            for claim_id, statement in claims:
                spec = FORMAL_ARTIFACTS.get(statement)
                cur.execute(
                    "SELECT kind, path, sha256, checker_cmd, "
                    "       project_root, file_digests, toolchain "
                    "FROM cert.artifact WHERE claim_id=%s",
                    (claim_id,),
                )
                art = cur.fetchone()
                project_root = file_digests = toolchain = None

                if art is None:
                    if spec is None:
                        print(f"  [SKIP] claim {claim_id}: no artifact + no known spec")
                        continue
                    # TOFU registration path is for single-file (python/file) artifacts;
                    # Lean artifacts must be registered first via `trunkit register-lean`.
                    fpath = (PROJECT_DIR / spec["path"]).resolve()
                    if not fpath.is_file():
                        print(f"  [ERR ] artifact missing: {fpath}")
                        rc = 1
                        continue
                    digest = sha256_file(fpath)
                    cur.execute(
                        "SELECT cert.register_artifact(%s,%s,%s,%s,%s)",
                        (claim_id, spec["kind"], spec["path"], digest,
                         spec["checker_cmd"]),
                    )
                    kind, path, trusted, checker_cmd = (
                        spec["kind"], spec["path"], digest, spec["checker_cmd"]
                    )
                    print(f"  [TOFU] registered artifact for claim {claim_id}: "
                          f"{path} sha256={digest[:12]}…")
                else:
                    (kind, path, trusted, checker_cmd,
                     project_root, file_digests, toolchain) = art

                vu = {
                    "calx_schema_version": calx_schema_version(cur),
                    "artifact_path": path,
                }

                if kind == "lean":
                    status, evidence, extra_vu = verify_lean(
                        project_root or path, file_digests, trusted,
                        checker_cmd, toolchain,
                    )
                    vu.update(extra_vu)
                else:
                    fpath = (PROJECT_DIR / path).resolve()
                    if not fpath.is_file():
                        status = "error"
                        evidence = {"reason": "artifact missing", "path": str(fpath)}
                    else:
                        current = sha256_file(fpath)
                        vu["artifact_sha256"] = current
                        if trusted is not None and current != trusted:
                            status = "refuted"
                            evidence = {
                                "reason": "artifact hash drift (untrusted change)",
                                "expected": trusted, "got": current,
                            }
                        else:
                            try:
                                proc = subprocess.run(
                                    checker_cmd, cwd=str(PROJECT_DIR), shell=True,
                                    capture_output=True, text=True,
                                    timeout=CHECKER_TIMEOUT,
                                )
                                status = "valid" if proc.returncode == 0 else "refuted"
                                evidence = {
                                    "checker_cmd": checker_cmd,
                                    "exit_code": proc.returncode,
                                    "stdout_tail": proc.stdout.strip()[-400:],
                                    "stderr_tail": proc.stderr.strip()[-200:],
                                    "artifact_sha256": current,
                                    "kind": kind,
                                }
                            except subprocess.TimeoutExpired:
                                status = "error"
                                evidence = {"reason": "checker timeout",
                                            "checker_cmd": checker_cmd}

                seq = append_certificate(
                    cur, claim_id, status, evidence, vu, statement, kind
                )
                if status != "valid":
                    rc = 1
                mark = "OK " if status == "valid" else "!!!"
                print(f"  [{mark}] claim {claim_id} seq{seq} -> {status}  "
                      f"({kind}: {path})")

        conn.commit()

    print(f"\nformal harness complete @ {datetime.now(UTC).isoformat()}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
