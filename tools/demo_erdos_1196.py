"""The #1196 baseline demonstration -- every tier, end to end, in one run.

Erdos #1196: for a primitive set A (no element of A divides another), is
sum 1/(a log a) maximised by the primes? The capability report picks this as the
baseline demo precisely because it SPLITS so cleanly:

    finitely checkable    is THIS set primitive? -- decided exactly, in SQL
    not finitely checkable the conjecture itself -- anchored, never derived

So the run below exercises the whole stack and, at the last step, refuses to
join the two halves:

    T2  comb    deposit four integer sets; probe primitivity and carry the
                object as the witness                       (110, 113, 116)
    T3  cert    the count of a primitive subset of [1..N] as a two-sided
                numeric bound                                     (105)
    T1  formal  anchor the asymptotic statement as formal_external -- no
                probe, no premises, unchecked until Lean runs      (41, 104)
    T5  prov    who contributed what: literature, AI, Lean, human, and what
                happens to the chain when one is withdrawn         (114)

The point of the last step is the one a demo usually fudges. Everything above it
is `valid`; the theorem stays `unchecked`, with zero premise edges. A ledger that
let a primitive set support a conjecture about all primitive sets would be
reporting a theorem as proved because one example behaved.

Contributor names are illustrative scaffolding for the graph SHAPE. This script
asserts nothing about who really proved what.

Re-runnable and idempotent. Point CALX_TEST_DSN at a test database:

    CALX_TEST_DSN=postgresql://trunk:trunk@localhost:5432/trunk_test \
        python tools/demo_erdos_1196.py
"""

from __future__ import annotations

import os
import sys
import uuid

import psycopg

PROBLEM = "erdos_1196"

# Where the asymptotic statement is proved and formalized. The anchor used to
# cite arXiv:2601.07421 -- that is the Aristotle writeup of #728, a different
# problem. #1196's proof is Tao's (arXiv:2605.00301); LeanMarathon
# (arXiv:2606.05400) formalized it with no sorry. Pinned by commit so the
# locator names one proof, not a moving branch.
PAPER = "arXiv:2605.00301"
LEAN_REPO = "https://github.com/YuanheZ/LeanMarathon"
LEAN_COMMIT = "e2febe2ce717ef5d8410909683f6f5b301bda4c2"
LOCATOR = f"{PAPER}; Lean: {LEAN_REPO}@{LEAN_COMMIT} (arXiv:2606.05400)"

# Four sets: three primitive for different reasons, one a maximal chain.
SETS = {
    "primes_to_50": (
        [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47],
        "the primes below 50 — the conjectured maximiser",
    ),
    "half_open_interval": (
        list(range(26, 51)),
        "(N/2, N] for N=50 — primitive because 2a > N for every a",
    ),
    "squarefree_semiprimes": (
        [6, 10, 14, 15, 21, 22, 26, 33, 35],
        "products of two distinct primes — primitive, and not coprime",
    ),
    "powers_of_two": (
        [2, 4, 8, 16, 32],
        "a chain — maximally NON-primitive, one witness per pair",
    ),
}


def _dsn() -> str:
    dsn = os.environ.get("CALX_TEST_DSN") or os.environ.get("ARITHMETIC_DB_TEST_DSN")
    if not dsn:
        sys.exit("Set CALX_TEST_DSN to a test database (not the canonical ledger).")
    return dsn


