#!/usr/bin/env python3
"""Live-populate animation: render calx filling into the divisor bar chart, one
frame per growing element count, each frame painted by a generated .tel program
via telc, then assembled into a GIF.

Same honest bridge as tel_calx_render.py (calx -> .tel -> telc -> PNG), but
parameterized over k = number of integers populated so far, on a fixed canvas so
frames align. Shows the number-theoretic structure emerging as [1..N] populates.
"""
import os, subprocess, psycopg
from PIL import Image

DSN = os.environ.get("TRUNK_DSN", "postgresql://trunk:trunk@localhost:5434/trunk")
REPO = "C:/AI-Local/tel-clean"
N, STEP = 96, 2
BW, M, BASE_OFF, UNIT, TOPPAD = 5, 14, 16, 10, 14
FRAMES_DIR = os.path.join(REPO, "bootstrap", "output", "frames")
GIF_PATH = os.path.join(REPO, "bootstrap", "output", "calx_populating.gif")


def fetch():
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT i.n, i.is_prime, dc.tau, ds.sigma FROM calx.integers i "
            "JOIN calx.divisor_count dc ON dc.n=i.n JOIN calx.divisor_sum ds ON ds.n=i.n "
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


def gen_tel(rows, k, width, height, baseline, png_rel):
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
    for n, is_prime, tau, sigma in rows:
        if n > k:
            break
        col = klass(n, is_prime, sigma)
        top = baseline - int(tau) * UNIT
        x0 = M + (n - 1) * BW
        for c in range(BW - 1):
            x = x0 + c
            L.append(f"  canvas.draw_line({x}, {baseline}, {x}, {top}, {col});")
    L.append(f'  canvas.save_png("{png_rel}");')
    L.append("  return 0;\n}")
    return "\n".join(L) + "\n"


def main():
    rows = fetch()
    max_tau = max(int(t) for _, _, t, _ in rows)
    width = M * 2 + N * BW
    baseline = TOPPAD + max_tau * UNIT
    height = baseline + BASE_OFF

    os.makedirs(FRAMES_DIR, exist_ok=True)
    binp = os.path.join(REPO, "target", "debug", "telc.exe")
    if not os.path.isfile(binp):
        binp = os.path.join(REPO, "target", "debug", "telc")
    tel_path = os.path.join(REPO, "bootstrap", "output", "_frame.tel")

    frame_files, ks = [], list(range(STEP, N + 1, STEP))
    if ks[-1] != N:
        ks.append(N)
    for idx, k in enumerate(ks):
        png_rel = f"bootstrap/output/frames/frame_{idx:03d}.png"
        with open(tel_path, "w", encoding="utf-8") as f:
            f.write(gen_tel(rows, k, width, height, baseline, png_rel))
        out_png = os.path.join(REPO, png_rel)
        if os.path.isfile(out_png):
            os.remove(out_png)
        p = subprocess.run([binp, tel_path, "--interpret"], cwd=REPO,
                           capture_output=True, text=True, timeout=120)
        if p.returncode != 0 or not os.path.isfile(out_png):
            print(f"frame k={k} FAILED rc={p.returncode}: {(p.stdout + p.stderr)[-200:]}")
            return
        frame_files.append(out_png)
        print(f"  frame {idx:02d}  k={k:3d}  {os.path.getsize(out_png)}B")

    imgs = [Image.open(f).convert("P", palette=Image.ADAPTIVE) for f in frame_files]
    durations = [110] * (len(imgs) - 1) + [1400]   # hold the final frame
    imgs[0].save(GIF_PATH, save_all=True, append_images=imgs[1:],
                 duration=durations, loop=0, optimize=True)
    print(f"frames: {len(frame_files)}  gif: {GIF_PATH} ({os.path.getsize(GIF_PATH)}B)")


if __name__ == "__main__":
    main()
