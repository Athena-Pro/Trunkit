"""
benchmarks/sequence_bench.py
============================
Token-cost comparison for FIVE approaches to generating number sequences.

The five approaches
-------------------
  raw_llm            No tools: model must recall or compute the sequence alone.
  nerode_grounded    Model calls nerode_sequence tool (parallel DFA run via DB).
  calx_grounded      Model calls calx_lookup tool (arithmetic facts, multi-turn).
  llm_builds_dfa     Model writes regex(es) → we build the DFA → run it and
                     feed the result back as a second turn.
  nerode_cached      Sequence was pre-built and stored in nerode.sequence_cache.
                     Tool returns pre-computed result with a single DB read —
                     the "pre-cached memory" path that stateless LLMs benefit from.

Three sequences
---------------
  accept_quad    Step n → 4-tuple of 0/1: does cycle_{4,6,9,10} accept at step n?
                 Length 60.  Ground truth: (n%4==0, n%6==0, n%9==0, n%10==0).
                 Nerode-native; calx N/A.

  lcm_accept     Accepting positions of cycle_4 × cycle_9 DFA for steps 0..179.
                 Ground truth: multiples of lcm(4,9)=36 up to 179 → [0,36,72,108,144].
                 Nerode-native; calx N/A.

  arith_deriv    Arithmetic derivative D(n) for n = 1..50.
                 D(1)=0, D(p)=1, extended by Leibniz rule.
                 Calx-native (live DB lookup); nerode N/A (not regular).

Metrics reported
----------------
  input_tokens   cumulative across all turns in the conversation
  output_tokens  cumulative model output (does NOT include tool-result tokens)
  turns          API call count
  cost_usd       estimated at Gemini Flash pricing
  accuracy       fraction of terms matching ground truth element-wise

Usage
-----
  python benchmarks/sequence_bench.py                    # all sequences, all approaches
  python benchmarks/sequence_bench.py --seq accept_quad  # single sequence
  python benchmarks/sequence_bench.py --skip-llm         # DB-side only (no API calls)
  python benchmarks/sequence_bench.py --length 20        # override sequence length
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import psycopg

from nerode.db import apply_schema, resolve_dsn, TRUNKIT_DSN

# ---------------------------------------------------------------------------
# Pricing (Gemini 2.5 Flash, thinking disabled)
# ---------------------------------------------------------------------------

GEMINI_MODEL        = "gemini-2.5-flash"
INPUT_PRICE_PER_TOK = 0.10 / 1_000_000   # USD per input token
OUTPUT_PRICE_PER_TOK = 0.40 / 1_000_000  # USD per output token

NERODE_DSN = resolve_dsn()
CALX_DSN   = os.environ.get("CALX_DSN", TRUNKIT_DSN)


# ---------------------------------------------------------------------------
# Sequence definitions
# ---------------------------------------------------------------------------

# Each entry:
#   description    : natural-language task description given to the LLM
#   length         : number of terms to generate
#   ground_truth   : callable(ids, calx_conn) → list of terms
#   nerode_mode    : "parallel_run" | "accepting_positions" | None
#   calx_available : bool — calx useful for this sequence?
#   llm_build_hint : hint for llm_builds_dfa approach (regex(es) to try)

SEQUENCES: dict[str, dict] = {
    "accept_quad": {
        "description": (
            "For n = 0, 1, 2, ..., {length_minus_1}, compute the 4-tuple\n"
            "  (n mod 4 == 0, n mod 6 == 0, n mod 9 == 0, n mod 10 == 0)\n"
            "where each element is 1 if true, 0 if false.\n"
            "Output a JSON array of 4-element integer arrays, one per value of n."
        ),
        "length": 60,
        "ground_truth": lambda ids, _calx: [
            [int(n % 4 == 0), int(n % 6 == 0), int(n % 9 == 0), int(n % 10 == 0)]
            for n in range(60)
        ],
        "nerode_mode": "parallel_run",
        "nerode_slugs": ["cycle_4", "cycle_6", "cycle_9", "cycle_10"],
        "calx_available": False,
        "llm_build_regexes": ["(aaaa)*", "(aaaaaa)*", "(aaaaaaaaa)*", "(aaaaaaaaaa)*"],
        "llm_build_mode": "parallel_accept",  # run all and extract accept bits
        "cache_key": "accept_quad:60",
        "cache_mode": "parallel_accept",
    },
    "lcm_accept": {
        "description": (
            "List every integer n in [0, {length_minus_1}] such that:\n"
            "  n is divisible by 4  AND  n is divisible by 9.\n"
            "Output a JSON array of integers in ascending order."
        ),
        "length": 180,
        "ground_truth": lambda ids, _calx: [
            n for n in range(180) if n % 4 == 0 and n % 9 == 0
        ],
        "nerode_mode": "accepting_positions",
        "nerode_slugs": [("cycle_4", "cycle_9")],   # product pair
        "calx_available": False,
        "llm_build_regexes": ["(aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa)*"],  # (a^36)*
        "llm_build_mode": "accepting_positions",
        "cache_key": "lcm_accept:180",
        "cache_mode": "accepting_positions",
    },
    "arith_deriv": {
        "description": (
            "Compute the arithmetic derivative D(n) for n = 1, 2, ..., {length_minus_1}.\n"
            "Rules: D(1)=0, D(p)=1 for prime p, D(p^k)=k*p^(k-1) for prime powers,\n"
            "and D(ab)=D(a)*b+a*D(b) (Leibniz rule) extended to all positive integers.\n"
            "Output a JSON array of integers [D(1), D(2), ..., D({length_minus_1})]."
        ),
        "length": 51,   # D(1) through D(50), so 50 values
        "ground_truth": lambda ids, calx: _load_arith_deriv(calx, 50),
        "nerode_mode": None,   # not regular
        "nerode_slugs": [],
        "calx_available": True,
        "llm_build_regexes": None,
        "llm_build_mode": None,   # not regular
        "cache_key":  "arith_deriv:50",     # stored via 'store' mode (no DFA walk)
        "cache_mode": "store",
    },
}


def _load_arith_deriv(calx_conn, n_max: int) -> list[int]:
    """Load arithmetic derivatives D(1)..D(n_max) from calx DB."""
    rows = calx_conn.execute(
        "SELECT n, calx.arithmetic_derivative(n) FROM calx.integers "
        "WHERE n BETWEEN 1 AND %s ORDER BY n",
        (n_max,),
    ).fetchall()
    return [r[1] for r in rows]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class BenchResult:
    approach:      str
    seq_name:      str
    output:        Optional[list]    # None = skipped or error
    error:         Optional[str]
    input_tokens:  int
    output_tokens: int
    turns:         int
    wall_ms:       float
    cost_usd:      float
    accuracy:      Optional[float]  # filled by scorer after all approaches run

    def is_na(self) -> bool:
        return self.output is None and self.error in ("NOT_APPLICABLE", "SKIPPED")


def _cost(inp: int, out: int) -> float:
    return inp * INPUT_PRICE_PER_TOK + out * OUTPUT_PRICE_PER_TOK


# ---------------------------------------------------------------------------
# Gemini multi-turn tool-use helper
# ---------------------------------------------------------------------------

@dataclass
class ToolConvResult:
    final_text:         str
    total_input_tokens: int
    total_output_tokens: int
    turns:              int


def _gemini_tool_conversation(
    client,
    system: str,
    user_text: str,
    tool_def: dict,
    executor: Callable[[str, dict], Any],
    max_turns: int = 60,
    thinking_budget: int = 0,
) -> ToolConvResult:
    """
    Run a Gemini conversation with one tool available.
    The executor is called with (tool_name, args_dict) and must return
    a JSON-serialisable result.

    Token accounting: each `generate_content` call returns usage_metadata;
    we accumulate `prompt_token_count` (input) and `candidates_token_count`
    (output) across all turns.
    """
    from google.genai import types

    schema = types.Schema(
        type="OBJECT",
        properties={
            k: types.Schema(**_schema_prop(v))
            for k, v in tool_def["parameters"]["properties"].items()
        },
        required=tool_def["parameters"].get("required", []),
    )
    gemini_tool = types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name=tool_def["name"],
                description=tool_def["description"],
                parameters=schema,
            )
        ]
    )

    thinking_cfg = types.ThinkingConfig(thinking_budget=thinking_budget)
    config = types.GenerateContentConfig(
        system_instruction=system,
        tools=[gemini_tool],
        thinking_config=thinking_cfg,
        max_output_tokens=4096,
    )

    contents = [types.Content(role="user", parts=[types.Part(text=user_text)])]

    total_in = total_out = 0
    turns = 0
    final_text = ""

    for _turn in range(max_turns):
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=config,
        )
        turns += 1

        usage = resp.usage_metadata
        if usage:
            total_in  += usage.prompt_token_count or 0
            total_out += usage.candidates_token_count or 0

        # Collect function calls from this turn
        fn_calls = []
        text_parts = []
        for cand in resp.candidates:
            for part in cand.content.parts:
                if hasattr(part, "function_call") and part.function_call:
                    fn_calls.append(part.function_call)
                elif hasattr(part, "text") and part.text:
                    text_parts.append(part.text)

        if not fn_calls:
            # No more tool calls — model produced final text
            final_text = "\n".join(text_parts)
            break

        # Append the model turn to history
        contents.append(resp.candidates[0].content)

        # Execute each tool call and prepare responses
        tool_parts = []
        for fc in fn_calls:
            args = dict(fc.args) if fc.args else {}
            try:
                result = executor(fc.name, args)
            except Exception as exc:
                result = {"error": str(exc)}

            tool_parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        name=fc.name,
                        response={"result": result},
                    )
                )
            )

        contents.append(types.Content(role="user", parts=tool_parts))

    return ToolConvResult(
        final_text=final_text,
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        turns=turns,
    )


def _schema_prop(v: dict) -> dict:
    """Convert a simple JSON-schema property dict to google.genai.types.Schema kwargs."""
    t = v.get("type", "STRING").upper()
    if t == "INTEGER":
        return {"type": "INTEGER"}
    if t == "ARRAY":
        items = v.get("items", {})
        items_type = items.get("type", "STRING").upper()
        return {"type": "ARRAY", "items": {"type": items_type}}
    return {"type": "STRING"}


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_json_array(text: str) -> Optional[list]:
    """Extract the first JSON array from model text. Returns None on failure."""
    text = text.strip()
    # Strip markdown fences
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    # Find first [...] block
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        return None
    try:
        val = json.loads(m.group(0))
        if isinstance(val, list):
            return val
    except json.JSONDecodeError:
        pass
    return None


def _normalize_term(term) -> tuple | int:
    if isinstance(term, (list, tuple)):
        return tuple(int(x) for x in term)
    try:
        return int(term)
    except (TypeError, ValueError):
        return term


def score_accuracy(output: Optional[list], gt: list) -> Optional[float]:
    if output is None:
        return None
    n = len(gt)
    if n == 0:
        return None
    correct = sum(
        1 for i in range(n)
        if i < len(output) and _normalize_term(output[i]) == _normalize_term(gt[i])
    )
    return correct / n


# ---------------------------------------------------------------------------
# Automaton ID resolution
# ---------------------------------------------------------------------------

def resolve_ids(nerode_conn) -> dict:
    """Build slug→automaton_id map from corpus and product_pairs tables."""
    ids: dict[str, int] = {}
    for slug, aid in nerode_conn.execute(
        "SELECT slug, automaton_id FROM nerode.corpus"
    ).fetchall():
        ids[slug] = aid
    for lhs, rhs, pid in nerode_conn.execute(
        "SELECT lhs_slug, rhs_slug, product_id FROM nerode.product_pairs"
    ).fetchall():
        ids[(lhs, rhs)] = pid
    return ids


# ---------------------------------------------------------------------------
# Approach 1: raw_llm
# ---------------------------------------------------------------------------

def run_raw_llm(seq_name: str, spec: dict, provider, length: int) -> BenchResult:
    desc = spec["description"].format(length_minus_1=length - 1)
    system = (
        "You are a precise mathematician. "
        "Generate sequences exactly as requested. "
        "Respond with ONLY a JSON array — no prose, no markdown, no explanation."
    )
    user = f"Generate the complete sequence:\n\n{desc}"

    t0 = time.perf_counter()
    cr = provider.call(system, user, max_tokens=4096)
    wall_ms = (time.perf_counter() - t0) * 1000

    output = _parse_json_array(cr.text)
    return BenchResult(
        approach="raw_llm", seq_name=seq_name,
        output=output, error=None if output is not None else "PARSE_ERROR",
        input_tokens=cr.input_tokens, output_tokens=cr.output_tokens,
        turns=1, wall_ms=wall_ms,
        cost_usd=_cost(cr.input_tokens, cr.output_tokens),
        accuracy=None,
    )


# ---------------------------------------------------------------------------
# Approach 2: nerode_grounded
# ---------------------------------------------------------------------------

def run_nerode_grounded(
    seq_name: str, spec: dict, provider, nerode_conn, ids: dict, length: int
) -> BenchResult:
    if spec["nerode_mode"] is None:
        return BenchResult(
            approach="nerode_grounded", seq_name=seq_name,
            output=None, error="NOT_APPLICABLE",
            input_tokens=0, output_tokens=0, turns=0, wall_ms=0.0,
            cost_usd=0.0, accuracy=None,
        )

    # Resolve automaton IDs
    slug_list = spec["nerode_slugs"]
    if spec["nerode_mode"] == "parallel_run":
        auto_ids = [ids[s] for s in slug_list]
        id_descriptions = "\n".join(
            f"  {s}: automaton_id={ids[s]}" for s in slug_list
        )
        tool_name = "nerode_sequence"
        tool_desc = (
            "Run DFAs in parallel for `length` steps over the unary alphabet {a}. "
            "Returns a list of {step, state_vector, accept_vector} objects. "
            "`accept_vector` maps automaton_id (as string) to true/false. "
            "Step k = state after k symbols consumed (step 0 = initial state)."
        )
        tool_params = {
            "type": "object",
            "properties": {
                "automaton_ids": {"type": "array", "items": {"type": "integer"}},
                "length": {"type": "integer"},
            },
            "required": ["automaton_ids", "length"],
        }
        def executor(name, args):
            a_ids = list(args.get("automaton_ids", auto_ids))
            n = int(args.get("length", length))
            rows = nerode_conn.execute(
                "SELECT step, state_vector, accept_vector FROM nerode.parallel_run(%s, %s)",
                (a_ids, n),
            ).fetchall()
            return [
                {"step": r[0], "state_vector": r[1], "accept_vector": r[2]}
                for r in rows
            ]
        system_tool_info = (
            f"Available automata for `nerode_sequence`:\n{id_descriptions}\n\n"
            "Call the tool with all automaton_ids in a single call. "
            "From the accept_vector, extract the boolean for each automaton_id "
            "(convert true→1, false→0) to build the output tuples."
        )

    else:  # accepting_positions
        # slug_list is a list of one (lhs, rhs) pair
        pair = slug_list[0]
        product_id = ids[pair]
        tool_name = "nerode_sequence"
        tool_desc = (
            "Run a single DFA for `length` steps and return the sorted list of "
            "steps where it is in an accepting state."
        )
        tool_params = {
            "type": "object",
            "properties": {
                "automaton_id": {"type": "integer"},
                "length": {"type": "integer"},
            },
            "required": ["automaton_id", "length"],
        }
        def executor(name, args):
            aid = int(args.get("automaton_id", product_id))
            n   = int(args.get("length", length))
            row = nerode_conn.execute(
                "SELECT nerode.accepting_positions(%s, %s)", (aid, n)
            ).fetchone()
            return {"accepting_positions": row[0] or []}
        system_tool_info = (
            f"The DFA for 'divisible by 4 AND divisible by 9' has automaton_id={product_id}. "
            "Call nerode_sequence with that automaton_id and the desired length."
        )

    desc = spec["description"].format(length_minus_1=length - 1)
    system = (
        "You have access to `nerode_sequence`, a tool that runs deterministic finite automata "
        "and returns their state/accept sequences.\n\n"
        f"{system_tool_info}\n\n"
        "Call the tool once, then produce the final answer as a JSON array."
    )
    user = f"Use the nerode_sequence tool to compute:\n\n{desc}"

    client = _gemini_client()
    t0 = time.perf_counter()
    conv = _gemini_tool_conversation(
        client, system, user,
        tool_def={"name": tool_name, "description": tool_desc, "parameters": tool_params},
        executor=executor,
        max_turns=4,
    )
    wall_ms = (time.perf_counter() - t0) * 1000

    output = _parse_json_array(conv.final_text)
    return BenchResult(
        approach="nerode_grounded", seq_name=seq_name,
        output=output, error=None if output is not None else "PARSE_ERROR",
        input_tokens=conv.total_input_tokens, output_tokens=conv.total_output_tokens,
        turns=conv.turns, wall_ms=wall_ms,
        cost_usd=_cost(conv.total_input_tokens, conv.total_output_tokens),
        accuracy=None,
    )


# ---------------------------------------------------------------------------
# Approach 3: calx_grounded
# ---------------------------------------------------------------------------

def run_calx_grounded(
    seq_name: str, spec: dict, provider, calx_conn, length: int
) -> BenchResult:
    if not spec["calx_available"]:
        return BenchResult(
            approach="calx_grounded", seq_name=seq_name,
            output=None, error="NOT_APPLICABLE",
            input_tokens=0, output_tokens=0, turns=0, wall_ms=0.0,
            cost_usd=0.0, accuracy=None,
        )

    tool_def = {
        "name": "calx_lookup",
        "description": (
            "Look up arithmetic facts for a positive integer n (1..100000). "
            "Returns: n, is_prime, omega (distinct prime factors), big_omega (total prime factors), "
            "is_squarefree, prime_signature, tau (divisor count), sigma (divisor sum), "
            "arithmetic_derivative."
        ),
        "parameters": {
            "type": "object",
            "properties": {"n": {"type": "integer"}},
            "required": ["n"],
        },
    }

    def executor(name, args):
        n = int(args.get("n", 1))
        rows = calx_conn.execute(
            """
            SELECT i.n, i.is_prime, i.omega, i.big_omega, i.is_squarefree,
                   ps.signature, dc.tau, ds.sigma,
                   calx.arithmetic_derivative(i.n) AS derivative
            FROM calx.integers i
            LEFT JOIN calx.prime_signatures ps ON ps.n = i.n
            LEFT JOIN calx.divisor_count    dc ON dc.n = i.n
            LEFT JOIN calx.divisor_sum      ds ON ds.n = i.n
            WHERE i.n = %s
            """,
            (n,),
        ).fetchone()
        if rows is None:
            return {"error": f"n={n} not in calx database"}
        return {
            "n": rows[0], "is_prime": rows[1], "omega": rows[2],
            "big_omega": rows[3], "is_squarefree": rows[4],
            "signature": rows[5], "tau": rows[6], "sigma": rows[7],
            "arithmetic_derivative": rows[8],
        }

    desc = spec["description"].format(length_minus_1=length - 1)
    n_values = length - 1   # arith_deriv: D(1)..D(n_values)
    system = (
        "You have access to `calx_lookup(n)` which returns arithmetic facts "
        "for integer n including its arithmetic_derivative.\n\n"
        f"You need to compute {n_values} values. Call the tool for each integer "
        "and accumulate the results. When done, output a JSON array of the "
        "arithmetic derivatives in order."
    )
    user = f"Compute the following sequence using calx_lookup:\n\n{desc}"

    client = _gemini_client()
    t0 = time.perf_counter()
    conv = _gemini_tool_conversation(
        client, system, user,
        tool_def=tool_def,
        executor=executor,
        max_turns=n_values + 5,
    )
    wall_ms = (time.perf_counter() - t0) * 1000

    output = _parse_json_array(conv.final_text)
    return BenchResult(
        approach="calx_grounded", seq_name=seq_name,
        output=output, error=None if output is not None else "PARSE_ERROR",
        input_tokens=conv.total_input_tokens, output_tokens=conv.total_output_tokens,
        turns=conv.turns, wall_ms=wall_ms,
        cost_usd=_cost(conv.total_input_tokens, conv.total_output_tokens),
        accuracy=None,
    )


# ---------------------------------------------------------------------------
# Approach 4: llm_builds_dfa
# ---------------------------------------------------------------------------

def run_llm_builds_dfa(
    seq_name: str, spec: dict, provider, nerode_conn, ids: dict, length: int
) -> BenchResult:
    if spec["llm_build_mode"] is None:
        return BenchResult(
            approach="llm_builds_dfa", seq_name=seq_name,
            output=None, error="NOT_APPLICABLE",
            input_tokens=0, output_tokens=0, turns=0, wall_ms=0.0,
            cost_usd=0.0, accuracy=None,
        )

    # Turn 1: ask model to specify regex(es) for the desired language(s)
    desc = spec["description"].format(length_minus_1=length - 1)
    system1 = (
        "You are a formal automata expert. "
        "Describe the DFA(s) needed to solve this sequence task using regex notation "
        "over the unary alphabet {a}.\n\n"
        "A DFA that accepts strings of length divisible by k has regex: (a^k)* "
        "(e.g. for k=4: (aaaa)*).\n\n"
        "Respond with a JSON object with keys:\n"
        '  "regexes": list of regex strings (one per DFA needed)\n'
        '  "mode": "parallel_accept" (return accept bit per DFA per step) '
        'or "accepting_positions" (return steps where single DFA accepts)\n\n'
        "Do NOT add any other text."
    )
    user1 = (
        f"What DFAs (as regexes over {{a}}) do I need to solve:\n\n{desc}\n\n"
        "Respond ONLY with the JSON object."
    )

    t0 = time.perf_counter()
    cr1 = provider.call(system1, user1, max_tokens=256)
    in_tok = cr1.input_tokens
    out_tok = cr1.output_tokens

    # Parse model's DFA specification
    build_spec = _parse_build_spec(cr1.text, spec)
    regexes  = build_spec.get("regexes", spec["llm_build_regexes"] or [])
    mode     = build_spec.get("mode", spec["llm_build_mode"])

    # Build DFAs from the regexes
    db_result, build_error = _build_and_run_dfas(nerode_conn, regexes, mode, length)

    # Turn 2: give the model the DB result, ask for the final JSON array
    system2 = (
        "You are a precise mathematician. "
        "Below is the output from running the DFA(s) you specified. "
        "Convert it into the final answer JSON array — no prose, no markdown."
    )
    if build_error:
        user2 = (
            f"The DFA build failed: {build_error}\n\n"
            "Please produce the sequence by direct computation instead. "
            f"Task:\n\n{desc}"
        )
    else:
        user2 = (
            f"DFA execution result:\n{json.dumps(db_result, indent=2)}\n\n"
            f"Original task:\n{desc}\n\n"
            "Produce the final JSON array."
        )

    cr2 = provider.call(system2, user2, max_tokens=4096)
    wall_ms = (time.perf_counter() - t0) * 1000

    in_tok  += cr2.input_tokens
    out_tok += cr2.output_tokens

    output = _parse_json_array(cr2.text)
    return BenchResult(
        approach="llm_builds_dfa", seq_name=seq_name,
        output=output, error=None if output is not None else "PARSE_ERROR",
        input_tokens=in_tok, output_tokens=out_tok,
        turns=2, wall_ms=wall_ms,
        cost_usd=_cost(in_tok, out_tok),
        accuracy=None,
    )


def _parse_build_spec(text: str, spec: dict) -> dict:
    """Try to extract the JSON build spec from model text."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {}


