#!/usr/bin/env python3
"""Render an Ulam prime spiral *in TEL*: query calx, emit a .tel program that
paints each integer on a square spiral grid coloured by abundance class, then
run telc to produce a PNG.

Same honest bridge as tel_calx_render.py (calx → .tel → telc → PNG).

Spiral layout: integer 1 at centre, coiling outward.  Each cell is CELL×CELL
pixels with a 1-px gap on two sides so the grid reads clearly.

Colour scheme (matches divisor bar chart):
  prime      → gold   (255, 200,   0)
  perfect    → red    (235,  45,  45)
  abundant   → orange (235, 130,  45)
  deficient  → blue   ( 70, 130, 220)
  1          → grey   (140, 140, 155)
"""
import os
import subprocess
import psycopg

DSN = os.environ.get("TRUNK_DSN", "postgresql://trunk:trunk@localhost:5434/trunk")
REPO = "C:/AI-Local/tel-clean"

N_SIDE = 31          # grid is N_SIDE × N_SIDE  (31² = 961 ≤ 1000 in calx)
CELL   = 8           # pixels per cell (7 filled + 1 gap)
PAD    = 4           # canvas margin
N_MAX  = N_SIDE * N_SIDE

TEL_PATH = os.path.join(REPO, "bootstrap", "output", "calx_ulam.tel")
PNG_REL  = "bootstrap/output/calx_ulam.png"


# ---------------------------------------------------------------------------
# Ulam spiral: map integer n → (col, row) in a N_SIDE × N_SIDE grid
# ---------------------------------------------------------------------------
def ulam_positions(n_side: int) -> dict[int, tuple[int, int]]:
    """Return {n: (col, row)} for n = 1 .. n_side² in Ulam spiral order.

    Integer 1 is at the centre; the spiral unwinds right → up → left → down,
    with segment lengths 1,1,2,2,3,3,4,4 … doubling every two segments.
    """
    total = n_side * n_side
    cx = cy = n_side // 2
    pos: dict[int, tuple[int, int]] = {1: (cx, cy)}
    x, y = cx, cy
    n = 2
    # directions: right (+col), up (−row), left (−col), down (+row)
    dirs = [(1, 0), (0, -1), (-1, 0), (0, 1)]
    di   = 0
    seg  = 1   # current segment length (increases by 1 every two directions)
    while n <= total:
        for _ in range(2):               # two directions share each segment length
            dx, dy = dirs[di % 4]
            di += 1
            for _ in range(seg):
                x += dx
                y += dy
                if n <= total:
                    pos[n] = (x, y)
                    n += 1
        seg += 1
    return pos


# ---------------------------------------------------------------------------
# Data from calx
# ---------------------------------------------------------------------------
def fetch() -> dict[int, tuple[bool, str]]:
    """Return {n: (is_prime, klass)} for n = 1 .. N_MAX."""
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT i.n, i.is_prime, ds.sigma "
            "FROM calx.integers i "
            "JOIN calx.divisor_sum ds ON ds.n = i.n "
            "WHERE i.n BETWEEN 1 AND %s ORDER BY i.n",
            (N_MAX,),
        )
        result: dict[int, tuple[bool, str]] = {}
        for n, is_prime, sigma in cur.fetchall():
            sigma_i = int(sigma)
            if n == 1:
                klass = "c_one"
            elif is_prime:
                klass = "c_prime"
            elif sigma_i == 2 * n:
                klass = "c_perfect"
            elif sigma_i > 2 * n:
                klass = "c_abundant"
            else:
                klass = "c_deficient"
            result[n] = (is_prime, klass)
    return result


# ---------------------------------------------------------------------------
# TEL code generation
# ---------------------------------------------------------------------------
def main() -> None:
    data  = fetch()
    spiral = ulam_positions(N_SIDE)

    width  = N_SIDE * CELL + 2 * PAD
    height = N_SIDE * CELL + 2 * PAD

    L = [
        "fn main() -> i32 {",
        f"  let canvas = Canvas::new({width}, {height});",
        "  let bg        = Color::new(12, 12, 20);",
        "  canvas.clear(bg);",
        "  let c_one       = Color::new(140, 140, 155);",
        "  let c_prime     = Color::new(255, 200,   0);",
        "  let c_perfect   = Color::new(235,  45,  45);",
        "  let c_abundant  = Color::new(235, 130,  45);",
        "  let c_deficient = Color::new( 70, 130, 220);",
    ]

    primes_found = 0
    for n in range(1, N_MAX + 1):
        if n not in spiral or n not in data:
            continue
        col, row = spiral[n]
        _, klass = data[n]
        if klass == "c_prime":
            primes_found += 1

        px = PAD + col * CELL   # top-left x of cell
        py = PAD + row * CELL   # top-left y of cell
        fill = CELL - 1         # leave 1-px gap on right and bottom

        # Draw filled square: fill horizontal scan-lines top-to-bottom
        for dy in range(fill):
            L.append(f"  canvas.draw_line({px}, {py + dy}, {px + fill - 1}, {py + dy}, {klass});")

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
    print(f"N={N_SIDE}² ({N_MAX} cells)  primes={primes_found}")
    print(f"draw_lines≈{(N_MAX) * (CELL - 1)}  canvas={width}×{height}px")
    print(f"telc rc={p.returncode}  png={'OK ' + str(os.path.getsize(out_png)) + 'B' if ok else 'MISSING'}")
    print(f"tel: {TEL_PATH}")
    print(f"png: {out_png}")
    if not ok:
        print((p.stdout + p.stderr)[-400:])


if __name__ == "__main__":
    main()
