#!/usr/bin/env python3
"""Drift-check for seeded TEL type/signature constants (capability-tree T1).

For each entry in tel_constants_manifest.json, extract the *structural core* of
the symbol from current tel-clean source (the enum/struct body, or the fn
signature), normalize it, and compare against the structural core of the value
stored in curry.constants (latest active version, comment tails stripped).

Three-valued per constant: match / drift / missing. Recorded into
cert.tel_constants; an aggregate cert claim (subject_kind='tel_constants')
reads it: valid iff all match, refuted if any drift, unverified if any missing.

This makes "are TEL's seeded constants still current with the source?" a query,
and closes the last baked-not-verified TEL surface. Re-runnable.
"""
import json, os, re, sys, psycopg

DSN = os.environ.get("TRUNK_DSN", "postgresql://trunk:trunk@localhost:5434/trunk")
HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "tel_constants_manifest.json")


def strip_comments(s: str) -> str:
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.S)
    s = re.sub(r"//[^\n]*", " ", s)
    return s


def normalize(s: str) -> str:
    """Canonical structural form: comments gone, whitespace collapsed, spacing
    tidied, leading `pub` and `fn Type::` editorial prefixes removed."""
    s = strip_comments(s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace("{", " { ").replace("}", " } ").replace(",", ", ")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace(", }", " }")          # drop trailing comma before close brace
    s = s.replace("( ", "(").replace(" )", ")")
    s = s.replace(",)", ")")            # drop trailing comma before close paren
    s = re.sub(r"^pub\s+", "", s)        # leading visibility
    s = re.sub(r"\bfn\s+\w+::", "fn ", s)  # Type::method editorial prefix
    return s.strip()


def extract_type(src: str, symbol: str):
    m = re.search(r"\bpub\s+(enum|struct)\s+" + re.escape(symbol) + r"\b", src)
    if not m:
        return None
    start = m.start()
    i = m.end()
    while i < len(src) and src[i] not in "{;":
        i += 1
    if i >= len(src):
        return None
    if src[i] == ";":                    # newtype, e.g. struct SetId(pub usize);
        return src[start:i + 1]
    depth = 0
    j = i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
        j += 1
    return None


def extract_fn(src: str, symbol: str):
    m = re.search(r"\bpub\s+fn\s+" + re.escape(symbol) + r"\s*\(", src)
    if not m:
        return None
    start = m.start()
    i = m.end()                          # just after the opening '('
    depth = 1
    while i < len(src) and depth > 0:
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
        i += 1
    j = i                                # capture return type up to body '{' or ';'
    while j < len(src) and src[j] not in "{;":
        j += 1
    sig = src[start:j]                   # signature without body
    # Resolve `Self` to the enclosing impl type so `Self` vs `SNFExpr` isn't drift.
    impls = list(re.finditer(r"\bimpl\b(?:\s*<[^>]*>)?\s+([A-Za-z_][A-Za-z0-9_]*)", src[:start]))
    if impls:
        sig = re.sub(r"\bSelf\b", impls[-1].group(1), sig)
    return sig


def stored_core(value: str) -> str:
    """Structural core of a stored constant: everything before the first `//`."""
    return value.split("//", 1)[0]


def main():
    manifest = json.load(open(MANIFEST, encoding="utf-8"))
    repo = manifest["repo"]
    entries = manifest["constants"]

    file_cache = {}

    def read(rel):
        if rel not in file_cache:
            with open(os.path.join(repo, rel), encoding="utf-8") as f:
                file_cache[rel] = f.read()
        return file_cache[rel]

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS cert.tel_constants (
            const_id   text PRIMARY KEY,
            kind       text,
            symbol     text,
            file       text,
            status     text,        -- match | drift | missing
            detail     text,
            checked_at timestamptz)""")

        for e in entries:
            cid, kind, symbol, rel = e["id"], e["kind"], e["symbol"], e["file"]
            cur.execute(
                "SELECT convert_from(value,'UTF8') FROM curry.constants "
                "WHERE id=%s AND retired_at IS NULL ORDER BY version DESC LIMIT 1",
                (cid,),
            )
            row = cur.fetchone()
            stored = row[0] if row else None
            if stored is not None:
                # Stored constants are JSON-encoded strings (wrapped in quotes).
                try:
                    stored = json.loads(stored)
                except (json.JSONDecodeError, TypeError):
                    pass

            try:
                src = read(rel)
            except FileNotFoundError:
                status, detail = "missing", f"source file not found: {rel}"
                _write(cur, e, status, detail)
                continue

            raw = extract_type(src, symbol) if kind == "type" else extract_fn(src, symbol)
            if raw is None:
                status = "missing"
                detail = f"symbol '{symbol}' not found in {rel}"
            elif stored is None:
                status = "missing"
                detail = f"no active curry.constants row for {cid}"
            else:
                src_norm = normalize(raw)
                stored_norm = normalize(stored_core(stored))
                if src_norm == stored_norm:
                    status, detail = "match", src_norm[:200]
                else:
                    status = "drift"
                    detail = f"source: {src_norm[:140]} || stored: {stored_norm[:140]}"
            _write(cur, e, status, detail)
            print(f"  {status:8} {cid}")
        conn.commit()
    print("tel constants check complete")


def _write(cur, e, status, detail):
    cur.execute(
        """INSERT INTO cert.tel_constants(const_id,kind,symbol,file,status,detail,checked_at)
           VALUES(%s,%s,%s,%s,%s,%s,now())
           ON CONFLICT(const_id) DO UPDATE SET kind=EXCLUDED.kind,symbol=EXCLUDED.symbol,
             file=EXCLUDED.file,status=EXCLUDED.status,detail=EXCLUDED.detail,checked_at=now()""",
        (e["id"], e["kind"], e["symbol"], e["file"], status, detail),
    )


if __name__ == "__main__":
    sys.exit(main())
