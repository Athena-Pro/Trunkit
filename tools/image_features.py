"""Out-of-package image descriptor tool for the cert vision layer.

Decodes an image (Pillow — the optional [image] extra, NOT a core dependency),
computes its sha256 (exact-integrity anchor) and the deterministic gray16c
descriptor, and either prints JSON or registers it into the cert.image_artifact
registry via cert.register_image.

Usage:
    python tools/image_features.py describe <image>            # -> JSON to stdout
    python tools/image_features.py register <image> [--label L] # -> register in DB

Keeping decode here (not in calx core) preserves the psycopg-only core, exactly
like tools/lean_check.sh keeps the Lean toolchain outside the package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "src"))
from calx import imagefeatures as imf  # noqa: E402

PG_DSN = os.environ.get("CALX_DSN", "postgresql://trunk:trunk@localhost:5434/trunk")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def describe(path: Path, fourier: bool = False) -> dict:
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        sys.exit("error: Pillow not installed. Install the optional extra: pip install 'trunkit[image]'")
    with Image.open(path) as im:
        width, height = im.size
        if fourier:
            side = imf.FOURIER_GRID * 2  # decode at higher res; descriptor downsamples internally
            gray = im.convert("L").resize((side, side), Image.BILINEAR)
            px = list(gray.getdata())
            matrix = [[float(v) for v in px[i * side:(i + 1) * side]] for i in range(side)]
            vector = imf.fourier_descriptor_from_matrix(matrix)
            kind = imf.FOURIER_VECTOR_KIND
        else:
            gray = im.convert("L").resize((imf.GRID, imf.GRID), Image.BILINEAR)
            vector = imf.finalize_descriptor([float(v) for v in gray.getdata()])
            kind = imf.VECTOR_KIND
    return {
        "sha256": sha256_file(path),
        "vector_kind": kind,
        "dims": len(vector),
        "vector": vector,
        "width": width,
        "height": height,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="image_features")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("describe", help="print sha256 + descriptor as JSON")
    d.add_argument("image")
    d.add_argument("--fourier", action="store_true",
                   help="radial-binned magnitude-spectrum descriptor (texture/frequency)")
    r = sub.add_parser("register", help="register the image in cert.image_artifact")
    r.add_argument("image")
    r.add_argument("--label")
    r.add_argument("--fourier", action="store_true",
                   help="radial-binned magnitude-spectrum descriptor (texture/frequency)")
    r.add_argument("--dsn", default=PG_DSN)
    args = ap.parse_args(argv)

    info = describe(Path(args.image), fourier=args.fourier)

    if args.cmd == "describe":
        print(json.dumps(info, indent=2))
        return 0

    import psycopg
    with psycopg.connect(args.dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT (cert.register_image(%s,%s,%s,%s,%s,%s,%s::jsonb)).id",
            (info["sha256"], info["vector_kind"], info["vector"],
             info["width"], info["height"], args.label, json.dumps({})),
        )
        image_id = cur.fetchone()[0]
    print(f"registered image {image_id}: sha256={info['sha256'][:12]}… "
          f"kind={info['vector_kind']} dims={info['dims']} label={args.label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
