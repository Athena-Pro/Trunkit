"""calx.methods — universal verify-easy / find-hard verification kernels.

The four METHODS.md reference kernels, promoted from repo-root scripts into
the package so they are importable, tested by the suite, and dispatched by
``calx.kernel.verify_witness`` (schemas: ``arith_check``, ``quote_carry``,
``csp_carry``, ``puzzle_parity``) — which also exposes them through
``trunkit verify --bundle`` and the trunkit-mcp ``kernel_verify`` tool.

Every kernel is deterministic, stdlib-only, and three-valued:
valid / refuted / unverified — never a guess.
"""

from calx.methods.arith_check import check_arith_check
from calx.methods.csp_carry import check_csp_carry
from calx.methods.parity_puzzle import check_puzzle_parity
from calx.methods.quote_carry import check_quote_carry

__all__ = [
    "check_arith_check",
    "check_csp_carry",
    "check_puzzle_parity",
    "check_quote_carry",
]
