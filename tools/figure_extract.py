"""Figure → data extraction (STARTING POINT) for the cert vision layer.

Turns a clean plot image into a numeric data series, so the *data* can be
attested with capabilities calx already has (OEIS match, or a comp_sql probe).
Like image_features.py this is an OUT-OF-PACKAGE tool: it needs Pillow (the
optional [image] extra) but no numpy/opencv — the pixel work is pure Python, so
the calx core stays psycopg-only.

What it does:
  * grayscale + threshold to isolate dark marks on a light background,
  * for each image column, take the mean row of dark pixels (a single-valued
    curve y(x)) — the tractable line/scatter case,
  * map pixel coords → data coords via a 2-point linear calibration you supply,
  * emit the (x, y) series as JSON or CSV.

HONEST LIMITS (why this is a scaffold, not a finished extractor):
  - assumes ONE curve, single-valued in x; multi-series/scatter clouds need
    clustering this does not do;
  - gridlines, axis ticks, legends, and text are not masked — crop to the plot
    area first, or they become spurious "marks";
  - anti-aliasing/compression soften the threshold; tune --threshold;
  - calibration is yours to provide (no automatic axis OCR — that would pull in
    a heavy dependency this layer deliberately avoids).

Usage:
  python tools/figure_extract.py extract fig.png \
      --calib-x 60 0 980 100  --calib-y 540 0 40 1.0  [--threshold 128] [--csv]

  --calib-x PX0 DX0 PX1 DX1 : pixels PX0,PX1 map to data x DX0,DX1
  --calib-y PY0 DY0 PY1 DY1 : pixels PY0,PY1 map to data y DY0,DY1 (PY grows down)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _linmap(p0, d0, p1, d1):
    if p1 == p0:
        raise ValueError("calibration pixels must differ")
    scale = (d1 - d0) / (p1 - p0)
    return lambda p: d0 + (p - p0) * scale


def extract_curve(path: Path, threshold: int, calib_x, calib_y) -> list[tuple[float, float]]:
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        sys.exit("error: Pillow not installed. Install the optional extra: pip install 'trunkit[image]'")
    with Image.open(path) as im:
        g = im.convert("L")
        w, h = g.size
        px = g.load()

    fx = _linmap(*calib_x)
    fy = _linmap(*calib_y)
    series: list[tuple[float, float]] = []
    for x in range(w):
        dark_rows = [y for y in range(h) if px[x, y] < threshold]
        if not dark_rows:
            continue
        y_mean = sum(dark_rows) / len(dark_rows)
        series.append((fx(x), fy(y_mean)))
    return series


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="figure_extract")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract", help="extract a single-curve (x,y) series from a plot")
    e.add_argument("image")
    e.add_argument("--threshold", type=int, default=128, help="dark-mark cutoff 0..255")
    e.add_argument("--calib-x", nargs=4, type=float, required=True,
                   metavar=("PX0", "DX0", "PX1", "DX1"))
    e.add_argument("--calib-y", nargs=4, type=float, required=True,
                   metavar=("PY0", "DY0", "PY1", "DY1"))
    e.add_argument("--csv", action="store_true", help="emit CSV instead of JSON")
    args = ap.parse_args(argv)

    series = extract_curve(Path(args.image), args.threshold,
                           tuple(args.calib_x), tuple(args.calib_y))
    if args.csv:
        print("x,y")
        for x, y in series:
            print(f"{x:.6g},{y:.6g}")
    else:
        print(json.dumps({"points": len(series),
                          "series": [[round(x, 6), round(y, 6)] for x, y in series]},
                         indent=2))
    # Next step (manual, by design): round a monotone integer y-series and feed
    # `trunkit oeis-match` / a comp_sql probe to attest what sequence it is.
    return 0


if __name__ == "__main__":
    sys.exit(main())