def _rule(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def deposit(cur, tag: str, name: str, labels: list[int], note: str) -> str:
    subject = f"{PROBLEM}-{tag}-{name}"
    cur.execute("SELECT comb.register_structure(%s,'set_system',%s,%s)",
                (subject, len(labels), note))
    cur.execute("SELECT comb.label_elements(%s,%s)", (subject, [str(x) for x in labels]))
    return subject


def main() -> int:
    tag = uuid.uuid4().hex[:8]
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regprocedure('comb.is_primitive_set(text)')")
        if cur.fetchone()[0] is None:
            sys.exit("Schema too old — apply 116_comb_primitive_set.sql (trunkit init).")

        # ---------------- T2: finite witnesses ----------------
        _rule("T2 — finite primitivity and divisibility witnesses  (110/113/116)")
        claims: dict[str, int] = {}
        for name, (labels, note) in SETS.items():
            subject = deposit(cur, tag, name, labels, note)
            cur.execute("SELECT ok, evidence, witness FROM comb.carried_primitive_set(%s)",
                        (subject,))
            ok, ev, witness = cur.fetchone()
            cur.execute("SELECT comb.primitive_set_claim(%s)", (subject,))
            claim = claims[name] = cur.fetchone()[0]

            print(f"\n  {name}  ({len(labels)} elements)  — {note}")
            print(f"    primitive : {ok}   claim #{claim}")
            if ok:
                print(f"    checked   : {ev['pairs_checked']} ordered pairs, "
                      f"{ev['repeated_labels']} repeated labels")
            else:
                chains = ev["divisibility_chains"]
                print(f"    refuted   : {ev['violations']} dividing pairs")
                for c in chains[:4]:
                    print(f"                {c['divisor']} | {c['multiple']}"
                          f"  (quotient {c['quotient']})")
                if len(chains) > 4:
                    print(f"                … and {len(chains) - 4} more")
            print(f"    witness   : the set itself travels "
                  f"({len(witness['elements'])} labels carried)")

        # ---------------- T3: a bound over the finite object ----------------
        _rule("T3 — the finite count as a two-sided numeric bound  (105)")
        # |(N/2, N]| = 25 for N = 50, and it is a known extremal size for a
        # primitive subset of [1..N]. Both sides are the same number, so the
        # enclosure closes to width 0 -- the same shape as Mantel in 113.
        q = f"{PROBLEM}-{tag}-max-primitive-subset-of-1..50"
        cur.execute("SELECT (cert.register_quantity(%s,%s)).id",
                    (q, "largest primitive subset of [1..50]"))
        for side, note in (("lower", "the interval (25,50] realises it"),
                           ("upper", "no primitive subset of [1..50] is larger")):
            cur.execute("SELECT (cert.register_bound(%s,%s,%s,false,'{}',%s)).id",
                        (q, side, 25, note))
            print(f"  {side:5} bound 25 — {note}   (bound #{cur.fetchone()[0]})")
        cur.execute("SELECT lower_value, upper_value, width FROM cert.enclosure(%s)", (q,))
        lo, hi, width = cur.fetchone()
        print(f"  enclosure       : [{lo}, {hi}]  width {width}"
              "   — construction meets theorem")

        # ---------------- T1: the anchor ----------------
        _rule("T1 — the asymptotic statement, anchored  (41/104)")
        stmt = (f"[{tag}] Erdos #1196: for every primitive set A, "
                "sum_{a in A} 1/(a log a) <= sum_{p prime} 1/(p log p)")
        cur.execute("SELECT comb.anchor_asymptotic(%s,%s,%s,%s)",
                    (stmt, PROBLEM, list(claims.values()), LOCATOR))
        anchor = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM cert.derivation WHERE conclusion_id = %s",
                    (anchor,))
        premises = cur.fetchone()[0]
        cur.execute("SELECT coalesce(effective_status,'unchecked') FROM cert.standing"
                    " WHERE claim_id = %s", (anchor,))
        row = cur.fetchone()
        print(f"  claim #{anchor}  method=formal_external  probe=NULL")
        print(f"  standing        : {row[0] if row else 'unchecked'}")
        print(f"  premise edges   : {premises}   <-- must be 0")
        print(f"  finite evidence : {list(claims.values())} (recorded as motivation only)")
        print(f"  proof           : {PAPER}")
        print(f"  formalization   : {LEAN_REPO}@{LEAN_COMMIT[:12]}")
        print("  to CHECK it     : clone at that commit, then")
        print(f"                    trunkit register-lean {anchor} --root <clone> "
              "--decl <#1196 theorem> --write")
        print("                    lean_check.sh <clone> <decl> > audit.json")
        print(f"                    trunkit bind-statement {anchor} --audit audit.json --write")
        print("                    trunkit attest --write")
        assert premises == 0, "the asymptotic anchor grew a premise edge"

        # ---------------- T5: provenance ----------------
        _rule("T5 — the credit structure, and what a withdrawal costs  (114)")
        # Each finite claim is CHECKED, not asserted -- cert.check re-runs the
        # probe -- so the standings below are earned. The literature node binds
        # no claim on purpose: a paper has no in-DB probe, and `unverified` is
        # the honest word for that rather than a defect in the demo.
        for claim in claims.values():
            cur.execute("SELECT (cert.check(%s)).status", (claim,))

        people = [
            (f"lit-{tag}",   "literature", "peer_reviewed",  "prior_literature",
             "prior bound in the literature", None),
            (f"ai-{tag}",    "ai_system",  "self_reported",  "ai_output",
             "AI-proposed primitive family", claims["primes_to_50"]),
            (f"lean-{tag}",  "tool",       "machine_checked", "lean_artifact",
             "Lean formalization", claims["half_open_interval"]),
            (f"human-{tag}", "human",      "community",      "human_step",
             "human verification", claims["squarefree_semiprimes"]),
        ]
        artifacts = []
        for who, ckind, tier, akind, title, claim in people:
            cur.execute("SELECT prov.register_contributor(%s,%s,%s,NULL)",
                        (who, ckind, tier))
            cur.execute("SELECT prov.register_artifact(%s,%s,%s,%s,NULL,%s,'full')",
                        (PROBLEM, akind, who, f"[{tag}] {title}", claim))
            artifacts.append(cur.fetchone()[0])

        for src, dst, rel in ((artifacts[0], artifacts[1], "prior_literature"),
                              (artifacts[1], artifacts[2], "formalized"),
                              (artifacts[2], artifacts[3], "used")):
            cur.execute("SELECT prov.link(%s,%s,%s,NULL)", (src, dst, rel))
        print("  4 artifacts, 3 edges (prior_literature -> formalized -> used)")

        def show(header: str) -> None:
            cur.execute(
                "SELECT contributor, contributor_kind, trust_tier, effective_status,"
                " coalesce(taint_status,'-') FROM prov.standing"
                " WHERE problem = %s AND title LIKE %s ORDER BY contributor",
                (PROBLEM, f"[{tag}]%"))
            print(f"\n  {header}")
            print(f"    {'contributor':16} {'kind':12} {'tier':16} {'standing':10} taint")
            for row in cur.fetchall():
                print(f"    {row[0][:15]:16} {row[1]:12} {row[2]:16} {row[3]:10} {row[4]}")

        show("credit structure, all checked:")

        # The withdrawal. Revoking the AI output must reach the Lean artifact and
        # the human check downstream of it -- through cert.derivation and 102's
        # closure, not through anything prov walks itself.
        cur.execute("SELECT cert.revoke_claim(%s,%s,'{}'::jsonb)",
                    (claims["primes_to_50"], "demo: AI output withdrawn"))
        show("after withdrawing the AI output:")
        cur.execute("SELECT count(*) FROM cert.tainted_closure WHERE claim_id = ANY(%s)",
                    ([claims["half_open_interval"], claims["squarefree_semiprimes"]],))
        print(f"\n    {cur.fetchone()[0]} downstream claim(s) tainted — and note the"
              " literature node,")
        print("    upstream of the withdrawal, is untouched. Taint flows one way.")

        _rule("Summary")
        cur.execute("SELECT count(*) FROM cert.derivation WHERE conclusion_id = %s",
                    (anchor,))
        print(f"  finite claims minted     : {len(claims)} (exact, re-runnable) — "
              f"{sum(1 for n in claims if n != 'powers_of_two')} valid, 1 refuted")
        print(f"  asymptotic anchor        : #{anchor}, {cur.fetchone()[0]} premises, "
              f"unchecked until LeanMarathon's proof is checked here")
        print("  the conjecture is NOT supported by the finite work, by construction.")
        print("\n  A primitive set is a fact. #1196 is not. The ledger keeps them apart.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
