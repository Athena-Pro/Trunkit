"""Anchor the September 2026 Navier-Stokes result -- three claims, kept apart.

On 2026-09-08 OpenAI released a construction of finite-time blow-up for the 3D
Navier-Stokes equations under a smooth, decaying external force, with a Lean 4
certificate checked by comparator. It answers the Clay problem's alternatives
(C) (on R^3) and (D) (on the torus) as written. Whether it answers the question
the field means -- which is unforced -- is disputed, and a construction for the
unforced case is reported to fail. That is three different things, and a ledger
that let one stand in for another would be reporting a theorem it does not
have. So:

  P_C, P_D   the forced breakdown statements, bound to their MEANING (117): the
             statement closure of Formal Conjectures' navier_stokes_breakdown_R3
             / _periodic -- the statement OpenAI's comparator challenge was
             adapted from. formal_external, unchecked here: comparator's
             sandbox needs Linux, and the proof is pinned, not vendored.
  I          "this settles Navier-Stokes regularity as the field intends it".
             Anchored with ZERO premise edges. Nothing about (C) is evidence
             for the unforced problem, and the ledger is built so it cannot be
             made to say otherwise by accident.

and the credit graph (114) around them, including a priority claim recorded as
CLAIMED and marked disputed, which transmits nothing.

WHAT THE BINDING DOES LATER. Clone openai/NavierStokesAndEuler at PROOF_COMMIT
on Linux and register it (`trunkit register-lean <P_C> --root <clone> --decl …
--comparator ComparatorChallenges/NavierStokes.json --write`).
Comparator then does the cross-environment statement check. The closure bound
here was taken under Lean v4.27 / Formal Conjectures; the proof builds under
v4.34, so cert.statement_bound will honestly report NOT COMPARABLE until the
closure is rebound in the proof's own environment -- it will not report drift.

Verified by hand on 2026-09-26: upstream Formal Conjectures (2424bb4) changed
the (C)/(D) statement files only in docs, the `research solved` attribute, the
module-system header and two helper-lemma proofs; the statements themselves are
unchanged from the vendored snapshot (0720658) these closures were taken from.

Idempotent: re-running upserts the same rows. Needs an explicit target:

    CALX_TEST_DSN=postgresql://trunk:trunk@localhost:5432/trunk_test \\
        python tools/anchor_navier_stokes.py
    python tools/anchor_navier_stokes.py --dsn <canonical ledger DSN>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from calx import leanbridge  # noqa: E402

PROBLEM = "navier_stokes"

FC_COMMIT = "0720658"  # vendored snapshot the closures were taken in
FC_UPSTREAM = "2424bb480c590237ffbb2cc831ae4cb8977e045a"
# The toolchain the bound closures were taken under. Must be the same string
# shape the harness records (json.dumps(toolchain, sort_keys=True)).
FC_TOOLCHAIN = {"lean": "leanprover/lean4:v4.27.0",
                "mathlib_rev": "a3a10db0e9d66acbebf76c5e6a135066525ac900"}

PROOF_REPO = "https://github.com/openai/NavierStokesAndEuler"
# The commit Formal Conjectures' `formal_proof` attribute cites, not the repo's
# moving HEAD (f9e8bc5b at the time of writing).
PROOF_COMMIT = "8937a8f4cbc7abaab5e9e97d1cc7f5d2319d9538"
PAPER = "https://cdn.openai.com/pdf/32d9f210-8b73-45e0-91bc-82a30aef8a9a/navier-stokes.pdf"

AUDITS = REPO / "proofs" / "navier_stokes"

STATEMENTS = {
    "C": ("NavierStokes.navier_stokes_breakdown_R3", "fc_breakdown_R3.audit.json",
          "Navier-Stokes (Clay C): for every nu > 0 there are smooth decaying u0 and a "
          "smooth decaying force f on R^3 for which no smooth finite-energy solution "
          "exists for all time", "NavierStokes.json"),
    "D": ("NavierStokes.navier_stokes_breakdown_periodic", "fc_breakdown_periodic.audit.json",
          "Navier-Stokes (Clay D): for every nu > 0 there are smooth periodic u0 and a "
          "smooth periodic force f on R^3/Z^3 for which no smooth periodic solution "
          "exists for all time", "NavierStokes.json"),
}

INTERPRETATION = (
    "Navier-Stokes: the forced breakdown (Clay C/D) settles the regularity question "
    "as the field intends it, i.e. for the unforced equations")


def _dsn(arg: str | None) -> str:
    dsn = arg or os.environ.get("CALX_TEST_DSN") or os.environ.get("ARITHMETIC_DB_TEST_DSN")
    if not dsn:
        sys.exit("Pass --dsn, or set CALX_TEST_DSN to a test database.")
    return dsn


def _anchor(cur, statement: str, subject_ref: dict) -> int:
    cur.execute(
        "INSERT INTO cert.claim (subject_kind, subject_ref, statement, claim_kind,"
        " method, probe_sql, domain)"
        " VALUES ('millennium_problem', %s, %s, 'formal', 'formal_external', NULL,"
        " 'unspecified')"
        " ON CONFLICT (statement) DO UPDATE SET subject_ref = EXCLUDED.subject_ref"
        " RETURNING id",
        (Jsonb(subject_ref), statement))
    return cur.fetchone()[0]


def _audit(name: str) -> dict:
    return json.loads((AUDITS / name).read_text(encoding="utf-8").strip().splitlines()[-1])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dsn")
    args = ap.parse_args(argv)

    with psycopg.connect(_dsn(args.dsn), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regprocedure('cert.bind_statement_closure("
                    "bigint,text,text,text,jsonb,text)'), to_regclass('prov.artifact')")
        if None in cur.fetchone():
            sys.exit("Schema too old - apply 114 and 117 (trunkit init).")

        tc = json.dumps(FC_TOOLCHAIN, sort_keys=True)
        claims: dict[str, int] = {}
        print("Forced breakdown statements, bound to their meaning (117)")
        for key, (decl, audit_file, stmt, challenge) in STATEMENTS.items():
            audit = _audit(audit_file)
            manifest = leanbridge.statement_closure(audit)
            sha = leanbridge.closure_manifest_digest(manifest)
            claims[key] = cid = _anchor(cur, stmt, {
                "problem": PROBLEM, "clay_alternative": key,
                "statement_decl": decl,
                "statement_source": f"formal-conjectures@{FC_COMMIT} "
                                    f"(unchanged at {FC_UPSTREAM[:7]})",
                "proof": f"{PROOF_REPO}/commit/{PROOF_COMMIT}",
                "checker": f"comparator: ComparatorChallenges/{challenge}",
                "paper": PAPER,
                "note": "unchecked here: comparator's sandbox is Linux-only and the "
                        "proof is pinned, not vendored"})
            cur.execute("SELECT (cert.bind_statement_closure(%s,%s,%s,%s,%s,%s)).id",
                        (cid, decl, audit["type"], sha,
                         Jsonb({k: list(v) for k, v in manifest.items()}), tc))
            bid = cur.fetchone()[0]
            print(f"  ({key}) claim #{cid}  binding #{bid}  {decl}")
            print(f"        closure {len(manifest)} constants, sha256 {sha[:16]}...")

        claims["I"] = interp = _anchor(cur, INTERPRETATION, {
            "problem": PROBLEM,
            "note": "Not a mathematical statement the ledger can check. Clay's text "
                    "admits forcing; the field's question is unforced, and Formal "
                    "Conjectures now labels (A)/(B) '(unforced)' and leaves them open. "
                    "A forced result is not evidence for it: zero premise edges, by "
                    "construction.",
            "against": "reported failure of the construction without forcing "
                       "(Scientific American, 2026-09); cf. arXiv:2609.26790"})
        print(f"\nInterpretation anchored: claim #{interp}")

        # ---------------- credit graph (114) ----------------
        contributors = [
            ("Clay Mathematics Institute", "literature", "peer_reviewed",
             "https://www.claymath.org/millennium/navier-stokes-equation/"),
            ("Córdoba & Martínez-Zoroa", "literature", "unrated", None),
            ("Formal Conjectures", "tool", "community",
             "https://github.com/google-deepmind/formal-conjectures"),
            ("OpenAI internal model (2026-09)", "ai_system", "self_reported", PAPER),
            ("openai/NavierStokesAndEuler", "tool", "self_reported", PROOF_REPO),
            ("unforced-case critics (press reports, 2026-09)", "literature", "unrated", None),
            ("priority claimant (NYU, per press reports)", "human", "self_reported", None),
        ]
        for name, kind, tier, ident in contributors:
            cur.execute("SELECT prov.register_contributor(%s,%s,%s,%s)",
                        (name, kind, tier, ident))

        def art(kind, who, title, locator=None, claim=None, grade="ungraded") -> int:
            cur.execute("SELECT prov.register_artifact(%s,%s,%s,%s,%s,%s,%s)",
                        (PROBLEM, kind, who, title, locator, claim, grade))
            return cur.fetchone()[0]

        clay = art("prior_literature", "Clay Mathematics Institute",
                   "Fefferman, official problem statement (2000), alternatives A-D",
                   contributors[0][3])
        cascade = art("prior_literature", "Córdoba & Martínez-Zoroa",
                      "infinite-cascade constructions under smooth forcing")
        fc = art("lean_artifact", "Formal Conjectures",
                 "Lean statement of alternatives A-D (Millenium/NavierStokes.lean)",
                 f"{contributors[2][3]}@{FC_UPSTREAM}")
        paper = art("ai_output", "OpenAI internal model (2026-09)",
                    "Finite time blowup for Navier-Stokes (forced)", PAPER, grade="full")
        cert_c = art("lean_artifact", "openai/NavierStokesAndEuler",
                     "Lean certificate for (C)", f"{PROOF_REPO}@{PROOF_COMMIT}",
                     claim=claims["C"])
        cert_d = art("lean_artifact", "openai/NavierStokesAndEuler",
                     "Lean certificate for (D)", f"{PROOF_REPO}@{PROOF_COMMIT}",
                     claim=claims["D"])
        critics = art("prior_literature", "unforced-case critics (press reports, 2026-09)",
                      "the construction does not survive removing the force",
                      "https://www.scientificamerican.com/article/"
                      "did-openai-solve-the-wrong-navier-stokes-problem/")
        priority = art("prior_literature", "priority claimant (NYU, per press reports)",
                       "claimed unpublished prior work (DISPUTED)")

        edges = [
            (clay, fc, "formalized", None),
            (cascade, paper, "used", None),
            (paper, cert_c, "formalized", None),
            (paper, cert_d, "formalized", None),
            (fc, cert_c, "used", "comparator challenge adapted from Formal Conjectures"),
            (fc, cert_d, "used", "comparator challenge adapted from Formal Conjectures"),
            (priority, paper, "prior_literature",
             "DISPUTED: recorded as claimed, not established; binds no claim, "
             "so it transmits nothing"),
        ]
        for src, dst, rel, note in edges:
            cur.execute("SELECT prov.link(%s,%s,%s,%s)", (src, dst, rel, note))
        print(f"Credit graph: 8 artifacts, {len(edges)} edges"
              f" (critics node #{critics} deliberately unlinked: it bears on I, "
              "and no relation means 'against')")

        # ---------------- the rule the whole file exists for ----------------
        cur.execute("SELECT count(*) FROM cert.derivation WHERE conclusion_id = %s",
                    (interp,))
        premises = cur.fetchone()[0]
        assert premises == 0, "the interpretation claim grew a premise edge"

        print("\nStanding")
        for key in ("C", "D", "I"):
            cur.execute("SELECT coalesce((SELECT effective_status FROM cert.standing"
                        " WHERE claim_id = %s), 'unchecked')", (claims[key],))
            status = cur.fetchone()[0]
            cur.execute("SELECT ok, evidence->>'reason' FROM cert.statement_bound(%s)",
                        (claims[key],))
            ok, reason = cur.fetchone()
            print(f"  {key}: claim #{claims[key]:<6} {status:10} binding ok={ok}"
                  f"{'  — ' + reason if reason else ''}")
        print(f"\n  premise edges into I: {premises}   <-- must be 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
