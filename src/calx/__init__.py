"""calx: a computational blackboard for the integers, on PostgreSQL."""

import sysconfig
from pathlib import Path

__version__ = "0.1.0"


def get_shared_data_dir(name: str) -> Path:
    data_path = Path(sysconfig.get_path("data")) / "share" / "trunkit" / name
    if data_path.exists():
        return data_path
    return Path(__file__).resolve().parents[2] / name
