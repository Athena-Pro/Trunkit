"""Thin wrapper around the ``primesieve`` CLI.

We shell out to ``primesieve`` (kimwalisch/primesieve) to enumerate the primes
in [2, N] and stream them into Postgres via COPY. This is roughly two orders of
magnitude faster than running the Phase 2 sieve inside PL/pgSQL.

The binary is expected on ``$PATH``. Install:
  - Debian/Ubuntu:  apt install primesieve
  - macOS:          brew install primesieve
  - Windows:        download release from github.com/kimwalisch/primesieve
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator


class PrimesieveMissingError(RuntimeError):
    """Raised when the ``primesieve`` CLI cannot be located."""


def require_binary() -> str:
    path = shutil.which("primesieve")
    if path is None:
        raise PrimesieveMissingError(
            "primesieve CLI not found on $PATH. "
            "See https://github.com/kimwalisch/primesieve#installation."
        )
    return path


def iter_primes(limit: int) -> Iterator[int]:
    """Yield primes p with 2 ≤ p ≤ limit by streaming primesieve's stdout."""
    binary = require_binary()
    proc = subprocess.Popen(
        [binary, str(limit), "--print"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1 << 20,
    )
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            line = line.strip()
            if line:
                yield int(line)
    finally:
        proc.stdout.close()
        rc = proc.wait()
        if rc != 0:
            stderr = proc.stderr.read() if proc.stderr else ""
            raise RuntimeError(f"primesieve exited {rc}: {stderr}")
