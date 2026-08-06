"""Demo of the numeric-bound certificate tier (105) -- gap T3 of
docs/reports/Erdos_Missing_Capabilities_Report.md (PR #36).

Corpus: the two bound results in arXiv:2607.06447 (Danus), chosen because
they are the two shapes the tier has to handle and they come from the same
paper, so the demo is checkable against a single source.

  1. The optimal bend-and-break constant for rank-r foliations (Sec 3.1).
     A sequence of competing UPPER bounds on one constant, improved twice in
     the literature, ending in a bound proved optimal. Exercises: domination,
     frontier, optimality-with-attainment, and -- once a conditional bound is
     added -- genuine incomparability.

  2. The Matryoshka constant S (Sec 3.4, OEIS A177384). A two-sided
     ENCLOSURE of width < 1e-9 around a constant with no known closed form,
     plus Kotesovec's conjectured value. Exercises: enclosure width, and the
     rule that a conjectural value must enter as a hypothesis-bearing bound
     so it never contaminates the unconditional frontier.

Then it does the thing the tier exists for: registers a bound with a
transcription error and shows the consistency claim flipping to refuted with
both colliding bounds named.

Read-only against any ledger but its own rows; point CALX_TEST_DSN at a test
database. Re-runnable.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

import psycopg

# The bend-and-break constants of Sec 3.1, evaluated at a fixed shape so the
# competing bounds are comparable numbers. n = ambient dimension, r = rank of
# the foliation. The whole point of the 2026 result is that the constant
# depends on r, not n, so a shape with r < n is what makes the three bounds
# differ.
N_AMBIENT = 3
R_RANK = 2

# Theorem 1 of arXiv:2607.06447 as transcribed. The digest below is over
# exactly this text: the transcription-integrity claim re-checks it, which is
# the check that would have caught the Sec 3.3 defect (Danus read
# "dimension >= 3" as "dimension > 3" from a PDF and lost the codimension-3
# case until a human traced it).
THEOREM_TEXT = (
    "Let X be a normal projective variety of dimension n, let F be a foliation "
    "on X of rank r, and let H_1, ..., H_{n-1}, H be ample divisors on X. Let C "
    "be a general complete intersection of elements of |m_i H_i| with m_i >> 0, "
    "and suppose K_F . C < 0. Then through a general point of C there is a "
    "rational curve Sigma tangent to F with H . Sigma <= (r+1) H.C / (-K_F.C), "
    "and the constant r+1 is optimal."
)

QTY_BB = f"bend_and_break_constant_n{N_AMBIENT}_r{R_RANK}"
QTY_MAT = "matryoshka_constant_S"


def dsn() -> str:
    d = os.environ.get("CALX_TEST_DSN")
    if not d:
        sys.exit(
            "Set CALX_TEST_DSN to an isolated database. This demo writes "
            "claims and bounds; it must not touch a production ledger."
        )
    return d


def rule(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def show(cur, sql, params=None, indent="  ") -> list:
    cur.execute(sql, params or ())
    rows = cur.fetchall()
    cols = [d.name for d in cur.description]
    for row in rows:
        print(indent + " | ".join(f"{c}={v}" for c, v in zip(cols, row, strict=True)))
    if not rows:
        print(indent + "(no rows)")
    return rows


def register_transcription_claim(cur) -> int:
    """A re-checkable claim that the theorem text hashes to a recorded digest.

    This certifies TRANSCRIPTION, not mathematics: the ledger's honest
    statement about a result it read out of a paper. The bound's optimality
    claim then rests on this, via 104's attestation axiom, so revoking the
    transcription degrades the optimality claim through 102's deep check.
    """
    digest = hashlib.sha256(THEOREM_TEXT.encode("utf-8")).hexdigest()
    stmt = (
        "transcription integrity: Theorem 1 of arXiv:2607.06447 "
        f"(optimal bend-and-break for foliations) hashes to sha256:{digest[:16]}..."
    )
    probe = (
        "SELECT encode(sha256(convert_to($txt${}$txt$, 'UTF8')), 'hex') = {} AS ok,"
        " jsonb_build_object('source', 'arXiv:2607.06447 Theorem 1',"
        " 'sha256', {}, 'chars', length($txt${}$txt$)) AS evidence"
    ).format(
        THEOREM_TEXT,
        f"'{digest}'",
        f"'{digest}'",
        THEOREM_TEXT,
    )
    cur.execute(
        "INSERT INTO cert.claim"
        " (subject_kind, subject_ref, statement, claim_kind, method, probe_sql)"
        " VALUES ('bound', %s, %s, 'computational', 'comp_sql', %s)"
        " ON CONFLICT (statement) DO UPDATE SET probe_sql = EXCLUDED.probe_sql"
        " RETURNING id",
        (json.dumps({"source": "arXiv:2607.06447", "theorem": 1}), stmt, probe),
    )
    claim_id = cur.fetchone()[0]
    cur.execute("SELECT (cert.check(%s)).status", (claim_id,))
    status = cur.fetchone()[0]
    print(f"  transcription claim #{claim_id}: {status}")

    # Bind it as a tool-attestation axiom (104). The axiom name is a foreign
    # key back into the ledger, so a Lean proof may cite the transcription
    # explicitly rather than smuggling the paper's statement in as folklore.
    cur.execute("SELECT (cert.register_attestation(%s, %s)).axiom_name", (claim_id, digest))
    print(f"  attestation axiom: {cur.fetchone()[0]}")
    return claim_id


def main() -> int:
    with psycopg.connect(dsn(), connect_timeout=5) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('cert.bound')")
            if cur.fetchone()[0] is None:
                sys.exit("cert.bound missing -- apply 105_cert_bound.sql first")

            # ---------------------------------------------------------------
            rule("1. Competing bounds on one constant (Danus Sec 3.1)")

            cur.execute(
                "SELECT (cert.register_quantity(%s, %s, %s)).id",
                (
                    QTY_BB,
                    f"optimal constant c in the bend-and-break bound H.Sigma <= "
                    f"c * H.C / (-K_F.C) for a rank-{R_RANK} foliation on a "
                    f"{N_AMBIENT}-fold",
                    json.dumps({"source": "arXiv:2607.06447 Sec 3.1", "n": N_AMBIENT, "r": R_RANK}),
                ),
            )
            truth_claim = register_transcription_claim(cur)

            bounds = [
                # value, hypotheses, source, attained_by, claim
                (2 * N_AMBIENT, [], "Shepherd-Barron 1992 (2n)", None, None),
                (2 * R_RANK, [], "Bogomolov-McQuillan 2016 (2r)", None, None),
                (
                    R_RANK + 1,
                    [],
                    "Jovinelly-Lehmann-Riedl / Liu-Sun-Jiang 2026 (r+1)",
                    json.dumps({"attained": "the constant r+1 is proved optimal",
                                "reference": "arXiv:2607.06447 Theorem 1"}),
                    truth_claim,
                ),
                # A deliberately synthetic conditional bound -- NOT from the
                # literature -- included only to show that a tighter bound
                # under a stronger hypothesis does not dominate.
                (
                    2.5,
                    ["demo_only:algebraically_integrable_leaves"],
                    "DEMO ONLY -- synthetic conditional bound, not a literature result",
                    None,
                    None,
                ),
            ]
            ids = {}
            for value, hyps, source, attained, claim in bounds:
                cur.execute(
                    "SELECT (cert.register_bound(%s,'upper',%s::numeric,false,%s,%s,%s,%s)).id",
                    (QTY_BB, str(value), hyps, source, claim, attained),
                )
                ids[source] = cur.fetchone()[0]
                print(f"  bound #{ids[source]}: <= {value}  {source}")

            rule("2. The partial order")
            sb = ids["Shepherd-Barron 1992 (2n)"]
            bmq = ids["Bogomolov-McQuillan 2016 (2r)"]
            opt = ids["Jovinelly-Lehmann-Riedl / Liu-Sun-Jiang 2026 (r+1)"]
            cond = ids["DEMO ONLY -- synthetic conditional bound, not a literature result"]
            for a, b, label in [
                (bmq, sb, "2r dominates 2n            (tighter, both unconditional)"),
                (opt, bmq, "r+1 dominates 2r           (tighter, both unconditional)"),
                (opt, sb, "r+1 dominates 2n           (transitivity)"),
                (cond, opt, "conditional 2.5 vs r+1     (tighter BUT assumes more)"),
                (opt, cond, "r+1 vs conditional 2.5     (weaker hypotheses BUT looser)"),
            ]:
                cur.execute("SELECT cert.bound_dominates(%s, %s)", (a, b))
                print(f"  {label} -> {cur.fetchone()[0]}")

            print("\n  frontier (nothing dominates these):")
            show(cur, "SELECT * FROM cert.bound_frontier(%s)", (QTY_BB,))
            print("\n  ^ two incomparable elements: the order is genuinely partial.")

            rule("3. Optimality: three-valued")
            for bid, label in [
                (opt, "r+1  (undominated, attained, truth-claim stands)"),
                (bmq, "2r   (dominated by r+1)"),
                (cond, "2.5  (undominated, but no attaining witness)"),
            ]:
                cur.execute("SELECT ok, evidence FROM cert.bound_optimal(%s)", (bid,))
                ok, ev = cur.fetchone()
                verdict = {True: "VALID", False: "REFUTED", None: "UNVERIFIED"}[ok]
                reason = ev.get("reason", "optimal")
                print(f"  {label}\n      -> {verdict}: {reason}")

            cur.execute("SELECT cert.bound_optimality_claim(%s)", (opt,))
            oc = cur.fetchone()[0]
            cur.execute("SELECT (cert.check(%s)).status", (oc,))
            print(f"\n  optimality claim #{oc} via cert.check: {cur.fetchone()[0]}")

            # ---------------------------------------------------------------
            rule("4. An enclosure (Danus Sec 3.4, OEIS A177384)")

            cur.execute(
                "SELECT (cert.register_quantity(%s, %s, %s)).id",
                (
                    QTY_MAT,
                    "S = lim a_n / (n! n^4) for the Matryoshka numbers A177384",
                    json.dumps({"oeis": "A177384", "source": "arXiv:2607.06447 Sec 3.4"}),
                ),
            )
            for direction, value, hyps, source in [
                ("lower", "0.00542831750", [], "Liu 2026, Matryoshka note (proved enclosure)"),
                ("upper", "0.00542831848", [], "Liu 2026, Matryoshka note (proved enclosure)"),
                # Kotesovec's value is CONJECTURAL and rounded. It enters under
                # an explicit hypothesis so it can never join the unconditional
                # frontier, and as an interval so its rounding is not mistaken
                # for precision it does not have.
                ("lower", "0.00542825", ["conjecture:Kotesovec_A177384"],
                 "Kotesovec (OEIS A177384), conjectural, ~5 s.f."),
                ("upper", "0.00542835", ["conjecture:Kotesovec_A177384"],
                 "Kotesovec (OEIS A177384), conjectural, ~5 s.f."),
            ]:
                cur.execute(
                    "SELECT (cert.register_bound(%s,%s,%s::numeric,false,%s,%s)).id",
                    (QTY_MAT, direction, value, hyps, source),
                )
                print(f"  bound #{cur.fetchone()[0]}: {direction} {value}  {source}")

            print("\n  unconditional enclosure:")
            show(cur, "SELECT * FROM cert.enclosure(%s, '{}')", (QTY_MAT,))
            print("\n  allowing the conjecture:")
            show(
                cur,
                "SELECT * FROM cert.enclosure(%s, %s)",
                (QTY_MAT, ["conjecture:Kotesovec_A177384"]),
            )

            cur.execute("SELECT cert.bound_consistency_claim(%s)", (QTY_MAT,))
            cc = cur.fetchone()[0]
            cur.execute("SELECT (cert.check(%s)).status", (cc,))
            print(f"\n  consistency claim #{cc}: {cur.fetchone()[0]}")

            # ---------------------------------------------------------------
            rule("5. What the tier is FOR: a transcription error, caught")

            print("  Registering a lower bound with a digit slip (0.0055 instead")
            print("  of 0.00542...) -- the shape of the Sec 3.3 defect.\n")
            cur.execute(
                "SELECT (cert.register_bound(%s,'lower',%s::numeric,false,'{}',%s)).id",
                (QTY_MAT, "0.0055", "DEMO ONLY -- simulated transcription error"),
            )
            bad = cur.fetchone()[0]
            print(f"  bound #{bad}: lower 0.0055")

            cur.execute("SELECT (cert.check(%s)).status, (cert.check(%s)).evidence", (cc, cc))
            status, ev = cur.fetchone()
            print(f"\n  consistency claim #{cc} re-checked: {status.upper()}")
            conflicts = ev.get("evidence", ev).get("conflicts", [])
            for c in conflicts:
                print(
                    f"    collision: lower #{c['lower_bound_id']} = {c['lower_value']}"
                    f"  >  upper #{c['upper_bound_id']} = {c['upper_value']}"
                )
                print(f"      lower source: {c['lower_source']}")
                print(f"      upper source: {c['upper_source']}")

            print("\n  The colliding PAIR is named, so the defect is localised to")
            print("  two rows rather than reported as a bare false.")

            rule("6. Cleanup")
            cur.execute("DELETE FROM cert.bound WHERE id = %s", (bad,))
            cur.execute("SELECT (cert.check(%s)).status", (cc,))
            print(f"  removed the bad bound; consistency claim #{cc}: {cur.fetchone()[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