def _prebuild_cache(nerode_conn, ids: dict) -> None:
    """
    Build (or verify) the persistent sequence cache for every SEQUENCES entry
    that carries a cache_key.  Idempotent — a second call returns instantly.

    Also demonstrates the NOTIFY callback: we LISTEN before each build so the
    notification arrives and we can print the round-trip time.
    """
    print("\n[cache] Pre-building sequence cache …", flush=True)

    for seq_name, spec in SEQUENCES.items():
        cache_key  = spec.get("cache_key")
        cache_mode = spec.get("cache_mode")
        nerode_mode = spec.get("nerode_mode")
        if not cache_key or not cache_mode or not nerode_mode:
            continue

        slug_list = spec["nerode_slugs"]
        if nerode_mode == "parallel_run":
            auto_ids = [ids[s] for s in slug_list]
        elif nerode_mode == "accepting_positions":
            auto_ids = [ids[slug_list[0]]]
        else:
            continue

        length = spec["length"]

        # Register LISTEN so we can observe the NOTIFY from build_sequence_cache.
        # We use a separate autocommit connection so LISTEN is always active.
        try:
            listen_conn = psycopg.connect(NERODE_DSN, autocommit=True)
            listen_conn.execute("LISTEN nerode_sequence_ready")
        except Exception:
            listen_conn = None

        t0 = time.perf_counter()
        cache_id = nerode_conn.execute(
            "SELECT nerode.build_sequence_cache(%s, %s, %s, %s)",
            (cache_key, auto_ids, length, cache_mode),
        ).fetchone()[0]
        nerode_conn.commit()
        build_ms = (time.perf_counter() - t0) * 1000

        notify_payload = None
        if listen_conn is not None:
            # Poll briefly for the notification (it may already be queued)
            try:
                for notify in listen_conn.notifies(timeout=0.5):
                    if notify.channel == "nerode_sequence_ready":
                        notify_payload = notify.payload
                        break
            except Exception:
                pass
            listen_conn.close()

        notify_s = f"  NOTIFY: {notify_payload}" if notify_payload else ""
        print(f"  {seq_name}: cache_id={cache_id}  ({build_ms:.1f} ms){notify_s}",
              flush=True)

    print("[cache] Done.\n", flush=True)


