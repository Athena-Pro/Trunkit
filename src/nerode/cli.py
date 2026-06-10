"""
nerode.cli — command-line interface.

Usage
-----
    nerode build   --regex PATTERN [--name NAME] [--write] [--dsn DSN]
    nerode min     ID [--write] [--dsn DSN]
    nerode equiv   ID1 ID2 [--write] [--dsn DSN]
    nerode run     ID --input STRING [--write] [--dsn DSN]
    nerode export  ID [--out FILE] [--dsn DSN]
    nerode import  FILE [--name NAME] [--dsn DSN]
    nerode close   --apply | --write [--dsn DSN]
    nerode facts   ID [--dsn DSN]
    nerode snap    [--dsn DSN]
    nerode corpus     [--list] [--certify] [--dsn DSN]
    nerode products   [--list] [--dsn DSN]
    nerode morphisms  [--list] [--dsn DSN]

Global flags
------------
    --trunkit   Shortcut for --dsn postgresql://trunk:trunk@localhost:5434/trunk
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from typing import Any

import psycopg

from .automata import export_from_db, import_to_db, load_json, print_transition_table
from .db import TRUNKIT_DSN, apply_schema, connect, resolve_dsn

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utf8_stdio() -> None:
    """Emit UTF-8 regardless of console code page.

    Windows cp1252 consoles otherwise raise UnicodeEncodeError on the
    → / ✓ / × marks used in transition tables and --help text.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(ValueError, OSError):
                stream.reconfigure(encoding="utf-8", errors="replace")


def _die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def _json_out(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------


def cmd_build(args: argparse.Namespace) -> None:
    """Build a minimal DFA from a regex pattern."""
    with connect(args.dsn) as conn:
        row = conn.execute(
            "SELECT nerode.from_regex(%s, %s)",
            (args.regex, args.name),
        ).fetchone()
        auto_id: int = row[0]

        if args.write:
            print(f"automaton id: {auto_id}")
            claim_id: int = conn.execute(
                "SELECT nerode.certify(%s,'from_regex',"
                "jsonb_build_object('pattern',%s),'construction_record',"
                "jsonb_build_object('pattern',%s,'automaton_id',%s))",
                (auto_id, args.regex, args.regex, auto_id),
            ).fetchone()[0]
            print(f"cert claim id: {claim_id}")

        data = export_from_db(conn, auto_id)
        if args.write:
            conn.commit()
        else:
            conn.rollback()
            print("dry-run: automaton not persisted (pass --write to record)")

    print_transition_table(data)


def cmd_min(args: argparse.Namespace) -> None:
    """Minimize an existing DFA."""
    with connect(args.dsn) as conn:
        row = conn.execute(
            "SELECT nerode.minimize(%s)", (args.id,)
        ).fetchone()
        if row is None:
            _die(f"automaton {args.id} not found")
        min_id: int = row[0]
        print(f"minimized automaton id: {min_id}")

        if args.write:
            row2 = conn.execute(
                "SELECT automaton_id, claim_id FROM nerode.minimize_certified(%s)",
                (args.id,),
            ).fetchone()
            print(f"cert claim id: {row2[1]}")
            conn.commit()
        else:
            conn.rollback()
            print("dry-run: minimized automaton not persisted (pass --write to record)")


def cmd_equiv(args: argparse.Namespace) -> None:
    """Test language equivalence of two DFAs."""
    with connect(args.dsn) as conn:
        if args.write:
            row = conn.execute(
                "SELECT equivalent, claim_id FROM nerode.certify_equivalence(%s, %s)",
                (args.id1, args.id2),
            ).fetchone()
            eq, claim_id = row
            print(f"equivalent: {eq}")
            print(f"cert claim id: {claim_id}")
        else:
            row = conn.execute(
                "SELECT equivalent, witness FROM nerode.equivalent(%s, %s)",
                (args.id1, args.id2),
            ).fetchone()
            eq, witness = row
            print(f"equivalent: {eq}")
            if not eq and witness:
                _json_out(witness)
        conn.commit()


def cmd_run(args: argparse.Namespace) -> None:
    """Simulate DFA on an input string."""
    with connect(args.dsn) as conn:
        if args.write:
            row = conn.execute(
                "SELECT accept, claim_id FROM nerode.certify_run(%s, %s)",
                (args.id, args.input),
            ).fetchone()
            accept, claim_id = row
            print(f"accept: {accept}")
            print(f"cert claim id: {claim_id}")
        else:
            row = conn.execute(
                "SELECT accept, evidence FROM nerode.run(%s, %s)",
                (args.id, args.input),
            ).fetchone()
            accept, evidence = row
            print(f"accept: {accept}")
            if evidence:
                _json_out(evidence)
        conn.commit()


def cmd_export(args: argparse.Namespace) -> None:
    """Export an automaton to JSON."""
    with connect(args.dsn) as conn:
        data = export_from_db(conn, args.id)

    text = json.dumps(data, indent=2)
    if args.out:
        from pathlib import Path
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"written to {args.out}")
    else:
        print(text)


