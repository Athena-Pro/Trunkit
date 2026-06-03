"""Prefer this checkout's src/ packages when running tools/ directly."""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
_src_str = str(_SRC)
if _SRC.is_dir() and _src_str not in sys.path:
    sys.path.insert(0, _src_str)