def _prebuild_calx_cache(nerode_conn, calx_conn) -> None:
    """
    Fetch D(1..50) from calx at startup and store in nerode.sequence_cache
    via the 'store' mode (no DFA walk needed).  Idempotent.

    This turns the expensive 50-turn calx_grounded conversation into a single
    indexed DB read — the same nerode_cached pattern used for DFA sequences.
    """
    print("\n[cache] Pre-building calx sequence cache …", flush=True)

    # Fast-path: already stored
    row = nerode_conn.execute(
        "SELECT id FROM nerode.sequence_cache WHERE seq_key = 'arith_deriv:50'"
    ).fetchone()
    if row:
        print(f"  arith_deriv: cache hit (id={row[0]})", flush=True)
        print("[cache] Done.\n", flush=True)
        return

    if calx_conn is None:
        print("  arith_deriv: skipped (calx DB unavailable)", flush=True)
        print("[cache] Done.\n", flush=True)
        return

    try:
        values = _load_arith_deriv(calx_conn, 50)   # [D(1), D(2), ..., D(50)]
    except Exception as e:
        print(f"  arith_deriv: calx fetch failed ({e})", flush=True)
        print("[cache] Done.\n", flush=True)
        return

    try:
        listen_conn = psycopg.connect(NERODE_DSN, autocommit=True)
        listen_conn.execute("LISTEN nerode_sequence_ready")
    except Exception:
        listen_conn = None

    t0 = time.perf_counter()
    cache_id = nerode_conn.execute(
        "SELECT nerode.build_sequence_cache("
        "%s, ARRAY[]::BIGINT[], %s, 'store', 'a', FALSE, %s::JSONB)",
        ("arith_deriv:50", 50, json.dumps(values)),
    ).fetchone()[0]
    nerode_conn.commit()
    build_ms = (time.perf_counter() - t0) * 1000

    notify_payload = None
    if listen_conn is not None:
        try:
            for notify in listen_conn.notifies(timeout=0.5):
                if notify.channel == "nerode_sequence_ready":
                    notify_payload = notify.payload
                    break
        except Exception:
            pass
        listen_conn.close()

    notify_s = f"  NOTIFY: {notify_payload}" if notify_payload else ""
    print(f"  arith_deriv: stored as cache_id={cache_id}  ({build_ms:.1f} ms){notify_s}",
          flush=True)
    print("[cache] Done.\n", flush=True)