def cmd_import(args: argparse.Namespace) -> None:
    """Import an automaton from a JSON file."""
    data = load_json(args.file)
    if args.name:
        data["name"] = args.name
    with connect(args.dsn) as conn:
        auto_id = import_to_db(conn, data)
        conn.commit()
    print(f"automaton id: {auto_id}")


def cmd_close(args: argparse.Namespace) -> None:
    """Apply schema migrations or run the eigenform / fixed-point scan."""
    if args.write:
        with connect(args.dsn) as conn:
            rows = conn.execute(
                "SELECT automaton_id, eigenform_id, is_minimal, "
                "original_states, minimal_states, claim_id "
                "FROM nerode.scan_eigenforms()"
            ).fetchall()
            conn.commit()
        n_total   = len(rows)
        n_minimal = sum(1 for r in rows if r[2])
        n_nonmin  = n_total - n_minimal
        n_distinct = len({r[1] for r in rows})
        print(f"nerode eigenform scan — {n_total} DFA(s)")
        if rows:
            print(f"  {'ID':>6}  {'EF_ID':>6}  {'MIN':>5}  {'|Q|':>4}  "
                  f"{'|Q_min|':>7}  {'CLAIM':>6}")
            for r in rows:
                auto_id, ef_id, is_min, orig, mins, cl_id = r
                print(f"  {auto_id:>6}  {ef_id:>6}  {str(is_min):>5}  "
                      f"{orig:>4}  {mins:>7}  {cl_id:>6}")
        print(f"  {n_minimal}/{n_total} already minimal")
        print(f"  {n_nonmin}/{n_total} non-minimal (eigenforms computed)")
        print(f"  {n_distinct} distinct eigenform(s)")
    elif args.apply:
        with connect(args.dsn) as conn:
            apply_schema(conn, verbose=True)
    else:
        print("Pass --apply (schema migration) or --write (eigenform scan).")


def cmd_facts(args: argparse.Namespace) -> None:
    """Print arithmetic facts (calx bridge) for an automaton."""
    with connect(args.dsn) as conn:
        row = conn.execute(
            "SELECT nerode.calx_state_facts(%s)", (args.id,)
        ).fetchone()
        _json_out(row[0])


def cmd_snap(args: argparse.Namespace) -> None:
    """Print the certification snapshot for all automata."""
    with connect(args.dsn) as conn:
        row = conn.execute("SELECT nerode.cert_snapshot()").fetchone()
        _json_out(row[0])


def cmd_products(args: argparse.Namespace) -> None:
    """Show pairwise intersection products of corpus DFAs (bound vs actual)."""
    with connect(args.dsn) as conn:
        rows = conn.execute(
            "SELECT lhs_slug, rhs_slug, product_id, state_bound, actual_count "
            "FROM nerode.product_pairs "
            "ORDER BY id"
        ).fetchall()
        print(f"nerode product pairs \u2014 {len(rows)} pair(s)")
        if rows:
            print(
                f"  {'pair':<24}  {'bound':>5}  {'actual':>6}  "
                f"{'actual/bound':>12}  {'tight?':>7}"
            )
            for lhs, rhs, _pid, bound, actual in rows:
                pair_str = f"{lhs} \u00d7 {rhs}"
                if bound is not None and actual is not None:
                    ratio = f"{actual / bound:.3f}"
                    tight = "Yes" if actual == bound else "No"
                    bound_str  = str(bound)
                    actual_str = str(actual)
                else:
                    ratio = bound_str = actual_str = tight = "?"
                print(
                    f"  {pair_str:<24}  {bound_str:>5}  {actual_str:>6}  "
                    f"{ratio:>12}  {tight:>7}"
                )
        conn.commit()


