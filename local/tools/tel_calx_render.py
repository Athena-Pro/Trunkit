#!/usr/bin/env python3
"""Render calx number theory *in TEL*: query calx, emit a .tel program that
paints the data with the Canvas API, and run telc to produce a PNG.

TEL programs can't reach Postgres, so this is a thin bridge: calx (the data) ->
generated .tel (the picture) -> telc --interpret (the renderer). The generated
program is straight-line (no while loops; capability-tree T1 mutable-loop support
is still pending) so it runs cleanly through the interpreter.

Picture: a bar per integer n in [1..N], height proportional to tau(n) (divisor
count), colored by abundance class:
  prime -> gold   perfect -> red   abundant -> orange   deficient -> blue
"""
import os, subprocess, psycopg

DSN = os.environ.get("TRUNK_DSN", "postgresql://trunk:trunk@localhost:5434/trunk")
REPO = "C:/AI-Local/tel-clean"
N = 96
BW, M, BASE_OFF, UNIT, TOPPAD = 5, 14, 16, 10, 14
TEL_PATH = os.path.join(REPO, "bootstrap", "output", "calx_render.tel")
PNG_REL = "bootstrap/output/calx_divisors.png"


def fetch():
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT i.n, i.is_prime, dc.tau, ds.sigma "
            "FROM calx.integers i "
            "JOIN calx.divisor_count dc ON dc.n=i.n "
            "JOIN calx.divisor_sum   ds ON ds.n=i.n "
            "WHERE i.n BETWEEN 1 AND %s ORDER BY i.n",
            (N,),
        )
        return cur.fetchall()


def klass(n, is_prime, sigma):
    if is_prime:
        return "c_prime"
    if sigma == 2 * n:
        return "c_perfect"
    if sigma > 2 * n:
        return "c_abundant"
    return "c_deficient"


def main():
    rows = fetch()
    max_tau = max(int(t) for _, _, t, _ in rows)
    width = M * 2 + N * BW
    baseline = TOPPAD + max_tau * UNIT
    height = baseline + BASE_OFF

    L = ["fn main() -> i32 {",
         f"  let canvas = Canvas::new({width}, {height});",
         "  let bg = Color::new(18, 18, 28);",
         "  canvas.clear(bg);",
         "  let c_prime = Color::new(255, 200, 0);",
         "  let c_perfect = Color::new(235, 45, 45);",
         "  let c_abundant = Color::new(235, 130, 45);",
         "  let c_deficient = Color::new(70, 130, 220);",
         "  let c_axis = Color::new(90, 90, 110);",
         f"  canvas.draw_line({M}, {baseline}, {width - M}, {baseline}, c_axis);"]

    counts = {"c_prime": 0, "c_perfect": 0, "c_abundant": 0, "c_deficient": 0}
    for n, is_prime, tau, sigma in rows:
        col = klass(n, is_prime, sigma)
        counts[col] += 1
        top = baseline - int(tau) * UNIT
        x0 = M + (n - 1) * BW
        for c in range(BW - 1):           # leave a 1px gap between bars
            x = x0 + c
            L.append(f"  canvas.draw_line({x}, {baseline}, {x}, {top}, {col});")

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
    p = subprocess.run([binp, TEL_PATH, "--interpret"], cwd=REPO,
                       capture_output=True, text=True, timeout=120)
    ok = p.returncode == 0 and os.path.isfile(out_png)
    print(f"N={N}  bars: {counts}")
    print(f"telc rc={p.returncode}  png={'OK ' + str(os.path.getsize(out_png)) + 'B' if ok else 'MISSING'}")
    print(f"tel: {TEL_PATH}")
    print(f"png: {out_png}")
    if not ok:
        print((p.stdout + p.stderr)[-400:])


if __name__ == "__main__":
    main()