# ---------------------------------------------------------------------------
# Approach 5: nerode_cached
# ---------------------------------------------------------------------------

def run_nerode_cached(
    seq_name: str, spec: dict, nerode_conn, length: int
) -> BenchResult:
    """
    Pure DB cache read — no LLM, no compute.

    Reads the pre-built result from nerode.sequence_cache via a single indexed
    lookup.  Demonstrates the 'pre-cached memory' pattern: once the sequence
    is computed and stored, stateless callers pay only for one DB round-trip.
    """
    cache_key  = spec.get("cache_key")
    cache_mode = spec.get("cache_mode")

    if not cache_key or not cache_mode:
        return BenchResult(
            approach="nerode_cached", seq_name=seq_name,
            output=None, error="NOT_APPLICABLE",
            input_tokens=0, output_tokens=0, turns=0, wall_ms=0.0,
            cost_usd=0.0, accuracy=None,
        )

    t0 = time.perf_counter()
    row = nerode_conn.execute(
        "SELECT nerode.query_sequence_cache(%s)", (cache_key,)
    ).fetchone()
    wall_ms = (time.perf_counter() - t0) * 1000

    if row is None or row[0] is None:
        return BenchResult(
            approach="nerode_cached", seq_name=seq_name,
            output=None, error="CACHE_MISS",
            input_tokens=0, output_tokens=0, turns=0, wall_ms=wall_ms,
            cost_usd=0.0, accuracy=None,
        )

    cached = row[0]  # Python list/dict decoded from JSONB by psycopg

    if cache_mode == "parallel_accept":
        # Stored as [{step: N, accepts: [0|1, ...]}, ...]
        output = [
            entry["accepts"]
            for entry in sorted(cached, key=lambda e: e["step"])
        ]
    elif cache_mode == "accepting_positions":
        # Stored directly as [int, int, ...]
        output = list(cached)
    elif cache_mode == "store":
        # Pre-computed result stored verbatim (e.g. calx-sourced sequences).
        output = list(cached)
    else:
        output = cached  # fallback — return as-is

    return BenchResult(
        approach="nerode_cached", seq_name=seq_name,
        output=output, error=None,
        input_tokens=0, output_tokens=0, turns=0, wall_ms=wall_ms,
        cost_usd=0.0, accuracy=None,
    )