def cmd_morphisms(args: argparse.Namespace) -> None:
    """List DFA morphisms registered from the product corpus."""
    with connect(args.dsn) as conn:
        rows = conn.execute(
            """
            SELECT m.id,
                   src.name AS src_name,
                   tgt.name AS tgt_name,
                   m.kind,
                   (SELECT count(*)          FROM jsonb_object_keys(m.state_map)) AS domain_size,
                   (SELECT count(DISTINCT value) FROM jsonb_each_text(m.state_map)) AS image_size
            FROM nerode.morphisms m
            JOIN nerode.automata src ON src.id = m.src_id
            JOIN nerode.automata tgt ON tgt.id = m.tgt_id
            ORDER BY m.id
            """
        ).fetchall()
        print(f"nerode morphisms \u2014 {len(rows)} morphism(s)")
        if rows:
            print(
                f"  {'id':>4}  {'src':>30}  {'tgt':>12}  "
                f"{'kind':>12}  {'|dom|':>6}  {'|img|':>6}"
            )
            for mid, src_name, tgt_name, kind, dom, img in rows:
                print(
                    f"  {mid:>4}  {str(src_name):<30}  {str(tgt_name):<12}  "
                    f"{kind:<12}  {dom:>6}  {img:>6}"
                )
        conn.commit()


