#!/usr/bin/env python3
"""Authoritative TEL-constants seeder: source -> Postgres curry.constants, direct.

Replaces the old fragmented path
  phase1_extract.py (hand-transcribed) -> .curry/curry.db (SQLite) -> port_curry_sqlite_to_pg.py
which is no longer reproducible in tel-clean (the SQLite intermediate is gone).

Single source of truth: tel_constants_manifest.json + the extraction logic shared
with tel_constants_check.py. For each manifest constant it extracts the normalized
structural core from current hir_*.rs and declares it into curry.constants:
  * no active row            -> declare v1
  * active value matches src -> no-op (idempotent)
  * active value drifted     -> declare next version (append-only; v1 retained)

Dry-run by default; pass --write to record. Re-runnable.
"""
import json, os, sys, psycopg

from tel_constants_check import extract_type, extract_fn, normalize, stored_core

DSN = os.environ.get("TRUNK_DSN", "postgresql://trunk:trunk@localhost:5434/trunk")
HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "tel_constants_manifest.json")
WRITE = "--write" in sys.argv


def main():
    manifest = json.load(open(MANIFEST, encoding="utf-8"))
    repo = manifest["repo"]
    cache = {}

    def read(rel):
        if rel not in cache:
            with open(os.path.join(repo, rel), encoding="utf-8") as f:
                cache[rel] = f.read()
        return cache[rel]

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        for e in manifest["constants"]:
            cid, kind, symbol, rel = e["id"], e["kind"], e["symbol"], e["file"]
            raw = extract_type(read(rel), symbol) if kind == "type" else extract_fn(read(rel), symbol)
            if raw is None:
                print(f"  missing   {cid}  (symbol not found in {rel})")
                continue
            value = normalize(raw)

            cur.execute(
                "SELECT version, convert_from(value,'UTF8') FROM curry.constants "
                "WHERE id=%s AND retired_at IS NULL ORDER BY version DESC LIMIT 1",
                (cid,),
            )
            row = cur.fetchone()
            if row is None:
                action, ver = "declare v1", 1
            else:
                cur_ver, cur_raw = row
                try:
                    cur_val = json.loads(cur_raw)
                except (json.JSONDecodeError, TypeError):
                    cur_val = cur_raw
                if normalize(stored_core(cur_val)) == value:
                    print(f"  unchanged {cid}  (v{cur_ver})")
                    continue
                action, ver = f"declare v{cur_ver + 1}", cur_ver + 1

            if WRITE:
                cur.execute(
                    """INSERT INTO curry.constants(id,version,value,type_signature,declared_at,description)
                       VALUES(%s,%s,%s,'String',now(),%s)
                       ON CONFLICT(id,version) DO NOTHING""",
                    (cid, ver, json.dumps(value).encode("utf-8"),
                     f"source-extracted {kind} {symbol} from {rel} (tel_constants_seed)"),
                )
                conn.commit()
            print(f"  {'WROTE' if WRITE else 'WOULD'} {action:11} {cid}")

    print("tel constants seed " + ("complete" if WRITE else "(dry-run; pass --write to record)"))


if __name__ == "__main__":
    sys.exit(main())