def _build_and_run_dfas(nerode_conn, regexes: list[str], mode: str, length: int):
    """Build DFAs from regexes and run them. Returns (result_dict, error_str)."""
    auto_ids = []
    try:
        for regex in regexes:
            aid = nerode_conn.execute(
                "SELECT nerode.from_regex(%s)", (regex,)
            ).fetchone()[0]
            nerode_conn.commit()
            auto_ids.append(aid)
    except Exception as e:
        nerode_conn.rollback()
        return None, f"from_regex failed: {e}"

    try:
        if mode == "parallel_accept" and len(auto_ids) > 0:
            rows = nerode_conn.execute(
                "SELECT step, accept_vector FROM nerode.parallel_run(%s, %s)",
                (auto_ids, length),
            ).fetchall()
            result = [
                {
                    "step": r[0],
                    "accepts": [
                        int(r[1].get(str(aid), False)) for aid in auto_ids
                    ],
                }
                for r in rows
            ]
            return result, None

        elif mode == "accepting_positions" and len(auto_ids) == 1:
            if len(auto_ids) == 2:
                # Build product
                pid = nerode_conn.execute(
                    "SELECT nerode.product(%s,%s,'intersection')", tuple(auto_ids)
                ).fetchone()[0]
                nerode_conn.commit()
                target_id = pid
            else:
                target_id = auto_ids[0]
            pos = nerode_conn.execute(
                "SELECT nerode.accepting_positions(%s, %s)", (target_id, length)
            ).fetchone()[0]
            return {"accepting_positions": pos or []}, None

        else:
            return None, f"unsupported mode {mode!r} for {len(auto_ids)} DFAs"

    except Exception as e:
        nerode_conn.rollback()
        return None, f"run failed: {e}"