def cmd_corpus(args: argparse.Namespace) -> None:
    """List the named DFA corpus, or certify every entry."""
    do_certify = args.certify
    # Default to --list when neither flag is given
    do_list = args.list or not do_certify

    with connect(args.dsn) as conn:
        if do_list:
            rows = conn.execute(
                "SELECT c.slug, c.automaton_id, a.state_count, c.description "
                "FROM nerode.corpus c "
                "LEFT JOIN nerode.automata a ON a.id = c.automaton_id "
                "ORDER BY c.id"
            ).fetchall()
            print(f"nerode corpus \u2014 {len(rows)} registered DFA(s)")
            if rows:
                print(f"  {'slug':<12}  {'ID':>6}  {'|Q|':>4}  description")
                for slug, aid, sc, desc in rows:
                    sc_str = str(sc) if sc is not None else "?"
                    print(f"  {slug:<12}  {aid!s:>6}  {sc_str:>4}  {desc or ''}")

        if do_certify:
            corpus_rows = conn.execute(
                "SELECT slug, automaton_id FROM nerode.corpus "
                "WHERE automaton_id IS NOT NULL ORDER BY id"
            ).fetchall()
            print(f"nerode corpus certification \u2014 {len(corpus_rows)} DFA(s)")
            for slug, aid in corpus_rows:
                prime_row = conn.execute(
                    "SELECT is_prime, state_count, claim_id, calx_facts "
                    "FROM nerode.certify_prime_dfa(%s)",
                    (aid,),
                ).fetchone()
                ef_row = conn.execute(
                    "SELECT is_minimal, eigenform_id, original_states, "
                    "minimal_states, claim_id "
                    "FROM nerode.certify_eigenform(%s)",
                    (aid,),
                ).fetchone()
                is_prime, sc, prime_claim, calx = prime_row
                is_min, ef_id, orig, mins, ef_claim = ef_row
                factors = calx.get("factorization", []) if calx else []
                print(
                    f"  {slug}: |Q|={sc}, is_prime={is_prime}, "
                    f"factors={factors}, eigenform_id={ef_id}, is_minimal={is_min}"
                )
        conn.commit()


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nerode",
        description="Deterministic automata construction middleware.",
    )
    p.add_argument("--dsn", metavar="DSN", help="PostgreSQL connection string.")
    p.add_argument(
        "--trunkit", action="store_true",
        help="Connect to the Trunkit DB (postgresql://trunk:trunk@localhost:5434/trunk)."
    )
    sub = p.add_subparsers(dest="command", required=True)

    # build
    pb = sub.add_parser("build", help="Regex → minimal DFA.")
    pb.add_argument("--regex", required=True, metavar="PATTERN")
    pb.add_argument("--name", metavar="NAME")
    pb.add_argument("--write", action="store_true",
                    help="Persist the automaton and issue a cert.claim (dry-run without).")

    # min
    pm = sub.add_parser("min", help="Minimize a DFA (Hopcroft).")
    pm.add_argument("id", type=int, metavar="ID")
    pm.add_argument("--write", action="store_true",
                    help="Persist the result and issue a cert.claim (dry-run without).")

    # equiv
    pe = sub.add_parser("equiv", help="Test language equivalence.")
    pe.add_argument("id1", type=int, metavar="ID1")
    pe.add_argument("id2", type=int, metavar="ID2")
    pe.add_argument("--write", action="store_true")

    # run
    pr = sub.add_parser("run", help="Simulate DFA on input.")
    pr.add_argument("id", type=int, metavar="ID")
    pr.add_argument("--input", required=True, metavar="STRING")
    pr.add_argument("--write", action="store_true")

    # export
    px = sub.add_parser("export", help="Export automaton to JSON.")
    px.add_argument("id", type=int, metavar="ID")
    px.add_argument("--out", metavar="FILE")

    # import
    pi = sub.add_parser("import", help="Import automaton from JSON.")
    pi.add_argument("file", metavar="FILE")
    pi.add_argument("--name", metavar="NAME")

    # close (schema migration or eigenform scan)
    pc = sub.add_parser("close", help="Schema migrations or eigenform scan.")
    pc.add_argument("--apply", action="store_true", help="Apply schema migrations (idempotent).")
    pc.add_argument("--write", action="store_true",
                    help="Scan and certify eigenforms (fixed-point scan).")

    # facts
    pf = sub.add_parser("facts", help="Arithmetic facts for an automaton.")
    pf.add_argument("id", type=int, metavar="ID")

    # snap
    sub.add_parser("snap", help="Certification snapshot.")

    # corpus
    pc2 = sub.add_parser("corpus", help="Named DFA corpus: list or certify.")
    pc2.add_argument("--list",    action="store_true", help="List corpus entries (default).")
    pc2.add_argument("--certify", action="store_true",
                     help="Certify each entry: certify_prime_dfa + certify_eigenform.")

    # products
    pp2 = sub.add_parser("products", help="Corpus intersection products: bound vs actual.")
    pp2.add_argument("--list", action="store_true",
                     help="List all product pairs (default when no flag given).")

    # morphisms
    pm2 = sub.add_parser("morphisms", help="DFA morphisms from product corpus to factor DFAs.")
    pm2.add_argument("--list", action="store_true", help="List all registered morphisms.")

    return p


_DISPATCH = {
    "build":  cmd_build,
    "min":    cmd_min,
    "equiv":  cmd_equiv,
    "run":    cmd_run,
    "export": cmd_export,
    "import": cmd_import,
    "close":  cmd_close,
    "facts":  cmd_facts,
    "snap":   cmd_snap,
    "corpus":    cmd_corpus,
    "products":  cmd_products,
    "morphisms": cmd_morphisms,
}


def main(argv: list[str] | None = None) -> None:
    _utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    # --trunkit overrides --dsn; otherwise fall back to env / default
    if getattr(args, "trunkit", False):
        args.dsn = TRUNKIT_DSN
    elif not args.dsn:
        args.dsn = resolve_dsn()

    try:
        _DISPATCH[args.command](args)
    except psycopg.OperationalError as exc:
        _die(f"database connection failed: {exc}")
    except psycopg.Error as exc:
        _die(f"database error: {exc}")
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
