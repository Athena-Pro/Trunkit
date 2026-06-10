"""Register every calx SQL procedure / function as a versioned Curry function.

Run once after creating ``.curry/config.json``:

    python tools/register_calx_in_curry.py

Idempotent: if a constant or function at version 1 already exists, the
declaration is skipped (with a printed note). Re-running after editing this
script does NOT replace existing v1 declarations — bump the version constant
below and declare v2 to evolve a wrapper.

Each Curry function's body is a single Python expression that returns a
"call plan" dict consumed by ``src/calx/curry_adapter.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


# --- import curry_core from the path declared in .curry/config.json ---

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / ".curry" / "config.json"

with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
    _config = json.load(fh)

_curry_path = _config["curry_module_path"]
if _curry_path not in sys.path:
    sys.path.insert(0, _curry_path)

import curry_core  # noqa: E402  (after sys.path tweak)


# --- declaration data ---

CALX_SCHEMA_VERSION = 1


CONSTANTS: List[Dict[str, Any]] = [
    {
        "id": "calx_schema_version",
        "version": 1,
        "value": CALX_SCHEMA_VERSION,
        "type_signature": "Int32",
        "description": (
            "Monotonic integer identifying the calx SQL schema (sql/01..07_*.sql) "
            "currently considered authoritative. Bump when any procedure's "
            "signature or semantics changes and re-declare wrapper functions at "
            "a new version pinned to this constant."
        ),
    },
    {
        "id": "calx_default_dsn",
        "version": 1,
        "value": "postgresql:///",
        "type_signature": "String",
        "description": (
            "Documentation-only default DSN. Actual connections go through "
            "src/calx/db.py:resolve_dsn(), which still prefers an explicit "
            "argument, then $CALX_DSN, then libpq defaults."
        ),
    },
    {
        "id": "calx_sql_files",
        "version": 1,
        "value": [
            "01_schema.sql",
            "02_views.sql",
            "03_generate.sql",
            "04_crt.sql",
            "05_dynamics.sql",
            "06_oeis_match.sql",
            "07_compositions.sql",
        ],
        "type_signature": "Json",
        "description": "Ordered list of SQL files applied by db.apply_schema().",
    },
    {
        "id": "calx_default_orbit_max_steps",
        "version": 1,
        "value": 200,
        "type_signature": "Int32",
        "description": "Default max_steps argument for trace_orbit (matches PL/pgSQL default).",
    },
    {
        "id": "calx_default_orbit_lim",
        "version": 1,
        "value": 10_000_000,
        "type_signature": "Int32",
        "description": (
            "Default ceiling for trace_orbit (PL/pgSQL default; the procedure "
            "clamps to MAX(integers.n) at runtime regardless)."
        ),
    },
    {
        "id": "calx_default_generate_limit",
        "version": 1,
        "value": 1_000_000,
        "type_signature": "Int32",
        "description": "Default --limit for the calx generate CLI command.",
    },
]


def _plan_function_scalar(proc: str, arg_names: List[str]) -> str:
    args_list = "[" + ", ".join(arg_names) + "]"
    return (
        '{"sql": "function", "proc": "' + proc + '", '
        '"args": ' + args_list + ', '
        '"returns": "scalar", '
        '"schema_version": calx_schema_version}'
    )


def _plan_function_rows(proc: str, arg_names: List[str]) -> str:
    args_list = "[" + ", ".join(arg_names) + "]"
    return (
        '{"sql": "function", "proc": "' + proc + '", '
        '"args": ' + args_list + ', '
        '"returns": "rows", '
        '"schema_version": calx_schema_version}'
    )


def _plan_procedure(proc: str, arg_names: List[str]) -> str:
    args_list = "[" + ", ".join(arg_names) + "]"
    return (
        '{"sql": "procedure", "proc": "' + proc + '", '
        '"args": ' + args_list + ', '
        '"returns": "none", '
        '"schema_version": calx_schema_version}'
    )


# Each entry: (curry_name, sql_kind, sql_proc, expected_args, returns, description, arg_descriptions)
FUNCTIONS: List[Dict[str, Any]] = [
    # ---- CRT scalar functions (04_crt.sql) ----
    {
        "name": "calx_mod_inverse",
        "body": _plan_function_scalar("mod_inverse", ["a", "m"]),
        "expected_args": ["a", "m"],
        "description": "Modular inverse of a mod m. Returns NULL when gcd(a,m) != 1.",
        "arg_descriptions": {
            "a": "Integer to invert (BIGINT; can be negative — normalized to [0, m) internally).",
            "m": "Modulus (BIGINT, m >= 2).",
        },
    },
    {
        "name": "calx_crt",
        "body": _plan_function_scalar("crt", ["remainders", "moduli"]),
        "expected_args": ["remainders", "moduli"],
        "description": (
            "CRT-combine a list of (remainder, modulus) pairs into the smallest "
            "non-negative x satisfying x ≡ remainders[i] (mod moduli[i]). "
            "Returns NULL if the moduli are not pairwise coprime in a way the "
            "PL/pgSQL implementation rejects."
        ),
        "arg_descriptions": {
            "remainders": "Python list of BIGINTs — same length as moduli.",
            "moduli": "Python list of BIGINTs — same length as remainders.",
        },
    },
    {
        "name": "calx_crt_reconstruct",
        "body": _plan_function_scalar("crt_reconstruct", ["residues", "prime_powers"]),
        "expected_args": ["residues", "prime_powers"],
        "description": (
            "Reconstruct an integer from its residue tuple modulo a list of "
            "pairwise-coprime prime powers (the inverse of crt_decompose)."
        ),
        "arg_descriptions": {
            "residues": "Python list of BIGINTs aligned with prime_powers.",
            "prime_powers": "Python list of pairwise-coprime BIGINT prime powers.",
        },
    },
    # ---- Dynamics step functions (05_dynamics.sql) ----
    {
        "name": "calx_aliquot_step",
        "body": _plan_function_scalar("aliquot_step", ["n"]),
        "expected_args": ["n"],
        "description": "One step of the aliquot dynamics: sigma(n) - n.",
        "arg_descriptions": {
            "n": "Integer to iterate (BIGINT, 1 <= n <= MAX(integers.n) in the calx DB).",
        },
    },
    {
        "name": "calx_arithmetic_derivative",
        "body": _plan_function_scalar("arithmetic_derivative", ["n"]),
        "expected_args": ["n"],
        "description": "Arithmetic derivative n' = sum over p^k || n of k * n / p.",
        "arg_descriptions": {
            "n": "Integer to differentiate (BIGINT, 1 <= n <= MAX(integers.n)).",
        },
    },
    {
        "name": "calx_signature_step",
        "body": _plan_function_scalar("signature_step", ["n"]),
        "expected_args": ["n"],
        "description": "Map n to the smallest integer sharing its prime signature (exponent multiset).",
        "arg_descriptions": {
            "n": "Integer to project (BIGINT, 1 <= n <= MAX(integers.n)).",
        },
    },
    {
        "name": "calx_crt_lift_step",
        "body": _plan_function_scalar("crt_lift_step", ["n", "depth"]),
        "expected_args": ["n", "depth"],
        "description": "Lift n by perturbing one CRT residue at the given depth into the residue space.",
        "arg_descriptions": {
            "n": "Integer to lift (BIGINT).",
            "depth": "Index of the prime-power coordinate to perturb (INTEGER, 1-based).",
        },
    },
    {
        "name": "calx_radical_step",
        "body": _plan_function_scalar("radical_step", ["n"]),
        "expected_args": ["n"],
        "description": "Map n to its squarefree radical rad(n) = product of distinct prime factors.",
        "arg_descriptions": {
            "n": "Integer to radicalize (BIGINT, 1 <= n <= MAX(integers.n)).",
        },
    },
    # ---- CRT / dynamics table functions ----
    {
        "name": "calx_ext_gcd",
        "body": _plan_function_rows("ext_gcd", ["a", "b"]),
        "expected_args": ["a", "b"],
        "description": "Extended Euclidean algorithm — returns one row (g, s, t) with g = s*a + t*b.",
        "arg_descriptions": {
            "a": "First operand (BIGINT, may be negative).",
            "b": "Second operand (BIGINT, may be negative).",
        },
    },
    {
        "name": "calx_crt_combine",
        "body": _plan_function_rows("crt_combine", ["a", "m", "b", "n"]),
        "expected_args": ["a", "m", "b", "n"],
        "description": "Combine two congruences x ≡ a (mod m), x ≡ b (mod n) into (x, modulus = lcm(m,n)).",
        "arg_descriptions": {
            "a": "First remainder (BIGINT).",
            "m": "First modulus (BIGINT, m >= 1).",
            "b": "Second remainder (BIGINT).",
            "n": "Second modulus (BIGINT, n >= 1).",
        },
    },
    {
        "name": "calx_wheel_spokes",
        "body": _plan_function_rows("wheel_spokes", ["k"]),
        "expected_args": ["k"],
        "description": "Residues coprime to the product of the first k primes (the k-wheel spokes).",
        "arg_descriptions": {
            "k": "Number of leading primes forming the wheel (INTEGER, k >= 1; k=4 → mod-210).",
        },
    },
    {
        "name": "calx_progression_intersect",
        "body": _plan_function_rows(
            "progression_intersect", ["r1", "m1", "r2", "m2"]
        ),
        "expected_args": ["r1", "m1", "r2", "m2"],
        "description": (
            "Intersect two arithmetic progressions r1 (mod m1) and r2 (mod m2). "
            "Returns one row with intersects=true and the combined (remainder, modulus) "
            "or intersects=false."
        ),
        "arg_descriptions": {
            "r1": "First remainder (BIGINT).",
            "m1": "First modulus (BIGINT, m1 >= 1).",
            "r2": "Second remainder (BIGINT).",
            "m2": "Second modulus (BIGINT, m2 >= 1).",
        },
    },
    {
        "name": "calx_crt_decompose",
        "body": _plan_function_rows("crt_decompose", ["target"]),
        "expected_args": ["target"],
        "description": "Decompose target into its (prime, prime_power, residue) triples per its factorization.",
        "arg_descriptions": {
            "target": "Integer to decompose (BIGINT, 1 <= target <= MAX(integers.n)).",
        },
    },
    {
        "name": "calx_characterize_relation",
        "body": _plan_function_rows("characterize_relation", ["n_val", "m_val"]),
        "expected_args": ["n_val", "m_val"],
        "description": (
            "Summarize how two integers n and m are related across calx's dynamical "
            "and structural layers (same orbit family, shared sequences, CRT distance, …)."
        ),
        "arg_descriptions": {
            "n_val": "First integer (BIGINT, 1 <= n_val <= MAX(integers.n)).",
            "m_val": "Second integer (BIGINT, 1 <= m_val <= MAX(integers.n)).",
        },
    },
    {
        "name": "calx_crt_class_neighbors",
        "body": _plan_function_rows("crt_class_neighbors", ["n_val", "depth"]),
        "expected_args": ["n_val", "depth"],
        "description": (
            "Integers within crt_lift distance ≤ depth of n. Returns (m, distance) "
            "rows ordered by distance."
        ),
        "arg_descriptions": {
            "n_val": "Center integer (BIGINT).",
            "depth": "Maximum lift distance to expand (INTEGER, e.g. 1 or 2).",
        },
    },
    {
        "name": "calx_shared_sequences",
        "body": _plan_function_rows("shared_sequences", ["n_val", "m_val"]),
        "expected_args": ["n_val", "m_val"],
        "description": "All OEIS-style sequences containing both n and m, with each sequence's id and name.",
        "arg_descriptions": {
            "n_val": "First integer (BIGINT).",
            "m_val": "Second integer (BIGINT).",
        },
    },
    # ---- Procedures ----
    {
        "name": "calx_trace_orbit",
        "body": _plan_procedure(
            "trace_orbit", ["start_n", "rel_type", "max_steps", "lim"]
        ),
        "expected_args": ["start_n", "rel_type", "max_steps", "lim"],
        "description": (
            "Trace a dynamical orbit from start_n under rel_type and persist it into the "
            "orbits / orbit_steps tables. Returns None; read the orbit back via SQL "
            "ordering by orbit_steps.step."
        ),
        "arg_descriptions": {
            "start_n": "Starting integer (BIGINT, 1 <= start_n <= MAX(integers.n)).",
            "rel_type": "One of 'ALIQUOT', 'ARITH_DERIV', 'SIGNATURE', 'RADICAL' (TEXT).",
            "max_steps": "Maximum orbit length before truncation (INTEGER, default in calx_default_orbit_max_steps = 200).",
            "lim": "Ceiling on n during iteration (BIGINT; the procedure also clamps to MAX(integers.n)).",
        },
    },
    {
        "name": "calx_generate_integer_database",
        "body": _plan_procedure("generate_integer_database", ["lim"]),
        "expected_args": ["lim"],
        "description": (
            "Pure PL/pgSQL pipeline: populate integers, primes, factorizations for n in [1, lim]. "
            "Slow past ~10⁶ — prefer the primesieve backend via CLI for large limits."
        ),
        "arg_descriptions": {
            "lim": "Inclusive upper bound on n (BIGINT, lim >= 1; default in calx_default_generate_limit = 1_000_000).",
        },
    },
    {
        "name": "calx_generate_factorizations_only",
        "body": _plan_procedure("generate_factorizations_only", ["lim"]),
        "expected_args": ["lim"],
        "description": (
            "Rebuild only the factorizations table for n in [1, lim], assuming integers and primes "
            "are already populated. Used after primesieve has loaded primes."
        ),
        "arg_descriptions": {
            "lim": "Inclusive upper bound on n (BIGINT, lim >= 1).",
        },
    },
]


def declare_constant_idempotent(db_handle, spec: Dict[str, Any]) -> str:
    try:
        db_handle.get_constant(spec["id"], spec["version"])
        return "skipped"
    except KeyError:
        db_handle.declare_constant(
            spec["id"],
            spec["version"],
            spec["value"],
            spec["type_signature"],
            description=spec.get("description"),
        )
        return "declared"


def declare_function_idempotent(db_handle, spec: Dict[str, Any]) -> str:
    try:
        db_handle.get_function(spec["name"], 1)
        return "skipped"
    except KeyError:
        db_handle.declare_function(
            name=spec["name"],
            version=1,
            body=spec["body"],
            constant_bindings={"calx_schema_version": 1},
            expected_args=spec["expected_args"],
            is_pure=True,
            description=spec["description"],
            arg_descriptions=spec["arg_descriptions"],
        )
        return "declared"


def main() -> int:
    session = curry_core.CurrySession.from_project(str(PROJECT_DIR))
    try:
        local = session.local_db
        print(f"calx project root: {PROJECT_DIR}")
        print(f"local Curry DB:    {local.db_path}")
        print()
        print("Constants:")
        for spec in CONSTANTS:
            status = declare_constant_idempotent(local, spec)
            print(f"  [{status}] {spec['id']}@v{spec['version']}")
        print()
        print("Functions:")
        for spec in FUNCTIONS:
            status = declare_function_idempotent(local, spec)
            print(f"  [{status}] {spec['name']}@v1  ({len(spec['expected_args'])} args)")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