# ---------------------------------------------------------------------------
# Gemini provider — self-contained, no dependency on llm_eval.py
# ---------------------------------------------------------------------------

@dataclass
class CallResult:
    text:          str
    ttft_ms:       float
    total_ms:      float
    input_tokens:  int
    output_tokens: int


def _api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError(
            "No API key found. Set GEMINI_API_KEY or GOOGLE_API_KEY in your environment."
        )
    return key


def _gemini_client():
    from google import genai
    return genai.Client(api_key=_api_key())


class GeminiProvider:
    """Minimal single-turn Gemini provider (no thinking)."""

    def __init__(self):
        from google import genai
        self._client = genai.Client(api_key=_api_key())

    def call(self, system: str, user: str, max_tokens: int = 4096) -> CallResult:
        from google.genai import types
        ttft_ms: float | None = None
        chunks: list[str] = []
        last_chunk = None
        t0 = time.perf_counter()

        for chunk in self._client.models.generate_content_stream(
            model=GEMINI_MODEL,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=max_tokens,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        ):
            text = chunk.text or ""
            if ttft_ms is None and text:
                ttft_ms = (time.perf_counter() - t0) * 1000
            if text:
                chunks.append(text)
            last_chunk = chunk

        total_ms = (time.perf_counter() - t0) * 1000
        usage = last_chunk.usage_metadata if last_chunk else None
        return CallResult(
            text="".join(chunks),
            ttft_ms=ttft_ms or total_ms,
            total_ms=total_ms,
            input_tokens=usage.prompt_token_count if usage else 0,
            output_tokens=usage.candidates_token_count if usage else 0,
        )


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

_last_call_time: float = 0.0
_MIN_GAP_S: float = 4.5   # ~13 RPM, well under 15 RPM limit


