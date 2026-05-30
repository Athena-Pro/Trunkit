"""Shared kernel test vectors — the single source of truth for BOTH kernels.

The cert_kernel tier is implemented twice on purpose (SECURITY.md / the
independent-checker design): once in PL/pgSQL (cert.kernel_* in
src/calx/sql/94_cert_kernel.sql) and once in dependency-free Python
(calx.kernel.* in src/calx/kernel.py). That duplication is a deliberate
defense-in-depth — a consumer with only Python can re-check a bundle — but it
creates a divergence risk: the two implementations could disagree.

These vectors are consumed by BOTH:
  * tests/test_cert_kernel.py        — runs them through calx.kernel (DB-free)
  * tests/test_kernel_parity.py      — runs them through cert.kernel_verify in
                                       PostgreSQL AND asserts the SQL verdict
                                       equals the Python verdict, vector by vector.

Each vector is (id, witness, expected) where expected is True/False/None for
valid/refuted/unverified. Adding a kernel schema means adding vectors here once;
both sides then cover it and parity is enforced.
"""
from __future__ import annotations

# (id, witness dict, expected verdict: True | False | None)
VECTORS: list[tuple[str, dict, bool | None]] = [
    # ---- factorization ----
    ("fact_28_perfect",
     {"schema": "factorization", "n": 28, "factors": [[2, 2], [7, 1]],
      "asserts": {"perfect": True, "sigma": 56}}, True),
    ("fact_composite_base",
     {"schema": "factorization", "n": 28, "factors": [[4, 1], [7, 1]]}, False),
    ("fact_wrong_product",
     {"schema": "factorization", "n": 28, "factors": [[2, 2], [7, 2]]}, False),
    ("fact_wrong_sigma",
     {"schema": "factorization", "n": 28, "factors": [[2, 2], [7, 1]],
      "asserts": {"sigma": 99}}, False),
    ("fact_12_not_perfect",
     {"schema": "factorization", "n": 12, "factors": [[2, 2], [3, 1]],
      "asserts": {"perfect": False}}, True),
    ("fact_empty_unverified",
     {"schema": "factorization", "n": 28, "factors": []}, None),

    # ---- crt ----
    ("crt_valid",
     {"schema": "crt", "x": 8, "congruences": [[2, 3], [3, 5]]}, True),
    ("crt_wrong_x",
     {"schema": "crt", "x": 9, "congruences": [[2, 3], [3, 5]]}, False),
    ("crt_non_coprime",
     {"schema": "crt", "x": 1, "congruences": [[1, 4], [1, 6]]}, False),

    # ---- unit_fraction (Egyptian fractions, arXiv:1606.02117) ----
    ("uf_distinct_odd_one",
     {"schema": "unit_fraction", "target": 1,
      "denominators": [3, 5, 7, 9, 11, 15, 35, 45, 231],
      "constraints": {"distinct": True, "odd": True}}, True),
    ("uf_wrong_sum",
     {"schema": "unit_fraction", "target": 1, "denominators": [2, 3, 7]}, False),
    ("uf_even_under_odd",
     {"schema": "unit_fraction", "target": 1, "denominators": [2, 3, 6],
      "constraints": {"odd": True}}, False),
    ("uf_repeat_under_distinct",
     {"schema": "unit_fraction", "target": 1, "denominators": [2, 2],
      "constraints": {"distinct": True}}, False),
    ("uf_empty_unverified",
     {"schema": "unit_fraction", "target": 1, "denominators": []}, None),

    # ---- matrix_word (matrix-semigroup membership, arXiv:2604.15386) ----
    ("mw_aba_ok",
     {"schema": "matrix_word",
      "generators": {"a": [[1, 1], [0, 1]], "b": [[1, 0], [1, 1]]},
      "word": ["a", "b", "a"], "target": [[2, 3], [1, 2]]}, True),
    ("mw_wrong_target",
     {"schema": "matrix_word",
      "generators": {"a": [[1, 1], [0, 1]], "b": [[1, 0], [1, 1]]},
      "word": ["a", "b"], "target": [[1, 0], [0, 1]]}, False),
    ("mw_order_ba",
     {"schema": "matrix_word",
      "generators": {"a": [[1, 1], [0, 1]], "b": [[1, 0], [1, 1]]},
      "word": ["b", "a"], "target": [[2, 1], [1, 1]]}, False),
    ("mw_undefined_gen",
     {"schema": "matrix_word",
      "generators": {"a": [[1, 1], [0, 1]], "b": [[1, 0], [1, 1]]},
      "word": ["a", "z"], "target": [[1, 0], [0, 1]]}, None),
    ("mw_empty_word",
     {"schema": "matrix_word",
      "generators": {"a": [[1, 1], [0, 1]], "b": [[1, 0], [1, 1]]},
      "word": [], "target": [[1, 0], [0, 1]]}, None),

    # ---- dispatch ----
    ("unknown_schema_unverified", {"schema": "no_such_kernel"}, None),
]
