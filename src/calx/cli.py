"""trunkit command-line entry point.

Consumer commands (read-only, safe for LLM use):
    trunkit verify <claim_id>
    trunkit verify --bundle FILE [--offline]
    trunkit standing [--method M] [--status S]
    trunkit export <id> [<id> ...]

Prover commands (require --write to record; dry-run otherwise):
    trunkit check <claim_id> [--write]
    trunkit attest [--write]
    trunkit close [--write]
    trunkit witness <claim_id> --kind KIND --body JSON [--write]

calx data commands:
    trunkit quickstart [--dir DIR] [--compose-only]
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
import contextlib
import json
import sys

import psycopg

from . import db, generate, validate


def _utf8_stdio() -> None:
    """Emit UTF-8 regardless of console code page.

    Windows cp1252 consoles otherwise raise UnicodeEncodeError on the
    ✓ / ✗ / → marks used in command output and --help text.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(ValueError, OSError):
                stream.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Consumer commands — read-only, no --write gate
# ---------------------------------------------------------------------------

def _cmd_verify_bundle(args: argparse.Namespace) -> int:
    from . import bundle as bundle_mod

    try:
        bundle = bundle_mod.load_bundle(args.bundle)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    import pathlib

    base_dir = pathlib.Path(args.bundle).resolve().parent
    if args.offline:
        results = bundle_mod.verify_bundle(bundle, None, base_dir)
    else:
        try:
            with db.connect(args.dsn) as live:
                results = bundle_mod.verify_bundle(bundle, live, base_dir)
        except psycopg.OperationalError as exc:
            print(f"  note: no database reachable ({exc}); structural checks only")
            results = bundle_mod.verify_bundle(bundle, None, base_dir)

    exported = bundle.get("exported_at", "?")
    print(f"  bundle: {args.bundle}  ({len(results)} claim(s), exported {exported})")
    for r in results:
        print(f"  [{r.mark}] claim {r.claim_id}  {r.method:<18}  {r.status}")
        print(f"      {r.statement[:70]}")
        for note in r.notes:
            print(f"      - {note}")
    n_valid = sum(1 for r in results if r.ok is True)
    print(f"  {n_valid}/{len(results)} valid")
    return 0 if n_valid == len(results) else 1


def _cmd_verify(args: argparse.Namespace) -> int:
    if args.bundle:
        return _cmd_verify_bundle(args)
    if args.claim_id is None:
        print("error: provide a claim_id or --bundle FILE", file=sys.stderr)
        return 2
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
        fn = ("cert.check_with_witness" if method == "witness_carry"
              else "cert.check_kernel"   if method == "cert_kernel"
              else "cert.check")
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


def _cmd_register_lean(args: argparse.Namespace) -> int:
    from pathlib import Path

    from . import leanbridge

    root = Path(args.root)
    if not root.is_dir():
        print(f"  error: project root not found: {root}")
        return 2
    rels = leanbridge.discover_closure(root)
    if not rels:
        print(f"  error: no closure files (lakefile / lean-toolchain / *.lean) under {root}")
        return 2
    file_digests = leanbridge.compute_file_digests(root, rels)
    digest = leanbridge.closure_digest(file_digests)
    toolchain = leanbridge.read_toolchain(root)
    checker = args.checker or leanbridge.default_checker_cmd(args.root, args.decl)

    if not args.write:
        print(f"  [dry-run] would register lean artifact for claim {args.claim_id}")
        print(f"    project_root : {args.root}")
        print(f"    target_decl  : {args.decl}")
        print(f"    files        : {len(rels)}  closure_digest={digest[:12]}…")
        print(f"    toolchain    : {toolchain}")
        print(f"    checker_cmd  : {checker}")
        print("  pass --write to register")
        return 0

    with db.connect(args.dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT (cert.register_lean_artifact(%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)).id",
            (args.claim_id, args.root, args.decl, json.dumps(file_digests),
             json.dumps(toolchain), digest, checker),
        )
        art_id = cur.fetchone()[0]
    print(f"  registered lean artifact {art_id} for claim {args.claim_id} "
          f"({len(rels)} files, decl {args.decl})")
    return 0


def _cmd_close(args: argparse.Namespace) -> int:
    if not args.write:
        print("  [dry-run] would compute reflexive closure")
        print("  curry fixed points + kan Perron-Frobenius attractor")
        print("  pass --write to execute and record eigenform claims")
        return 0
    if _resolve_tool("kan_in_kan.py") is None:
        print("  reflexive closure (kan-in-kan self-analysis) is a local extension,")
        print("  not part of the base package. It lives at local/tools/kan_in_kan.py")
        print("  in a repo checkout / local overlay; install that to enable `close`.")
        return 2
    mod = _load_tools_module("kan_in_kan", "kan_in_kan.py")
    rc = mod.main()
    return rc if isinstance(rc, int) else 0


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

def _cmd_quickstart(args: argparse.Namespace) -> int:
    from . import quickstart

    return quickstart.run(args)


def _cmd_init(args: argparse.Namespace) -> int:
    with db.connect(args.dsn) as conn:
        db.apply_unified(conn)
    print("schema applied")
    if args.local:
        import pathlib
        ext_dir = pathlib.Path(args.local)
        if not ext_dir.is_dir():
            print(f"  warning: --local path {ext_dir} does not exist, skipping")
            return 0
        with db.connect(args.dsn) as conn:
            applied = db.apply_extensions(conn, ext_dir)
        if applied:
            print(f"  extensions applied ({len(applied)}):")
            for f in applied:
                print(f"    {f}")
        else:
            print("  (no numbered SQL files found in extension directory)")
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


