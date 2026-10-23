"""Portable combinatorial workflows; database writes require explicit --write."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from . import combinatorial as comb
from .automata_count import export_count_witness, export_extremal_witness, product_dfa
from .bundle import verify_bundle
from .kernel import verify_witness
from .proposition import witness_subject_ref


def portable_bundle(witness: dict) -> dict:
    """Bundle a typed, independently checkable proposition without ledger claims."""
    return {
        "trunk_bundle_version": 1,
        "claims": [
            {
                "claim": {
                    "id": 1,
                    "subject_kind": "portable_certificate",
                    "subject_ref": witness_subject_ref(witness),
                    "statement": "Portable typed proposition: "
                    + json.dumps(witness_subject_ref(witness)["predicate"], sort_keys=True),
                    "method": "witness_carry",
                    "domain": "exact_int",
                    "probe_sql": None,
                },
                "certificate": {"seq": 1, "status": "unverified"},
                "witness": {"kind": "term", "body": witness},
            }
        ],
    }


def _read(path: str) -> dict:
    source = Path(path)
    if source.stat().st_size > comb.MAX_JSON_BYTES:
        raise ValueError("input exceeds portable certificate size budget")
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    return data


def _emit(data: Any, output: str | None = None) -> None:
    rendered = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if output:
        Path(output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


def bounded_colouring(instance: dict, k: int, max_steps: int = 100000) -> dict:
    """A bounded optional producer; absence never becomes a negative certificate."""
    instance = comb.normalize_instance(instance)
    if instance["kind"] != "graph" or type(k) is not int or not 0 <= k <= 128:
        raise ValueError("search needs a graph and 0..128 colours")
    n = instance["ground_n"]
    if verify_witness(comb.make_witness(instance, "wellformed"))[0] is not True:
        raise ValueError("search requires a wellformed structure")
    if n > 128 or type(max_steps) is not int or not 1 <= max_steps <= 1000000:
        raise ValueError("search budget supports at most 128 vertices / 1000000 steps")
    adjacency = [set() for _ in range(n)]
    for block in instance["blocks"]:
        if len(block["elements"]) != 2:
            raise ValueError("search requires a wellformed simple graph")
        a, b = block["elements"]
        adjacency[a].add(b)
        adjacency[b].add(a)
    order = sorted(range(n), key=lambda v: (-len(adjacency[v]), v))
    values, next_colour = [-1] * n, [0] * n
    depth = steps = 0
    started = time.monotonic()
    while 0 <= depth < n and steps < max_steps:
        vertex = order[depth]
        chosen = False
        while next_colour[depth] < k and steps < max_steps:
            colour = next_colour[depth]
            next_colour[depth] += 1
            steps += 1
            if all(values[v] != colour for v in adjacency[vertex]):
                values[vertex] = colour
                depth += 1
                if depth < n:
                    next_colour[depth] = 0
                chosen = True
                break
        if not chosen and steps < max_steps:
            values[vertex] = -1
            next_colour[depth] = 0
            depth -= 1
    witness = (
        comb.make_witness(instance, {"name": "colouring", "k": k}, {"colouring": values})
        if depth == n
        else None
    )
    ok, evidence = verify_witness(witness)
    return {
        "verdict": "valid" if ok is True else "unverified",
        "witness": witness,
        "evidence": evidence,
        "steps": steps,
        "max_steps": max_steps,
        "wall_clock_s": time.monotonic() - started,
        "search_exhausted": depth < 0,
        "note": "No negative proof is inferred from an unsuccessful search.",
    }


def run(args: argparse.Namespace) -> int:
    try:
        if args.comb_command == "import":
            instance = comb.import_instance(_read(args.file))
            if args.write:
                from . import db

                if not args.subject:
                    raise ValueError("--write import requires --subject")
                if instance["kind"] not in ("graph", "digraph", "hypergraph", "set_system"):
                    raise ValueError("database import supports incidence structures")
                if verify_witness(comb.make_witness(instance, "wellformed"))[0] is not True:
                    raise ValueError("database import requires a wellformed structure")
                with db.connect(args.dsn) as conn:
                    existing = conn.execute(
                        "SELECT id FROM comb.structure WHERE subject_id=%s", (args.subject,)
                    ).fetchone()
                    if existing:
                        snapshot = conn.execute(
                            "SELECT comb.serialize_structure(%s)", (args.subject,)
                        ).fetchone()[0]
                        if comb.normalize_instance(snapshot) != instance:
                            raise ValueError("subject already names a different instance")
                        _emit(
                            {
                                "instance": instance,
                                "instance_digest": comb.instance_digest(instance),
                            },
                            args.output,
                        )
                        return 0
                    sid = conn.execute(
                        "SELECT (comb.register_structure(%s,%s,%s)).id",
                        (args.subject, instance["kind"], instance["ground_n"]),
                    ).fetchone()[0]
                    for block in instance["blocks"]:
                        conn.execute(
                            "INSERT INTO comb.block(structure_id,idx) VALUES(%s,%s)",
                            (sid, block["idx"]),
                        )
                        positions = block.get("positions")
                        for i, vertex in enumerate(block["elements"]):
                            conn.execute(
                                "INSERT INTO comb.incidence VALUES(%s,%s,%s,%s)",
                                (sid, block["idx"], vertex, positions[i] if positions else None),
                            )
            _emit(
                {"instance": instance, "instance_digest": comb.instance_digest(instance)},
                args.output,
            )
        elif args.comb_command == "check":
            value = _read(args.file)
            if "trunk_bundle_version" in value:
                results = verify_bundle(value)
                _emit(
                    {
                        "all_valid": all(r.ok is True for r in results),
                        "claims": [
                            {
                                "id": r.claim_id,
                                "verdict": r.status.lower(),
                                "evidence": r.kernel_evidence,
                                "notes": r.notes,
                            }
                            for r in results
                        ],
                    },
                    args.output,
                )
                return 0 if all(r.ok is True for r in results) else 1
            ok, evidence = verify_witness(value)
            _emit(
                {
                    "verdict": "valid"
                    if ok is True
                    else "refuted"
                    if ok is False
                    else "unverified",
                    "evidence": evidence,
                },
                args.output,
            )
            return 0 if ok is True else 1
        elif args.comb_command == "export":
            value = _read(args.file)
            if "schema" not in value:
                if not args.predicate:
                    raise ValueError("export of an instance requires --predicate JSON")
                value = comb.make_witness(
                    value.get("instance", value),
                    json.loads(args.predicate),
                    json.loads(args.certificate),
                )
            _emit(portable_bundle(value) if args.bundle else value, args.output)
        elif args.comb_command == "exclude":
            from .sat_adapter import make_colouring_exclusion

            proof = Path(args.proof)
            if proof.stat().st_size > 8_000_000:
                raise ValueError("DRUP proof exceeds import size budget")
            value = _read(args.file)
            witness = make_colouring_exclusion(
                value.get("instance", value), args.colours, proof.read_text(encoding="ascii")
            )
            _emit(portable_bundle(witness) if args.bundle else witness, args.output)
        elif args.comb_command == "demo":
            _emit(portable_bundle(comb.c5_chromatic_example()), args.output)
        elif args.comb_command == "transform":
            value = _read(args.file)
            if args.operation == "relabel":
                if not args.mapping:
                    raise ValueError("relabel requires --mapping JSON")
                result = comb.relabel_witness(value, json.loads(args.mapping))
            else:
                if not args.other:
                    raise ValueError("disjoint_union requires --other witness.json")
                result = comb.disjoint_union_witness([value, _read(args.other)])
            _emit(result, args.output)
        elif args.comb_command == "search":
            value = _read(args.file)
            instance = value.get("instance", value)
            result = bounded_colouring(instance, args.colours, args.max_steps)
            if args.write:
                from . import db

                with db.connect(args.dsn) as conn:
                    conn.execute(
                        "SELECT cert.record_attempt(%s,%s,p_agent=>%s,"
                        "p_wall_clock_s=>%s,p_notes=>%s::jsonb)",
                        (
                            "comb:" + comb.instance_digest(instance),
                            "partial" if result["witness"] else "exhausted",
                            "bounded_colouring",
                            result["wall_clock_s"],
                            json.dumps({"steps": result["steps"], "colours": args.colours}),
                        ),
                    )
            _emit(result, args.output)
            return 0 if result["verdict"] == "valid" else 1
        return 0
    except (ValueError, TypeError, OSError, KeyError) as exc:
        print(json.dumps({"verdict": "unverified", "error": str(exc)}))
        return 2


def run_dfa(args: argparse.Namespace) -> int:
    try:
        if args.dfa_command == "count":
            witness = export_count_witness(
                _read(args.file),
                args.length,
                annihilator=json.loads(args.annihilator) if args.annihilator else None,
            )
            _emit(portable_bundle(witness) if args.bundle else witness, args.output)
        elif args.dfa_command == "extremal":
            witness = export_extremal_witness(_read(args.file), args.horizon, args.sense)
            _emit(portable_bundle(witness) if args.bundle else witness, args.output)
        else:
            _emit(
                product_dfa(
                    _read(args.left),
                    _read(args.right),
                    args.operation,
                    state_budget=args.state_budget,
                    transition_budget=args.transition_budget,
                ),
                args.output,
            )
        return 0
    except (ValueError, TypeError, OSError, KeyError) as exc:
        print(json.dumps({"verdict": "unverified", "error": str(exc)}))
        return 2


def add_parsers(sub) -> None:
    group = sub.add_parser("comb", help="portable finite combinatorial certificates")
    actions = group.add_subparsers(dest="comb_command", required=True)
    for name in ("import", "check", "export", "demo", "transform", "search", "exclude"):
        parser = actions.add_parser(name)
        if name != "demo":
            parser.add_argument("file")
        parser.add_argument("--output")
        parser.set_defaults(func=run)
        if name in ("import", "search"):
            parser.add_argument("--write", action="store_true")
        if name == "import":
            parser.add_argument("--subject")
        if name == "export":
            parser.add_argument("--predicate", help="typed predicate JSON")
            parser.add_argument("--certificate", default="{}", help="certificate JSON")
            parser.add_argument("--bundle", action="store_true")
        if name == "transform":
            parser.add_argument("--operation", choices=("relabel", "disjoint_union"), required=True)
            parser.add_argument("--mapping")
            parser.add_argument("--other")
        if name == "search":
            parser.add_argument("--colours", type=int, required=True)
            parser.add_argument("--max-steps", type=int, default=100000)
        if name == "exclude":
            parser.add_argument("--colours", type=int, required=True)
            parser.add_argument("--proof", required=True, help="addition-only ASCII DRUP proof")
            parser.add_argument("--bundle", action="store_true")
    dfa = sub.add_parser("dfa", help="exact language counting and bounded DFA products")
    actions = dfa.add_subparsers(dest="dfa_command", required=True)
    count = actions.add_parser("count")
    count.add_argument("file")
    count.add_argument("--length", type=int, required=True)
    count.add_argument("--annihilator", help="matrix polynomial coefficients, ascending order")
    count.add_argument("--bundle", action="store_true")
    count.add_argument("--output")
    count.set_defaults(func=run_dfa)
    product = actions.add_parser("product")
    product.add_argument("left")
    product.add_argument("right")
    product.add_argument("--operation", choices=("intersection", "union"), default="intersection")
    product.add_argument("--state-budget", type=int, default=256)
    product.add_argument("--transition-budget", type=int, default=16384)
    product.add_argument("--output")
    product.set_defaults(func=run_dfa)
    extremal = actions.add_parser("extremal")
    extremal.add_argument("file")
    extremal.add_argument("--horizon", type=int, required=True)
    extremal.add_argument("--sense", choices=("best", "worst"), default="best")
    extremal.add_argument("--bundle", action="store_true")
    extremal.add_argument("--output")
    extremal.set_defaults(func=run_dfa)
