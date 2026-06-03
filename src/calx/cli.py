"""trunkit command-line entry point.

Consumer commands (read-only, safe for LLM use):
    trunkit verify <claim_id>
    trunkit standing [--method M] [--status S]
    trunkit export <id> [<id> ...]

Prover commands (require --write to record; dry-run otherwise):
    trunkit check <claim_id> [--write]
    trunkit attest [--write]
    trunkit close [--write]
    trunkit witness <claim_id> --kind KIND --body JSON [--write]

calx data commands:
    trunkit init
    trunkit generate --limit N [--backend primesieve|pure]
    trunkit validate [--limit N]
    trunkit reset
    trunkit oeis-load [--family F] [--seq-id A000000] [--backfill-only]
    trunkit oeis-match [--orbit-id ID | --all] [--min-length N] [--prefix N]
    trunkit compose-match [--static-only] [--orbit-only] [--no-oeis] [--prefix N]
"""

from __future__ import annotations

import argparse
import json
import sys

from . import db, generate, validate

# ---------------------------------------------------------------------------
# Consumer commands — read-only, no --write gate
# ---------------------------------------------------------------------------

def _cmd_verify(args: argparse.Namespace) -> int:
    with db.connect(args.dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ok, evidence, witness FROM cert.verify(%s)",
            (args.claim_id,),
        )
        row = cur.fetchone()
    if row is None:
        print(f"  claim {args.claim_id} not found")
        return 1
    ok, evidence, witness = row
    status = "VALID" if ok is True else "REFUTED" if ok is False else "UNVERIFIED"
    mark = "✓" if ok is True else "✗" if ok is False else "?"
    print(f"  [{mark}] claim {args.claim_id}  →  {status}")
    if evidence:
        print(f"  evidence : {json.dumps(evidence, indent=4)}")
    if witness:
        print(f"  witness  : {json.dumps(witness, indent=4)}")
    return 0 if ok is True else 1