def _cmd_oeis_cosine(args: argparse.Namespace) -> int:
    with db.connect(args.dsn) as conn, conn.cursor() as cur:
        if args.rebuild:
            cur.execute("SELECT DISTINCT seq_id FROM sequence_membership")
            ids = [r[0] for r in cur.fetchall()]
            for sid in ids:
                cur.execute("SELECT calx.build_seq_vector(%s, %s)", (sid, args.k))
            print(f"rebuilt {len(ids)} sequence vectors (k={args.k})")
        # ensure the query has a descriptor (build from membership if absent)
        cur.execute("SELECT 1 FROM calx.seq_vector WHERE seq_id = %s", (args.seq_id,))
        if not cur.fetchone():
            try:
                cur.execute("SELECT calx.build_seq_vector(%s, %s)", (args.seq_id, args.k))
            except psycopg.Error as exc:
                print(f"  error: {str(exc).strip()}")
                return 1
        cur.execute(
            "SELECT seq_id, cosine, exact_prefix "
            "FROM calx.oeis_cosine_candidates(%s, %s)", (args.seq_id, args.top))
        rows = cur.fetchall()
    if not rows:
        print("  no candidates (is more than one sequence vectorised? try --rebuild)")
        return 0
    print(f"  cosine candidates for {args.seq_id} (cosine = growth-shape; "
          f"exact = leading-term agreement):")
    for sid, cos, ex in rows:
        flag = "EXACT✓" if ex is not None and ex >= args.k else f"exact_prefix={ex}"
        print(f"    {cos:+.4f}  {sid:<20} {flag}")
    print("  note: cosine is a scale-invariant pre-filter; confirm identity with exact_prefix.")
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
    v = sub.add_parser(
        "verify",
        help="side-effect-free re-verification of a claim or an exported bundle",
    )
    v.add_argument("claim_id", type=int, nargs="?",
                   help="claim id in the connected DB (omit when using --bundle)")
    v.add_argument("--bundle", metavar="FILE",
                   help="verify an exported proof bundle file instead of a DB claim")
    v.add_argument("--offline", action="store_true",
                   help="with --bundle: skip probe replay, structural checks only")
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

    rl = sub.add_parser("register-lean",
                        help="register a Lean proof project as a formal-tier artifact")
    rl.add_argument("claim_id", type=int)
    rl.add_argument("--root", required=True, help="repo-relative Lake project dir")
    rl.add_argument("--decl", required=True, help="fully-qualified target declaration")
    rl.add_argument("--checker", help="override checker command (e.g. a sandbox wrapper)")
    rl.add_argument("--write", action="store_true", help="register (dry-run without)")
    rl.set_defaults(func=_cmd_register_lean)

    cl = sub.add_parser("close", help="reflexive closure — curry fixed points + kan eigenform")
    cl.add_argument("--write", action="store_true",
                    help="record eigenform claims (dry-run without)")
    cl.set_defaults(func=_cmd_close)

    wi = sub.add_parser("witness", help="attach a structured proof witness to a claim")
    wi.add_argument("claim_id", type=int)
    wi.add_argument("--kind", required=True,
                    choices=["term", "trace", "counterexample", "hash_chain",
                             "kan_diagram", "quote_span"])
    wi.add_argument("--body", required=True, help="witness body as JSON string")
    wi.add_argument("--write", action="store_true", help="record the witness (dry-run without)")
    wi.set_defaults(func=_cmd_witness)

    # --- calx data ---
    qs = sub.add_parser(
        "quickstart",
        help="write docker-compose, start both databases, apply both schemas",
    )
    qs.add_argument(
        "--dir", default=".", metavar="DIR",
        help="where to write docker-compose.yml (default: current directory)",
    )
    qs.add_argument(
        "--compose-only", action="store_true",
        help="only write the compose file; do not start containers or apply schemas",
    )
    qs.set_defaults(func=_cmd_quickstart)

    ini = sub.add_parser("init", help="apply core schema; optionally load local extensions")
    ini.add_argument(
        "--local", metavar="DIR", default=None,
        help="also apply numbered SQL files from DIR after the core schema "
             "(e.g. trunkit init --local local/sql)",
    )
    ini.set_defaults(func=_cmd_init)

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

    oc = sub.add_parser("oeis-cosine",
                        help="cosine candidate generator: growth-shape neighbours, "
                             "confirmed by exact prefix")
    oc.add_argument("--seq-id", required=True, help="query sequence id (e.g. A000045)")
    oc.add_argument("--k", type=int, default=16, help="prefix length to vectorise")
    oc.add_argument("--top", type=int, default=5, help="number of candidates")
    oc.add_argument("--rebuild", action="store_true",
                    help="(re)build descriptors for all sequences with membership first")
    oc.set_defaults(func=_cmd_oeis_cosine)

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

def _resolve_tool(filename: str):
    """Locate a tool script: shipped ``tools/`` first, then the ``local/`` overlay.

    Returns a ``Path`` if found, else ``None``. Project-specific tools (e.g. the
    kan-in-kan self-analysis that backs ``trunkit close``) live under
    ``local/tools/`` in a repo checkout and are not part of the base package.
    """
    import pathlib

    from calx import get_shared_data_dir

    candidates = [
        get_shared_data_dir("tools") / filename,
        pathlib.Path(__file__).resolve().parents[2] / "local" / "tools" / filename,
    ]
    return next((p for p in candidates if p.is_file()), None)


def _load_tools_module(module_name: str, filename: str):
    import importlib.util

    path = _resolve_tool(filename)
    if path is None:
        raise FileNotFoundError(f"tool not found: {filename}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    _utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except psycopg.OperationalError as exc:
        print(f"error: database connection failed: {exc}", file=sys.stderr)
        print(
            "  point --dsn or $CALX_DSN at a running PostgreSQL instance, "
            "then run `trunkit init`",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
