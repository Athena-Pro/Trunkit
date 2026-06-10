"""
tools/agent_demo.py
===================
Live demonstration of the Nerode stream interceptor wired to a Gemini agent.

The agent is asked to construct a minimal DFA for a given language. When it
calls the ``nerode_query`` tool, the interceptor:

  1. Builds the certified minimal DFA via nerode.from_regex().
  2. Issues a cert.claim + cert.witness (construction_record).
  3. Gathers calx arithmetic facts (live from Trunkit when --trunkit is set).
  4. Returns the transition table + Nerode partition as the tool result.

The agent then continues its response using ground-truth structure rather
than hallucinated state counts, transitions, or partition classes.

Usage::

    python tools/agent_demo.py [--trunkit] [--language LANG]

``--language`` accepts one of: astar_bplus, ab_star, anbn, email_local

The demo also shows what the model would have said *without* the tool, so
the grounding benefit is directly visible.
"""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path
from typing import Any

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
src_dir_str = str(SRC_DIR)
if src_dir_str not in sys.path:
    sys.path.insert(0, src_dir_str)

# ---------------------------------------------------------------------------
# Language menu
# ---------------------------------------------------------------------------

LANGUAGES: dict[str, dict[str, str]] = {
    "astar_bplus": {
        "regex":       "a*b+",
        "description": "any number of a's (including zero) followed by one or more b's",
        "question":    "Construct the minimal DFA for the language a*b+ over the alphabet {a, b}.",
    },
    "ab_star": {
        "regex":       "(ab)*",
        "description": "any number of repetitions of the pair 'ab' (including the empty string)",
        "question":    "Construct the minimal DFA for the language (ab)* over the alphabet {a, b}.",
    },
    "anbn": {
        "regex":       None,   # not regular; demo shows the tool rejecting it
        "description": "strings of the form a^n b^n — equal numbers of a's then b's",
        "question":    (
            "Construct the minimal DFA for the language {a^n b^n | n >= 1} "
            "over the alphabet {a, b}."
        ),
    },
    "aplus_or_bplus": {
        "regex":       "a+|b+",
        "description": "one or more a's, OR one or more b's",
        "question":    "Construct the minimal DFA for the language a+|b+ over the alphabet {a, b}.",
    },
    "ends_ab": {
        "regex":       "(a|b)*ab",
        "description": "all strings over {a,b} that end with 'ab'",
        "question":    "Construct the minimal DFA for strings over {a, b} that end with 'ab'.",
    },
}

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM = textwrap.dedent("""\
    You are a formal language theory expert.

    When you need to construct or verify a DFA, call the nerode_query tool
    with the regex pattern for the language. The tool returns:
      - the certified minimal DFA (state count, transition table)
      - the Myhill-Nerode equivalence classes (the partition)
      - arithmetic facts about the state count (primality, factorization)

    Use the tool result as ground truth. Describe the DFA clearly:
    state count, accepting states, transition function, and what the
    Nerode partition tells us about the language's complexity.

    If the language is not regular, explain why no DFA exists.
""")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gemini_client():
    import google.genai as genai
    key = os.environ.get("GOOGLE_API_KEY")
    if not key:
        sys.exit("GOOGLE_API_KEY not set.")
    return genai.Client(api_key=key)


def _nerode_tool():
    from google.genai import types
    return types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="nerode_query",
                description=(
                    "Build and certify the minimal DFA for a regular language "
                    "given as a regex pattern. Returns the transition table, "
                    "Myhill-Nerode partition, and arithmetic facts about the state count."
                ),
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "spec": types.Schema(
                            type="STRING",
                            description=(
                                "Regular expression. Supported: literals a-z A-Z 0-9 _, "
                                "| (union), * (Kleene star), + (one-or-more), "
                                "? (optional), () grouping."
                            ),
                        )
                    },
                    required=["spec"],
                ),
            )
        ]
    )