def _cmd_standing(args: argparse.Namespace) -> int:
    where, params = [], []
    if args.method:
        where.append("method = %s")
        params.append(args.method)
    if args.status:
        where.append("status = %s")
        params.append(args.status)
    sql = "SELECT claim_id, statement, method, status, checked_at FROM cert.standing"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY claim_id"
    with db.connect(args.dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    if not rows:
        print("  (no claims match)")
        return 0
    for id_, stmt, method, status, checked_at in rows:
        mark = "✓" if status == "valid" else "✗" if status == "refuted" else "?"
        ts = checked_at.strftime("%Y-%m-%d %H:%M") if checked_at else "—"
        print(f"  [{mark}] #{id_:>3}  {method:<20}  {status:<12}  {ts}  {stmt[:55]}")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    with db.connect(args.dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT cert.export_bundle(%s::bigint[])",
            (list(args.claim_ids),),
        )
        bundle = cur.fetchone()[0]
    print(json.dumps(bundle, indent=2, default=str))
    return 0


# ---------------------------------------------------------------------------
# Prover commands — require --write to record; dry-run otherwise
# ---------------------------------------------------------------------------

def _cmd_check(args: argparse.Namespace) -> int:
    if not args.write:
        with db.connect(args.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT ok, evidence FROM cert.verify(%s)",
                (args.claim_id,),
            )
            row = cur.fetchone()
        if row is None:
            print(f"  claim {args.claim_id} not found")
            return 1
        ok, evidence = row
        status = "valid" if ok is True else "refuted" if ok is False else "unverified"
        print(f"  [dry-run] claim {args.claim_id} would attest as: {status}")
        print("  pass --write to record a certificate")
        return 0

    with db.connect(args.dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT method FROM cert.claim WHERE id = %s",
            (args.claim_id,),
        )
        row = cur.fetchone()
        if row is None:
            print(f"  claim {args.claim_id} not found")
            return 1
        method = row[0]
        fn = "cert.check_with_witness" if method == "witness_carry" else "cert.check"
        cur.execute(f"SELECT {fn}(%s)", (args.claim_id,))
        cur.execute(
            "SELECT status, seq FROM cert.certificate "
            "WHERE claim_id = %s ORDER BY seq DESC LIMIT 1",
            (args.claim_id,),
        )
        status, seq = cur.fetchone()

    mark = "✓" if status == "valid" else "✗" if status == "refuted" else "?"
    print(f"  [{mark}] claim {args.claim_id}  →  {status}  (cert seq={seq})")
    return 0 if status == "valid" else 1


def _cmd_attest(args: argparse.Namespace) -> int:
    if not args.write:
        print("  [dry-run] would run tools/cert_formal.py against all formal-tier claims")
        print("  pass --write to execute and record certificates")
        return 0
    mod = _load_tools_module("cert_formal", "cert_formal.py")
    mod.main()
    return 0


def _cmd_close(args: argparse.Namespace) -> int:
    if not args.write:
        print("  [dry-run] would compute reflexive closure")
        print("  curry fixed points + kan Perron-Frobenius attractor")
        print("  pass --write to execute and record eigenform claims")
        return 0
    mod = _load_tools_module("kan_in_kan", "kan_in_kan.py")
    mod.main()
    return 0


def _cmd_witness(args: argparse.Namespace) -> int:
    try:
        body = json.loads(args.body)
    except json.JSONDecodeError as exc:
        print(f"  error: --body is not valid JSON: {exc}")
        return 2

    if not args.write:
        print(f"  [dry-run] would attach witness to claim {args.claim_id}")
        print(f"    kind : {args.kind}")
        print(f"    body : {json.dumps(body, indent=4)}")
        print("  pass --write to record")
        return 0

    with db.connect(args.dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT cert.attach_witness(%s, %s, %s::jsonb)",
            (args.claim_id, args.kind, json.dumps(body)),
        )
        wit_id = cur.fetchone()[0]
    print(f"  witness {wit_id} attached to claim {args.claim_id}")
    return 0


# ---------------------------------------------------------------------------
# calx data commands (unchanged)
# ---------------------------------------------------------------------------

def _cmd_init(args: argparse.Namespace) -> int:
    with db.connect(args.dsn) as conn:
        db.apply_schema(conn)
    print("schema applied")
    return 0


def _cmd_generate(args: argparse.Namespace) -> int:
    with db.connect(args.dsn) as conn:
        db.apply_schema(conn)
        if args.backend == "pure":
            generate.generate_pure(conn, args.limit)
        else:
            generate.generate_with_primesieve(conn, args.limit)
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    with db.connect(args.dsn) as conn:
        results = [
            validate.check_omega(conn, args.limit),
            validate.check_big_omega(conn, args.limit),
        ]
    bad = [r for r in results if not r.ok]
    for r in results:
        status = "OK  " if r.ok else "FAIL"
        print(f"  [{status}] {r.sequence}  checked={r.checked}  mismatches={len(r.mismatches)}")
    return 0 if not bad else 1


def _cmd_reset(args: argparse.Namespace) -> int:
    with db.connect(args.dsn) as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS factorizations, primes, integers CASCADE")
    print("dropped factorizations, primes, integers")
    return 0


def _cmd_oeis_load(args: argparse.Namespace) -> int:
    mod = _load_tools_module("oeis_loader", "oeis_loader.py")
    with db.connect(args.dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(n) FROM integers")
            lim = cur.fetchone()[0]
        if not lim:
            print("error: no integers populated; run `trunkit generate` first")
            return 1
        print(f"DB range: 1..{lim:,}")
        updated = mod.backfill_local_families(conn)
        print(f"backfilled family on {updated} existing sequence rows")
        if args.backfill_only:
            return 0
        mod.fetch_whitelist(conn, lim, only_family=args.family, only_seq=args.seq_id)
    return 0


def _cmd_oeis_match(args: argparse.Namespace) -> int:
    if not args.orbit_id and not args.all:
        print("error: specify --orbit-id ID or --all")
        return 2
    mod = _load_tools_module("oeis_match", "oeis_match.py")
    with db.connect(args.dsn) as conn:
        db.apply_schema(conn)
        sync = not args.no_sync_membership
        if args.orbit_id:
            hits = mod.search_orbit(conn, args.orbit_id, args.prefix, sync_membership=sync)
            for m in hits:
                print(
                    f"  #{m.candidate_id} {m.oeis_id} [{m.match_kind}] "
                    f"conf={m.confidence:.3f} prefix={m.prefix_len} — {m.oeis_name[:72]}"
                )
            if not hits:
                print(f"  orbit {args.orbit_id}: no OEIS candidates stored")
        else:
            n = mod.search_all_orbits(conn, args.min_length, args.prefix, sync_membership=sync)
            print(f"searched {n} orbits (min_length={args.min_length}, prefix={args.prefix})")
    return 0


def _cmd_compose_match(args: argparse.Namespace) -> int:
    mod = _load_tools_module("compose_match", "compose_match.py")
    with db.connect(args.dsn) as conn:
        stats = mod.run_compose_pass(
            conn,
            static=not args.orbit_only,
            orbits=not args.static_only,
            search_oeis=not args.no_oeis,
            prefix_len=args.prefix,
        )
    print(
        f"run {stats['run_id']}: catalog={stats['catalog_size']} "
        f"composed={stats['composed']} (static={stats['static_specs']} "
        f"orbit={stats['orbit_specs']}) searched={stats['searched']} "
        f"identifications={stats['identifications']}"
    )
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trunkit",
        description="Proof-carrying code middleware on PostgreSQL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Consumer commands are read-only and safe for LLM use.\n"
            "Prover commands require --write to record; they dry-run without it."
        ),
    )
    p.add_argument("--dsn", help="PostgreSQL DSN (overrides $CALX_DSN / $TRUNK_DSN)")

    sub = p.add_subparsers(dest="command", required=True)

    # --- consumer ---
    v = sub.add_parser("verify", help="side-effect-free re-verification of a claim")
    v.add_argument("claim_id", type=int)
    v.set_defaults(func=_cmd_verify)

    st = sub.add_parser("standing", help="latest attestation status for all claims")
    st.add_argument("--method", help="filter by method (comp_sql, struct_kan, …)")
    st.add_argument("--status", help="filter by status (valid, refuted, unverified)")
    st.set_defaults(func=_cmd_standing)

    ex = sub.add_parser("export", help="export portable proof bundle to stdout (JSONB)")
    ex.add_argument("claim_ids", type=int, nargs="+", metavar="claim_id")
    ex.set_defaults(func=_cmd_export)

    # --- prover ---
    ck = sub.add_parser("check", help="attest a claim and record a certificate")
    ck.add_argument("claim_id", type=int)
    ck.add_argument("--write", action="store_true", help="record the certificate (dry-run without)")
    ck.set_defaults(func=_cmd_check)

    at = sub.add_parser("attest", help="run formal-tier artifact attestation")
    at.add_argument("--write", action="store_true", help="record certificates (dry-run without)")
    at.set_defaults(func=_cmd_attest)

    cl = sub.add_parser("close", help="reflexive closure — curry fixed points + kan eigenform")
    cl.add_argument("--write", action="store_true",
                    help="record eigenform claims (dry-run without)")
    cl.set_defaults(func=_cmd_close)

    wi = sub.add_parser("witness", help="attach a structured proof witness to a claim")
    wi.add_argument("claim_id", type=int)
    wi.add_argument("--kind", required=True,
                    choices=["term", "trace", "counterexample", "hash_chain", "kan_diagram"])
    wi.add_argument("--body", required=True, help="witness body as JSON string")
    wi.add_argument("--write", action="store_true", help="record the witness (dry-run without)")
    wi.set_defaults(func=_cmd_witness)

    # --- calx data ---
    sub.add_parser("init", help="apply schema/views/procedures").set_defaults(func=_cmd_init)

    g = sub.add_parser("generate", help="populate integer tables up to --limit")
    g.add_argument("--limit", type=int, required=True)
    g.add_argument("--backend", choices=("primesieve", "pure"), default="primesieve")
    g.set_defaults(func=_cmd_generate)

    vl = sub.add_parser("validate", help="compare derived columns against OEIS")
    vl.add_argument("--limit", type=int, default=100_000)
    vl.set_defaults(func=_cmd_validate)

    sub.add_parser("reset", help="drop all calx tables").set_defaults(func=_cmd_reset)

    ol = sub.add_parser("oeis-load", help="fetch curated OEIS b-files")
    ol.add_argument("--family")
    ol.add_argument("--seq-id")
    ol.add_argument("--backfill-only", action="store_true")
    ol.set_defaults(func=_cmd_oeis_load)

    om = sub.add_parser("oeis-match", help="match orbit prefixes against OEIS")
    om.add_argument("--orbit-id", type=int)
    om.add_argument("--all", action="store_true")
    om.add_argument("--min-length", type=int, default=4)
    om.add_argument("--prefix", type=int, default=8)
    om.add_argument("--no-sync-membership", action="store_true")
    om.set_defaults(func=_cmd_oeis_match)

    cm = sub.add_parser("compose-match", help="Tier 3 compose_index + OEIS search")
    cm.add_argument("--static-only", action="store_true")
    cm.add_argument("--orbit-only", action="store_true")
    cm.add_argument("--no-oeis", action="store_true")
    cm.add_argument("--prefix", type=int, default=8)
    cm.set_defaults(func=_cmd_compose_match)

    return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_tools_module(module_name: str, filename: str):
    import importlib.util

    from calx import get_shared_data_dir

    path = get_shared_data_dir("tools") / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