def _rate_limited_call(fn):
    global _last_call_time
    now = time.perf_counter()
    gap = now - _last_call_time
    if gap < _MIN_GAP_S:
        time.sleep(_MIN_GAP_S - gap)
    result = fn()
    _last_call_time = time.perf_counter()
    return result


class RateLimitedProvider:
    def __init__(self, inner: GeminiProvider):
        self._inner = inner

    def call(self, system, user, max_tokens=4096):
        return _rate_limited_call(lambda: self._inner.call(system, user, max_tokens))


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(all_results: dict[str, list[BenchResult]]) -> None:
    SEP = "=" * 82

    print()
    print(SEP)
    print(f"  Sequence Generation Benchmark  —  model: {GEMINI_MODEL}")
    print(SEP)

    approach_order = [
        "raw_llm", "nerode_grounded", "calx_grounded",
        "llm_builds_dfa", "nerode_cached",
    ]
    approach_label = {
        "raw_llm":         "raw_llm        ",
        "nerode_grounded": "nerode_grounded",
        "calx_grounded":   "calx_grounded  ",
        "llm_builds_dfa":  "llm_builds_dfa ",
        "nerode_cached":   "nerode_cached  ",
    }

    summary: dict[str, list[BenchResult]] = {a: [] for a in approach_order}

    for seq_name, results in all_results.items():
        spec = SEQUENCES[seq_name]
        length = spec["length"]
        print()
        print(f"  Sequence: {seq_name}  (length={length-1})")
        print(f"  {spec['description'].split(chr(10))[0].format(length_minus_1=length-1)}")
        print()
        print(f"  {'Approach':<18} {'Accuracy':>9} {'In-tok':>8} {'Out-tok':>9} "
              f"{'Turns':>6} {'Cost($)':>10} {'ms':>8}")
        print(f"  {'-'*18} {'-'*9} {'-'*8} {'-'*9} {'-'*6} {'-'*10} {'-'*8}")

        by_approach = {r.approach: r for r in results}
        for ap in approach_order:
            r = by_approach.get(ap)
            if r is None:
                continue
            if r.is_na():
                print(f"  {approach_label[ap]} {'N/A':>9} {'—':>8} {'—':>9} "
                      f"{'—':>6} {'—':>10} {'—':>8}")
            elif ap == "nerode_cached":
                # DB-only: no LLM tokens. Render token/cost cols as "—" to
                # distinguish from LLM approaches; keep accuracy and ms.
                acc_s = f"{r.accuracy*100:>7.1f}%" if r.accuracy is not None else "  ?.?%"
                print(f"  {approach_label[ap]} {acc_s} {'—':>8} {'—':>9} "
                      f"{'DB':>6} {'—':>10} {r.wall_ms:>8.0f}  [cache hit]")
                summary[ap].append(r)
            else:
                acc_s = f"{r.accuracy*100:>7.1f}%" if r.accuracy is not None else "  ?.?%"
                err_s = f" [{r.error}]" if r.error else ""
                print(f"  {approach_label[ap]} {acc_s} {r.input_tokens:>8,} "
                      f"{r.output_tokens:>9,} {r.turns:>6} "
                      f"${r.cost_usd:>9.5f} {r.wall_ms:>8.0f}")
                if err_s:
                    print(f"    {err_s}")
                summary[ap].append(r)

    # Summary
    print()
    print(SEP)
    print("  Summary across all sequences")
    print(SEP)
    print()
    print(f"  {'Approach':<18} {'Avg acc':>9} {'Total in':>10} {'Total out':>10} "
          f"{'Total $':>10} {'Avg turns':>10}")
    print(f"  {'-'*18} {'-'*9} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")

    for ap in approach_order:
        rl = [r for r in summary[ap] if not r.is_na()]
        if not rl:
            print(f"  {approach_label[ap]} {'N/A':>9} {'—':>10} {'—':>10} {'—':>10} {'—':>10}")
            continue
        acc_vals = [r.accuracy for r in rl if r.accuracy is not None]
        avg_acc = sum(acc_vals) / len(acc_vals) if acc_vals else None
        acc_s = f"{avg_acc*100:.1f}%" if avg_acc is not None else "—"
        if ap == "nerode_cached":
            # Token/cost meaningless (0): show DB-only note instead
            print(f"  {approach_label[ap]} {acc_s:>9} {'—':>10} {'—':>10} "
                  f"{'—':>10} {'DB-only':>10}  [no LLM tokens]")
        else:
            tot_in  = sum(r.input_tokens  for r in rl)
            tot_out = sum(r.output_tokens for r in rl)
            tot_c   = sum(r.cost_usd      for r in rl)
            avg_t   = sum(r.turns         for r in rl) / len(rl)
            print(f"  {approach_label[ap]} {acc_s:>9} {tot_in:>10,} {tot_out:>10,} "
                  f"${tot_c:>9.5f} {avg_t:>10.1f}")

    print()
    print(SEP)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        prog="sequence_bench",
        description="Token-cost benchmark: five approaches to sequence generation.",
    )
    ap.add_argument(
        "--seq", choices=list(SEQUENCES), default=None,
        help="Run only this sequence (default: all).",
    )
    ap.add_argument(
        "--approach",
        choices=["raw_llm", "nerode_grounded", "calx_grounded",
                 "llm_builds_dfa", "nerode_cached"],
        default=None, help="Run only this approach.",
    )
    ap.add_argument(
        "--skip-llm", action="store_true",
        help="Skip all LLM API calls (shows N/A for every LLM-dependent approach).",
    )
    ap.add_argument(
        "--probe-db", action="store_true",
        help="Run only the DB-side queries (no LLM) and print ground truth + raw DB output.",
    )
    ap.add_argument(
        "--length", type=int, default=None,
        help="Override sequence length (default: per-sequence setting).",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Print ground truths and exit without calling the LLM.",
    )
    args = ap.parse_args()

    # DB connections
    nerode_conn = psycopg.connect(NERODE_DSN, autocommit=False)
    calx_conn   = None
    try:
        calx_conn = psycopg.connect(CALX_DSN, autocommit=True)
    except Exception as e:
        print(f"[warn] calx DB unavailable ({e}); arith_deriv calx approach skipped.")

    ids = resolve_ids(nerode_conn)

    # Provider (skip if --skip-llm or --probe-db or --dry-run)
    provider = None
    if not args.skip_llm and not args.dry_run and not args.probe_db:
        try:
            provider = RateLimitedProvider(GeminiProvider())
            print(f"[ok] Gemini provider ready (key={_api_key()[:8]}...)", flush=True)
        except Exception as e:
            print(f"[warn] Could not create Gemini provider: {e}\n"
                  f"       Set GEMINI_API_KEY or GOOGLE_API_KEY. Falling back to --skip-llm.")
            args.skip_llm = True

    seqs_to_run = [args.seq] if args.seq else list(SEQUENCES)
    approaches_to_run = [args.approach] if args.approach else [
        "raw_llm", "nerode_grounded", "calx_grounded",
        "llm_builds_dfa", "nerode_cached",
    ]

    # Pre-build cache for sequences that support nerode_cached.
    # build_sequence_cache is idempotent — instant on second run.
    if "nerode_cached" in approaches_to_run and not args.dry_run and not args.probe_db:
        _prebuild_cache(nerode_conn, ids)
        _prebuild_calx_cache(nerode_conn, calx_conn)

    all_results: dict[str, list[BenchResult]] = {}

    for seq_name in seqs_to_run:
        spec = SEQUENCES[seq_name]
        length = args.length or spec["length"]

        print(f"\n>>> {seq_name}  (length={length})", flush=True)

        # Ground truth
        gt_calx = calx_conn   # may be None
        gt = spec["ground_truth"](ids, gt_calx)
        if args.dry_run:
            print(f"    Ground truth ({len(gt)} terms): {gt[:5]} ...")
            continue

        if args.probe_db:
            # Run DB queries directly and show raw results — no LLM
            print(f"    Ground truth ({len(gt)} terms): {gt[:6]} ...")
            if spec["nerode_mode"] == "parallel_run":
                auto_ids = [ids[s] for s in spec["nerode_slugs"]]
                rows = nerode_conn.execute(
                    "SELECT step, accept_vector FROM nerode.parallel_run(%s, %s)",
                    (auto_ids, min(length, 12)),
                ).fetchall()
                print(f"    parallel_run first 12 steps:")
                for step, av in rows:
                    flags = tuple(int(av.get(str(aid), False)) for aid in auto_ids)
                    print(f"      step {step:2d}: {flags}")
            elif spec["nerode_mode"] == "accepting_positions":
                pair = spec["nerode_slugs"][0]
                product_id = ids[pair]
                pos = nerode_conn.execute(
                    "SELECT nerode.accepting_positions(%s, %s)", (product_id, length)
                ).fetchone()[0]
                print(f"    accepting_positions({length}): {pos}")
            if spec["calx_available"] and calx_conn:
                sample = _load_arith_deriv(calx_conn, 10)
                print(f"    calx arith_deriv D(1..10): {sample}")
            continue

        results: list[BenchResult] = []

        def _run(approach_name: str, fn, requires_llm: bool = True):
            if approach_name not in approaches_to_run:
                return
            # LLM-dependent approaches are skipped in --skip-llm mode;
            # DB-only approaches (nerode_cached) always run.
            if args.skip_llm and requires_llm:
                r = BenchResult(
                    approach=approach_name, seq_name=seq_name,
                    output=None, error="SKIPPED",
                    input_tokens=0, output_tokens=0, turns=0,
                    wall_ms=0.0, cost_usd=0.0, accuracy=None,
                )
                results.append(r)
                return
            print(f"    [{approach_name}] ...", end=" ", flush=True)
            try:
                r = fn()
            except Exception as exc:
                r = BenchResult(
                    approach=approach_name, seq_name=seq_name,
                    output=None, error=str(exc)[:80],
                    input_tokens=0, output_tokens=0, turns=0,
                    wall_ms=0.0, cost_usd=0.0, accuracy=None,
                )
            r.accuracy = score_accuracy(r.output, gt)
            acc_s = f"{r.accuracy*100:.1f}%" if r.accuracy is not None else r.error
            print(f"done — {acc_s}", flush=True)
            results.append(r)

        _run("raw_llm",
             lambda: run_raw_llm(seq_name, spec, provider, length))
        _run("nerode_grounded",
             lambda: run_nerode_grounded(seq_name, spec, provider, nerode_conn, ids, length))
        _run("calx_grounded",
             lambda: run_calx_grounded(seq_name, spec, provider, calx_conn, length)
             if calx_conn else BenchResult(
                 approach="calx_grounded", seq_name=seq_name, output=None,
                 error="NOT_APPLICABLE", input_tokens=0, output_tokens=0,
                 turns=0, wall_ms=0.0, cost_usd=0.0, accuracy=None))
        _run("llm_builds_dfa",
             lambda: run_llm_builds_dfa(seq_name, spec, provider, nerode_conn, ids, length))
        _run("nerode_cached",
             lambda: run_nerode_cached(seq_name, spec, nerode_conn, length),
             requires_llm=False)

        all_results[seq_name] = results

    if not args.dry_run:
        print_report(all_results)

    nerode_conn.close()
    if calx_conn:
        calx_conn.close()


if __name__ == "__main__":
    main()