def _render_tool_result(result: dict[str, Any]) -> str:
    """Summarise a handle_nerode_query result as readable text for the model."""
    lines = [
        f"Certified minimal DFA — automaton_id={result['automaton_id']}",
        f"State count : {result['state_count']}",
        f"Cert claim  : {result['cert_claim_id']}",
    ]
    cf = result.get("calx_facts", {})
    inner = cf.get("calx_facts", {})
    if inner:
        lines.append(
            f"Arithmetic  : |Q|={result['state_count']}  "
            f"signature={inner.get('signature')}  "
            f"is_prime={inner.get('is_prime')}  "
            f"derivative={inner.get('derivative')}"
        )
    lines.append("")
    lines.append("Transition table:")
    lines.append(result["transition_table"])
    part = result.get("nerode_partition")
    if part:
        lines.append("Myhill-Nerode partition (equivalence classes → state IDs):")
        for cls, members in sorted(part.items(), key=lambda x: int(x[0])):
            lines.append(f"  class {cls}: {members}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Grounded run (with tool)
# ---------------------------------------------------------------------------


def run_grounded(lang: dict, dsn: str | None, verbose: bool) -> str:
    """Run a full Gemini turn with the nerode_query tool active."""
    import importlib.util
    import pathlib

    from google.genai import types
    _si = importlib.util.spec_from_file_location(
        "stream_interceptor",
        pathlib.Path(__file__).parent / "stream_interceptor.py"
    )
    _mod = importlib.util.module_from_spec(_si)
    _si.loader.exec_module(_mod)
    handle_nerode_query = _mod.handle_nerode_query

    client = _gemini_client()
    tool   = _nerode_tool()
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM,
        tools=[tool],
        thinking_config=types.ThinkingConfig(thinking_budget=0),
        max_output_tokens=2048,
    )

    contents = [types.Content(role="user", parts=[types.Part(text=lang["question"])])]

    # Multi-turn: keep going until no more function calls
    for _turn in range(4):
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config=config,
        )

        # Collect function calls from this turn
        fn_calls = [
            p.function_call
            for cand in resp.candidates
            for p in cand.content.parts
            if hasattr(p, "function_call") and p.function_call
        ]

        if not fn_calls:
            # No more tool calls — collect final text
            text_parts = [
                p.text
                for cand in resp.candidates
                for p in cand.content.parts
                if hasattr(p, "text") and p.text
            ]
            return "\n".join(text_parts)

        # Append model turn to history
        contents.append(resp.candidates[0].content)

        # Service each function call
        tool_parts = []
        for fc in fn_calls:
            spec = (fc.args or {}).get("spec", "")
            if verbose:
                print(f"  [tool call] nerode_query(spec={spec!r})", flush=True)
            try:
                result   = handle_nerode_query(spec, dsn=dsn)
                rendered = _render_tool_result(result)
                if verbose:
                    print(f"  [tool result] {result['state_count']} states, "
                          f"cert_claim={result['cert_claim_id']}", flush=True)
            except Exception as exc:  # noqa: BLE001
                rendered = f"nerode_query error: {exc}"
                if verbose:
                    print(f"  [tool error] {exc}", flush=True)

            tool_parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        name="nerode_query",
                        response={"result": rendered},
                    )
                )
            )

        contents.append(types.Content(role="user", parts=tool_parts))

    return "(max turns reached)"


# ---------------------------------------------------------------------------
# Ungrounded run (no tool — baseline)
# ---------------------------------------------------------------------------


def run_ungrounded(lang: dict) -> str:
    """Ask Gemini directly without any tool, to show the baseline."""
    from google.genai import types

    client = _gemini_client()
    config = types.GenerateContentConfig(
        system_instruction=(
            "You are a formal language theory expert. "
            "Give a precise, detailed answer."
        ),
        thinking_config=types.ThinkingConfig(thinking_budget=0),
        max_output_tokens=1024,
    )
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[lang["question"]],
        config=config,
    )
    parts = [
        p.text
        for cand in resp.candidates
        for p in cand.content.parts
        if hasattr(p, "text") and p.text
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(
        prog="agent_demo",
        description="Live Nerode stream-interceptor demo wired to Gemini.",
    )
    p.add_argument(
        "--language", default="astar_bplus",
        choices=list(LANGUAGES),
        help="Language to demonstrate (default: astar_bplus).",
    )
    p.add_argument(
        "--trunkit", action="store_true",
        help="Use the Trunkit co-deployment DB (live calx facts).",
    )
    p.add_argument(
        "--no-baseline", action="store_true",
        help="Skip the ungrounded baseline run.",
    )
    p.add_argument(
        "--quiet", action="store_true",
        help="Suppress interceptor progress messages.",
    )
    args = p.parse_args()

    from nerode.db import TRUNKIT_DSN
    dsn = TRUNKIT_DSN if args.trunkit else None

    lang = LANGUAGES[args.language]

    print("=" * 70)
    print(f"NERODE AGENT DEMO  —  language: {args.language}")
    print(f"  {lang['description']}")
    print(f"  DB: {'Trunkit (port 5434, live calx)' if args.trunkit else 'standalone (port 5435)'}")
    print("=" * 70)

    # ---- Grounded run -------------------------------------------------------
    print("\n[GROUNDED — nerode_query tool active]\n")
    grounded = run_grounded(lang, dsn, verbose=not args.quiet)
    print(grounded)

    # ---- Baseline run -------------------------------------------------------
    if not args.no_baseline:
        print("\n" + "=" * 70)
        print("[BASELINE — no tool, Gemini alone]\n")
        baseline = run_ungrounded(lang)
        print(baseline)

    print("\n" + "=" * 70)
    print("Demo complete.")


if __name__ == "__main__":
    main()
