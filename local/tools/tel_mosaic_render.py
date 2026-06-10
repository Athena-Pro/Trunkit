#!/usr/bin/env python3
"""Render a factorization mosaic *in TEL*: query calx.factorizations, emit a
.tel program that paints a prime × integer grid, then run telc to produce a PNG.

Same honest bridge as tel_calx_render.py (calx → .tel → telc → PNG).

Layout:
  X axis — integers n from 2 to N (left to right)
  Y axis — primes p in ascending order (top to bottom)
  Cell (n, p) is lit when p | n; brightness encodes the exponent:
    e=1 → medium blue    (80,  140, 210)
    e=2 → bright teal    (30,  200, 180)
    e=3 → bright green   (100, 220,  80)
    e≥4 → bright yellow  (250, 220,  50)

Background is very dark; un-factored cells are invisible, so the coloured
cells reveal the multiplicative structure at a glance.  Primorial columns
(30, 60, 90, …) light up with many primes; powers of 2 form a top-row stripe;
prime columns (col n=p) show a single lit row.
"""
import os
import subprocess
import psycopg

DSN = os.environ.get("TRUNK_DSN", "postgresql://trunk:trunk@localhost:5434/trunk")
REPO = "C:/AI-Local/tel-clean"

N_MAX  = 100    # integers 2..N_MAX on x axis
CELL   = 6      # pixels per cell (5 filled + 1 gap)
PAD    = 10     # canvas margin (also used as column/row label space)

TEL_PATH = os.path.join(REPO, "bootstrap", "output", "calx_mosaic.tel")
PNG_REL  = "bootstrap/output/calx_mosaic.png"


def fetch_primes() -> list[int]:
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT p FROM calx.primes WHERE p <= %s ORDER BY p", (N_MAX,))
        return [r[0] for r in cur.fetchall()]


def fetch_factorizations() -> dict[tuple[int, int], int]:
    """Return {(n, prime): exponent} for n in [2..N_MAX]."""
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT n, prime, exponent FROM calx.factorizations "
            "WHERE n BETWEEN 2 AND %s ORDER BY n, prime",
            (N_MAX,),
        )
        return {(int(n), int(p)): int(e) for n, p, e in cur.fetchall()}


def exp_color(e: int) -> str:
    if e == 1:
        return "c_e1"
    if e == 2:
        return "c_e2"
    if e == 3:
        return "c_e3"
    return "c_e4"


def main() -> None:
    primes       = fetch_primes()
    facts        = fetch_factorizations()
    n_primes     = len(primes)
    prime_index  = {p: i for i, p in enumerate(primes)}

    n_integers = N_MAX - 2 + 1   # columns: n = 2..N_MAX

    canvas_w = PAD + n_integers * CELL + PAD
    canvas_h = PAD + n_primes   * CELL + PAD

    L = [
        "fn main() -> i32 {",
        f"  let canvas = Canvas::new({canvas_w}, {canvas_h});",
        "  let bg   = Color::new( 10,  10,  20);",
        "  canvas.clear(bg);",
        "  let c_e1 = Color::new( 80, 140, 210);",   # e=1  blue
        "  let c_e2 = Color::new( 30, 200, 180);",   # e=2  teal
        "  let c_e3 = Color::new(100, 220,  80);",   # e=3  green
        "  let c_e4 = Color::new(250, 220,  50);",   # e≥4  yellow
    ]

    fill    = CELL - 1    # filled pixels per side (leaving 1-px gap)
    n_cells = 0

    for (n, p), e in sorted(facts.items()):
        if p not in prime_index:
            continue
        col = n - 2                     # 0-indexed column
        row = prime_index[p]            # 0-indexed row
        px  = PAD + col * CELL
        py  = PAD + row * CELL
        col_name = exp_color(e)
        for dy in range(fill):
            L.append(
                f"  canvas.draw_line({px}, {py + dy}, {px + fill - 1}, {py + dy}, {col_name});"
            )
        n_cells += 1

    L.append(f'  canvas.save_png("{PNG_REL}");')
    L.append("  return 0;")
    L.append("}")

    os.makedirs(os.path.dirname(TEL_PATH), exist_ok=True)
    with open(TEL_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    binp = os.path.join(REPO, "target", "debug", "telc.exe")
    if not os.path.isfile(binp):
        binp = os.path.join(REPO, "target", "debug", "telc")
    out_png = os.path.join(REPO, PNG_REL)
    if os.path.isfile(out_png):
        os.remove(out_png)

    p = subprocess.run(
        [binp, TEL_PATH, "--interpret"],
        cwd=REPO, capture_output=True, text=True, timeout=180,
    )
    ok = p.returncode == 0 and os.path.isfile(out_png)
    print(f"integers 2..{N_MAX}  primes={n_primes}  lit cells={n_cells}")
    print(f"draw_lines≈{n_cells * fill}  canvas={canvas_w}×{canvas_h}px")
    print(f"telc rc={p.returncode}  png={'OK ' + str(os.path.getsize(out_png)) + 'B' if ok else 'MISSING'}")
    print(f"tel: {TEL_PATH}")
    print(f"png: {out_png}")
    if not ok:
        print((p.stdout + p.stderr)[-400:])


if __name__ == "__main__":
    main()
