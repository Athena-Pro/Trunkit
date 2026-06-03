"""
benchmarks/llm_eval.py
=======================
LLM accuracy / speed / token-usage evaluation harness.

Ground truth: the Chomsky simulators (chomsky.run_dfa/pda/lba/tm) are the
oracle.  Each test case is generated from the DB, so the correct answer is
always known before the model is asked.

Three task modes
----------------
  membership_s  -- Structured: "Answer only yes or no."
  membership_c  -- Chain-of-thought: reason freely, end with "ANSWER: yes/no"
  classify      -- Multi-label: which of the 4 machines accept this input?
                   Model returns JSON; scored per-machine and overall.

Metrics captured per call
--------------------------
  input_tokens   from the API usage field (exact)
  output_tokens  from the API usage field (exact)
  think_tokens   reasoning/thinking tokens (Gemini only; 0 for Anthropic)
  ttft_ms        wall time to first streaming token
  total_ms       wall time to stream completion

Providers
---------
  anthropic  -- Claude models (requires ANTHROPIC_API_KEY)
  gemini     -- Gemini models via google-genai (requires GEMINI_API_KEY)

Results stored in curry.llm_eval_run / curry.llm_eval_result (created on
first run; safe to re-run against a populated table).

Usage
-----
  python benchmarks/llm_eval.py                          # gemini-2.5-flash default
  python benchmarks/llm_eval.py --provider anthropic --model claude-haiku-4-5-20251001
  python benchmarks/llm_eval.py --tasks membership_s,membership_c
  python benchmarks/llm_eval.py --report-only            # print summary of last run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import time
import uuid
from dataclasses import dataclass
from typing import Optional, Protocol

import psycopg

from nerode.db import apply_schema, resolve_dsn

# ---------------------------------------------------------------------------
# Eval schema — created once, idempotent
# ---------------------------------------------------------------------------

_EVAL_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS curry.llm_eval_run (
    run_id      TEXT PRIMARY KEY,
    model       TEXT NOT NULL,
    tasks       TEXT NOT NULL,
    corpus_n    INTEGER NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS curry.llm_eval_result (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES curry.llm_eval_run(run_id) ON DELETE CASCADE,
    task          TEXT NOT NULL,        -- membership_s | membership_c | classify
    machine       TEXT NOT NULL,        -- dfa | pda | lba | tm
    input_str     TEXT NOT NULL,
    ground_truth  JSONB NOT NULL,       -- {"accept": true/false}  or  {"dfa":T,"pda":F,...}
    llm_raw       TEXT,                 -- verbatim model output
    llm_parsed    JSONB,                -- structured interpretation
    correct       BOOLEAN,             -- NULL if parse failed
    input_tokens  INTEGER,
    output_tokens INTEGER,
    ttft_ms       FLOAT,
    total_ms      FLOAT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_llm_eval_result_run
    ON curry.llm_eval_result (run_id, task, machine);
"""


# ---------------------------------------------------------------------------
# Corpus — ground truth from the DB simulators
# ---------------------------------------------------------------------------

@dataclass
class Case:
    machine:      str
    input_str:    str
    ground_truth: dict          # {"accept": bool}  OR  {"dfa": bool, ...}
    language:     str
    lang_desc:    str


def generate_corpus(conn: psycopg.Connection) -> list[Case]:
    """
    Build labeled test cases by running each simulator.
    Balanced accept/reject across all four machines and several input lengths.
    """
    corpus: list[Case] = []

    def _check_dfa(s): return conn.execute(
        "SELECT accept FROM chomsky.run_dfa(%s, FALSE)", (s,)).fetchone()[0]
    def _check_pda(s): return conn.execute(
        "SELECT accept FROM chomsky.run_pda(%s, FALSE)", (s,)).fetchone()[0]
    def _check_lba(s): return conn.execute(
        "SELECT accept FROM chomsky.run_lba(%s, FALSE)", (s,)).fetchone()[0]
    def _check_tm(s): return conn.execute(
        "SELECT accept FROM chomsky.run_tm(%s, 64, FALSE)", (s,)).fetchone()[0]

    # -- DFA: a*b* ----------------------------------------------------------
    dfa_inputs = [
        "", "a", "b", "ab", "aab", "abb", "aaabbb", "a"*10+"b"*5,
        "ba", "aba", "bba", "c", "abc", "cab", "a"*5+"b"*5+"a",
    ]
    for s in dfa_inputs:
        corpus.append(Case("dfa", s, {"accept": _check_dfa(s)},
                           "a*b*",
                           "any number of a's (including zero) followed by any number of b's"))

    # -- PDA: a^n b^n -------------------------------------------------------
    pda_inputs = [
        "ab", "aabb", "aaabbb", "a"*5+"b"*5, "a"*10+"b"*10,
        "", "a", "b", "aab", "abb", "ba", "aaabbbccc", "abab",
    ]
    for s in pda_inputs:
        corpus.append(Case("pda", s, {"accept": _check_pda(s)},
                           "a^n b^n",
                           "exactly n a's followed by exactly n b's, for some n >= 1"))

    # -- LBA: a^n b^n c^n ---------------------------------------------------
    lba_inputs = [
        "abc", "aabbcc", "aaabbbccc", "a"*4+"b"*4+"c"*4,
        "", "a", "ab", "aabb", "aabbc", "abcabc", "abcc", "aabc",
    ]
    for s in lba_inputs:
        corpus.append(Case("lba", s, {"accept": _check_lba(s)},
                           "a^n b^n c^n",
                           "exactly n a's, then n b's, then n c's, for some n >= 1"))

    # -- TM: 0^(2^k) --------------------------------------------------------
    tm_inputs = [
        "0", "00", "0000", "0"*8, "0"*16,
        "", "000", "00000", "0"*6, "0"*10, "1", "010", "0"*3,
    ]
    for s in tm_inputs:
        corpus.append(Case("tm", s, {"accept": _check_tm(s)},
                           "0^(2^k)",
                           "exactly 2^k zeros for some k >= 0 — lengths 1, 2, 4, 8, 16, ..."))

    # -- Classify cases (all four machines, know all four answers) ----------
    classify_inputs = [
        "", "a", "aabb", "abc", "aabbcc", "aaabbbccc",
        "0", "0000", "0"*8, "ba", "aab", "abcabc",
    ]
    for s in classify_inputs:
        rows = conn.execute(
            "SELECT machine, accept FROM chomsky.classify(%s)", (s,)
        ).fetchall()
        gt = {r[0]: r[1] for r in rows}
        corpus.append(Case("all", s, gt,
                           "classify",
                           "four machines: DFA (a*b*), PDA (a^n b^n), LBA (a^n b^n c^n), TM (0^(2^k))"))

    return corpus


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_MEMBERSHIP_S_SYSTEM = (
    "You are a formal language theory expert. "
    "Answer the user's question with exactly one word: yes or no. "
    "Do not explain. Do not add punctuation."
)

_MEMBERSHIP_C_SYSTEM = (
    "You are a formal language theory expert. "
    "Think step by step about whether the input string belongs to the language. "
    "After your reasoning, write your final answer on its own line as exactly: "
    "ANSWER: yes  or  ANSWER: no"
)

_CLASSIFY_SYSTEM = (
    "You are a formal language theory expert. "
    "Evaluate each of four machines INDEPENDENTLY against the given input string.\n\n"
    "ALPHABET RULE (apply first, before any other check):\n"
    "  Before checking the language structure, verify the string's symbols.\n"
    "  If the string contains ANY symbol not in the machine's alphabet => result is FALSE.\n"
    "  dfa alphabet: {a, b}  — 'c' or '0' or any other character means dfa=FALSE immediately.\n"
    "  pda alphabet: {a, b}  — same: any 'c' or non-{a,b} character means pda=FALSE immediately.\n"
    "  lba alphabet: {a,b,c} — a '0' or any non-{a,b,c} character means lba=FALSE immediately.\n"
    "  tm  alphabet: {0}     — any non-'0' character means tm=FALSE immediately.\n\n"
    "LANGUAGE RULE (apply only after alphabet check passes):\n"
    "  dfa: a*b*       — the whole string matches zero-or-more a's then zero-or-more b's.\n"
    "  pda: a^n b^n    — exactly n a's then exactly n b's, n >= 1.\n"
    "  lba: a^n b^n c^n — exactly n a's, n b's, n c's, n >= 1.\n"
    "  tm:  0^(2^k)    — exactly 2^k zeros (k >= 0): lengths 1, 2, 4, 8, 16, ...\n\n"
    "WORKED EXAMPLES:\n"
    "  'aabb'     -> dfa=true  (a*b* over {a,b}), pda=true  (n=2), lba=false (no c), tm=false\n"
    "  'abc'      -> dfa=false (c not in {a,b}!), pda=false (c not in {a,b}!),\n"
    "               lba=true  (n=1), tm=false\n"
    "  'aabbcc'   -> dfa=false (c not in {a,b}!), pda=false (c not in {a,b}!),\n"
    "               lba=true  (n=2), tm=false\n"
    "  '00000000' -> dfa=false (0 not in {a,b}!), pda=false, lba=false, tm=true (2^3=8)\n"
    "  'ab'       -> dfa=true, pda=true (n=1), lba=false, tm=false\n"
    "  ''         -> dfa=true (zero a's, zero b's), pda=false (n>=1 required), lba=false, tm=false\n\n"
    "Respond with ONLY a JSON object, no other text:\n"
    '{"dfa": true_or_false, "pda": true_or_false, "lba": true_or_false, "tm": true_or_false}'
)


def _membership_prompt(case: Case) -> str:
    s = repr(case.input_str) if case.input_str else '""  (empty string)'
    return (
        f"Language: {case.language} — {case.lang_desc}\n\n"
        f"Is the string {s} accepted by this language?"
    )


def _classify_prompt(case: Case) -> str:
    s = repr(case.input_str) if case.input_str else '""  (empty string)'
    return f"Input string: {s}\n\nWhich of the four languages contain this string?"


# ---------------------------------------------------------------------------
# API call with streaming TTFT
# ---------------------------------------------------------------------------

@dataclass
class CallResult:
    text:          str
    ttft_ms:       float
    total_ms:      float
    input_tokens:  int
    output_tokens: int
    think_tokens:  int = 0      # reasoning tokens (Gemini only)


# ---------------------------------------------------------------------------
# Provider abstraction
# ---------------------------------------------------------------------------

class Provider(Protocol):
    def call(self, system: str, user: str, max_tokens: int) -> CallResult: ...
    @property
    def label(self) -> str: ...


class AnthropicProvider:
    def __init__(self, model: str):
        import anthropic as _anthropic
        self._client = _anthropic.Anthropic()
        self._model = model

    @property
    def label(self) -> str:
        return f"anthropic/{self._model}"

    def call(self, system: str, user: str, max_tokens: int = 256) -> CallResult:
        import anthropic as _anthropic
        ttft_ms: Optional[float] = None
        chunks: list[str] = []
        t0 = time.perf_counter()

        with self._client.messages.stream(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            for text in stream.text_stream:
                if ttft_ms is None:
                    ttft_ms = (time.perf_counter() - t0) * 1000
                chunks.append(text)
            msg = stream.get_final_message()

        total_ms = (time.perf_counter() - t0) * 1000
        return CallResult(
            text="".join(chunks),
            ttft_ms=ttft_ms if ttft_ms is not None else total_ms,
            total_ms=total_ms,
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
            think_tokens=0,
        )


class GeminiProvider:
    def __init__(self, model: str, thinking_budget: int = 0):
        from google import genai as _genai
        self._client = _genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self._model = model
        self._thinking_budget = thinking_budget  # 0 = off, -1 = let model decide, N = cap

    @property
    def label(self) -> str:
        suffix = "" if self._thinking_budget == 0 else f"+think({self._thinking_budget})"
        return f"gemini/{self._model}{suffix}"

    def call(self, system: str, user: str, max_tokens: int = 256) -> CallResult:
        from google.genai import types as _types

        # Always use a large output budget so thinking tokens don't consume
        # the entire quota and starve actual output.
        output_budget = max(max_tokens, 2048)

        cfg_kwargs: dict = dict(
            system_instruction=system,
            max_output_tokens=output_budget,
        )
        if self._thinking_budget == 0:
            cfg_kwargs["thinking_config"] = _types.ThinkingConfig(thinking_budget=0)
        elif self._thinking_budget > 0:
            cfg_kwargs["thinking_config"] = _types.ThinkingConfig(
                thinking_budget=self._thinking_budget
            )
        # thinking_budget == -1 → omit, let model decide

        ttft_ms: Optional[float] = None
        chunks: list[str] = []
        last_chunk = None
        t0 = time.perf_counter()

        for chunk in self._client.models.generate_content_stream(
            model=self._model,
            contents=user,
            config=_types.GenerateContentConfig(**cfg_kwargs),
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
            ttft_ms=ttft_ms if ttft_ms is not None else total_ms,
            total_ms=total_ms,
            input_tokens=usage.prompt_token_count if usage else 0,
            output_tokens=usage.candidates_token_count if usage else 0,
            think_tokens=getattr(usage, "thoughts_token_count", 0) or 0,
        )


def make_provider(provider: str, model: str, thinking_budget: int = 0) -> Provider:
    if provider == "gemini":
        return GeminiProvider(model, thinking_budget=thinking_budget)
    if provider == "anthropic":
        return AnthropicProvider(model)
    raise ValueError(f"Unknown provider {provider!r}; choose 'gemini' or 'anthropic'")


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_membership_s(text: str) -> Optional[bool]:
    """Parse a yes/no answer from a structured response."""
    t = text.strip().lower().rstrip(".")
    if t in ("yes", "y"):
        return True
    if t in ("no", "n"):
        return False
    # Tolerate trailing punctuation or a full sentence containing yes/no
    if re.search(r"\byes\b", t):
        return True
    if re.search(r"\bno\b", t):
        return False
    return None


def parse_membership_c(text: str) -> Optional[bool]:
    """Extract ANSWER: yes/no from CoT response."""
    m = re.search(r"ANSWER\s*:\s*(yes|no)", text, re.IGNORECASE)
    if m:
        return m.group(1).lower() == "yes"
    # Fallback: last line that is just yes or no
    for line in reversed(text.strip().splitlines()):
        t = line.strip().lower().rstrip(".")
        if t == "yes":
            return True
        if t == "no":
            return False
    return None


def parse_classify(text: str) -> Optional[dict]:
    """Extract JSON object from classify response."""
    # Try direct parse first
    try:
        obj = json.loads(text.strip())
        if isinstance(obj, dict):
            return {k: bool(v) for k, v in obj.items()}
    except json.JSONDecodeError:
        pass
    # Find JSON block inside the text
    m = re.search(r'\{[^}]+\}', text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            return {k: bool(v) for k, v in obj.items()}
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_membership(parsed: Optional[bool], ground_truth: dict) -> Optional[bool]:
    if parsed is None:
        return None
    return parsed == ground_truth["accept"]


def score_classify(parsed: Optional[dict], ground_truth: dict) -> Optional[bool]:
    """All four machine answers must be correct."""
    if parsed is None:
        return None
    machines = ("dfa", "pda", "lba", "tm")
    return all(parsed.get(m) == ground_truth.get(m) for m in machines)


# ---------------------------------------------------------------------------
# Per-result record
# ---------------------------------------------------------------------------

@dataclass
class EvalResult:
    task:          str
    machine:       str
    input_str:     str
    ground_truth:  dict
    llm_raw:       str
    llm_parsed:    object
    correct:       Optional[bool]
    input_tokens:  int
    output_tokens: int
    think_tokens:  int
    ttft_ms:       float
    total_ms:      float


# ---------------------------------------------------------------------------
# Main eval loop
# ---------------------------------------------------------------------------

def run_eval(
    conn:         psycopg.Connection,
    provider:     Provider,
    corpus:       list[Case],
    tasks:        list[str],
    run_id:       str,
    verbose:      bool = False,
    min_gap_s:    float = 0.0,   # minimum seconds between API calls (rate-limit guard)
) -> list[EvalResult]:
    results: list[EvalResult] = []
    total = len(corpus) * len(tasks)
    done = 0
    last_call_t: float = 0.0

    for task in tasks:
        for case in corpus:
            # classify task only uses "all" cases; membership tasks skip "all" cases
            if task == "classify" and case.machine != "all":
                continue
            if task != "classify" and case.machine == "all":
                continue

            system = {
                "membership_s": _MEMBERSHIP_S_SYSTEM,
                "membership_c": _MEMBERSHIP_C_SYSTEM,
                "classify":     _CLASSIFY_SYSTEM,
            }[task]

            user = _classify_prompt(case) if task == "classify" else _membership_prompt(case)

            max_tok = 512 if task == "membership_c" else 128

            # Throttle to stay within rate limit
            if min_gap_s > 0:
                elapsed = time.perf_counter() - last_call_t
                if elapsed < min_gap_s:
                    time.sleep(min_gap_s - elapsed)

            for attempt in range(4):
                try:
                    last_call_t = time.perf_counter()
                    cr = provider.call(system, user, max_tokens=max_tok)
                    break
                except Exception as exc:
                    msg = str(exc)
                    if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "rate" in msg.lower():
                        # Extract retryDelay from error body if present, else back off
                        m = re.search(r"retryDelay['\"]:\s*['\"](\d+)s", msg)
                        wait = int(m.group(1)) + 5 if m else 15 * (2 ** attempt)
                        if verbose:
                            print(f"  [rate-limit] sleeping {wait}s …")
                        time.sleep(wait)
                        if attempt == 3:
                            raise
                    else:
                        raise
            else:
                raise RuntimeError("rate-limit retries exhausted")

            if task == "membership_s":
                parsed = parse_membership_s(cr.text)
            elif task == "membership_c":
                parsed = parse_membership_c(cr.text)
            else:
                parsed = parse_classify(cr.text)

            if task == "classify":
                correct = score_classify(parsed, case.ground_truth)
                machine = "all"
            else:
                correct = score_membership(parsed, case.ground_truth)
                machine = case.machine

            er = EvalResult(
                task=task, machine=machine, input_str=case.input_str,
                ground_truth=case.ground_truth,
                llm_raw=cr.text,
                llm_parsed=parsed,
                correct=correct,
                input_tokens=cr.input_tokens, output_tokens=cr.output_tokens,
                think_tokens=cr.think_tokens,
                ttft_ms=cr.ttft_ms, total_ms=cr.total_ms,
            )
            results.append(er)

            # Persist to DB (think_tokens stored in metadata column via llm_parsed extension)
            conn.execute(
                """
                INSERT INTO curry.llm_eval_result
                    (run_id, task, machine, input_str, ground_truth,
                     llm_raw, llm_parsed, correct,
                     input_tokens, output_tokens, ttft_ms, total_ms)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (run_id, task, machine, case.input_str,
                 json.dumps(case.ground_truth),
                 cr.text,
                 json.dumps({"answer": parsed, "think_tokens": cr.think_tokens}),
                 correct,
                 cr.input_tokens, cr.output_tokens,
                 cr.ttft_ms, cr.total_ms),
            )
            conn.commit()

            done += 1
            mark = "+" if correct else ("-" if correct is False else "?")
            if verbose:
                s_repr = repr(case.input_str)[:20]
                think = f" think={cr.think_tokens}" if cr.think_tokens else ""
                print(f"  [{done:>3}/{total}] {task:<14} {machine:<4} {s_repr:<22} "
                      f"{mark}  {cr.total_ms:>6.0f}ms  "
                      f"in={cr.input_tokens} out={cr.output_tokens}{think}")

    return results


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _pct(num: int, den: int) -> str:
    return f"{num/den*100:>5.1f}%" if den else "   n/a"


def print_report(results: list[EvalResult], model: str) -> None:
    print(f"\n{'='*74}")
    print(f"  LLM Evaluation Report — model: {model}")
    print(f"{'='*74}")

    tasks = sorted({r.task for r in results})
    machines = sorted({r.machine for r in results})

    # ---- Accuracy by task × machine ----
    print(f"\n  Accuracy  (correct / scored)\n")
    header = f"  {'Machine':<10}"
    for t in tasks:
        header += f"  {t:<16}"
    print(header)
    print(f"  {'-'*10}" + (f"  {'-'*16}" * len(tasks)))

    for mach in machines:
        row = f"  {mach:<10}"
        for t in tasks:
            sub = [r for r in results if r.task == t and r.machine == mach]
            scored = [r for r in sub if r.correct is not None]
            correct = sum(1 for r in scored if r.correct)
            row += f"  {correct:>3}/{len(scored):<3} {_pct(correct, len(scored)):<8}"
        print(row)

    # Overall accuracy per task
    print(f"  {'TOTAL':<10}", end="")
    for t in tasks:
        sub = [r for r in results if r.task == t]
        scored = [r for r in sub if r.correct is not None]
        correct = sum(1 for r in scored if r.correct)
        print(f"  {correct:>3}/{len(scored):<3} {_pct(correct, len(scored)):<8}", end="")
    print()

    # Parse failures
    print(f"\n  Parse failures:")
    for t in tasks:
        failed = [r for r in results if r.task == t and r.correct is None]
        if failed:
            print(f"    {t}: {len(failed)} unparseable responses")
            for r in failed[:3]:
                print(f"      input={r.input_str!r:<15}  raw={r.llm_raw[:60]!r}")

    # ---- Latency ----
    print(f"\n  Latency  (wall time per call)\n")
    print(f"  {'Task':<16} {'n':>5}  {'med TTFT':>10}  {'med total':>10}  "
          f"{'p95 total':>10}  {'p99 total':>10}")
    print(f"  {'-'*16} {'-'*5}  {'-'*10}  {'-'*10}  {'-'*10}  {'-'*10}")

    for t in tasks:
        sub = [r for r in results if r.task == t]
        if not sub:
            continue
        ttfts = sorted(r.ttft_ms for r in sub)
        totals = sorted(r.total_ms for r in sub)
        n = len(totals)
        print(f"  {t:<16} {n:>5}  "
              f"{statistics.median(ttfts):>9.1f}ms  "
              f"{statistics.median(totals):>9.1f}ms  "
              f"{totals[int(n*0.95)]:>9.1f}ms  "
              f"{totals[int(n*0.99)]:>9.1f}ms")

    # ---- Token usage ----
    has_think = any(r.think_tokens for r in results)
    print(f"\n  Token usage{'  (think=Gemini reasoning tokens)' if has_think else ''}\n")
    hdr = f"  {'Task':<16} {'n':>5}  {'med in':>8}  {'med out':>8}"
    if has_think:
        hdr += f"  {'med think':>10}"
    hdr += f"  {'total in':>9}  {'total out':>9}  {'tok/correct':>12}"
    print(hdr)
    sep = f"  {'-'*16} {'-'*5}  {'-'*8}  {'-'*8}"
    if has_think:
        sep += f"  {'-'*10}"
    sep += f"  {'-'*9}  {'-'*9}  {'-'*12}"
    print(sep)

    for t in tasks:
        sub = [r for r in results if r.task == t]
        if not sub:
            continue
        correct_n = sum(1 for r in sub if r.correct)
        total_in    = sum(r.input_tokens  for r in sub)
        total_out   = sum(r.output_tokens for r in sub)
        total_think = sum(r.think_tokens  for r in sub)
        med_in    = statistics.median(r.input_tokens  for r in sub)
        med_out   = statistics.median(r.output_tokens for r in sub)
        med_think = statistics.median(r.think_tokens  for r in sub)
        billed = total_in + total_out + total_think
        tok_per_correct = billed / correct_n if correct_n else float("inf")
        row = f"  {t:<16} {len(sub):>5}  {med_in:>8.0f}  {med_out:>8.0f}"
        if has_think:
            row += f"  {med_think:>10.0f}"
        row += f"  {total_in:>9}  {total_out:>9}  {tok_per_correct:>12.0f}"
        print(row)

    # ---- Accuracy vs token cost tradeoff (structured vs CoT) ----
    if "membership_s" in tasks and "membership_c" in tasks:
        print(f"\n  Structured vs Chain-of-thought tradeoff:\n")
        for mach in [m for m in machines if m != "all"]:
            s_sub = [r for r in results if r.task == "membership_s" and r.machine == mach]
            c_sub = [r for r in results if r.task == "membership_c" and r.machine == mach]
            if not s_sub or not c_sub:
                continue
            s_acc = sum(1 for r in s_sub if r.correct) / max(len(s_sub), 1)
            c_acc = sum(1 for r in c_sub if r.correct) / max(len(c_sub), 1)
            s_tok = statistics.median(r.output_tokens for r in s_sub)
            c_tok = statistics.median(r.output_tokens for r in c_sub)
            s_lat = statistics.median(r.total_ms for r in s_sub)
            c_lat = statistics.median(r.total_ms for r in c_sub)
            print(f"  {mach.upper():<4}  structured: acc={s_acc:.0%}  "
                  f"out_tok={s_tok:.0f}  lat={s_lat:.0f}ms")
            print(f"        cot:        acc={c_acc:.0%}  "
                  f"out_tok={c_tok:.0f}  lat={c_lat:.0f}ms  "
                  f"(delta acc={c_acc-s_acc:+.0%}  "
                  f"cost={c_tok/max(s_tok,1):.1f}x tokens)")

    print(f"\n{'='*74}\n")


# ---------------------------------------------------------------------------
# Report-only mode: print summary of stored results
# ---------------------------------------------------------------------------

def report_last_run(conn: psycopg.Connection) -> None:
    row = conn.execute(
        "SELECT run_id, model, tasks, corpus_n, created_at "
        "FROM curry.llm_eval_run ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        print("No eval runs found.")
        return
    run_id, model, tasks_str, corpus_n, created_at = row
    print(f"\nLast run: {run_id}  model={model}  tasks={tasks_str}  "
          f"corpus={corpus_n}  at={created_at}")

    rows = conn.execute(
        "SELECT task, machine, input_str, ground_truth, llm_raw, llm_parsed, "
        "correct, input_tokens, output_tokens, ttft_ms, total_ms "
        "FROM curry.llm_eval_result WHERE run_id = %s",
        (run_id,),
    ).fetchall()

    results = [
        EvalResult(
            task=r[0], machine=r[1], input_str=r[2],
            ground_truth=r[3], llm_raw=r[4],
            llm_parsed=r[5], correct=r[6],
            input_tokens=r[7], output_tokens=r[8],
            think_tokens=(r[5] or {}).get("think_tokens", 0) if isinstance(r[5], dict) else 0,
            ttft_ms=r[9], total_ms=r[10],
        )
        for r in rows
    ]
    print_report(results, model)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LLM accuracy/speed/token eval harness")
    p.add_argument("--dsn",      default=None)
    p.add_argument("--provider", default="gemini",
                   choices=["gemini", "anthropic"],
                   help="API provider (default: gemini)")
    p.add_argument("--model",    default=None,
                   help="Model ID (default: gemini-2.5-flash for gemini, "
                        "claude-haiku-4-5-20251001 for anthropic)")
    p.add_argument("--tasks",           default="membership_s,membership_c,classify",
                   help="Comma-separated task list (default: all three)")
    p.add_argument("--thinking-budget", type=int, default=0,
                   help="Gemini thinking budget: 0=off (default), -1=auto, N=cap at N tokens")
    p.add_argument("--min-gap", type=float, default=None,
                   help="Minimum seconds between API calls. "
                        "Default: 13s for Gemini free tier (5 RPM), 0 for Anthropic. "
                        "Paid Gemini 2.5 Flash (15 RPM): use 4.5")
    p.add_argument("--verbose",  action="store_true",
                   help="Print each result as it arrives")
    p.add_argument("--report-only", action="store_true",
                   help="Print summary of the last stored run, then exit")
    p.add_argument("--no-setup", action="store_true",
                   help="Skip schema apply")
    return p.parse_args()


_PROVIDER_DEFAULTS = {
    "gemini":    "gemini-2.5-flash",
    "anthropic": "claude-haiku-4-5-20251001",
}


def main() -> None:
    args = _parse()
    dsn   = args.dsn or resolve_dsn()
    tasks = [t.strip() for t in args.tasks.split(",")]
    model = args.model or _PROVIDER_DEFAULTS[args.provider]

    with psycopg.connect(dsn, autocommit=False) as conn:
        if not args.no_setup:
            apply_schema(conn)
            conn.execute(_EVAL_SCHEMA_SQL)
            conn.commit()

        if args.report_only:
            report_last_run(conn)
            return

        provider = make_provider(args.provider, model,
                                 thinking_budget=args.thinking_budget)

        print(f"\nGenerating corpus from DB simulators ...", flush=True)
        corpus = generate_corpus(conn)

        membership_cases = [c for c in corpus if c.machine != "all"]
        classify_cases   = [c for c in corpus if c.machine == "all"]
        mem_calls = len(membership_cases) * sum(1 for t in tasks if t != "classify")
        cls_calls = len(classify_cases)   * sum(1 for t in tasks if t == "classify")

        print(f"  {len(membership_cases)} membership cases + "
              f"{len(classify_cases)} classify cases  ->  "
              f"{mem_calls + cls_calls} API calls planned")
        print(f"  Provider: {provider.label}")
        print(f"  Tasks: {', '.join(tasks)}\n")

        run_id = str(uuid.uuid4())[:12]
        conn.execute(
            "INSERT INTO curry.llm_eval_run (run_id, model, tasks, corpus_n) "
            "VALUES (%s, %s, %s, %s)",
            (run_id, provider.label, args.tasks, len(corpus)),
        )
        conn.commit()

        # Rate-limit throttle: default 13s for Gemini free tier (5 RPM).
        # Paid Gemini 2.5 Flash (15 RPM): pass --min-gap 4.5
        if args.min_gap is not None:
            min_gap = args.min_gap
        elif args.provider == "gemini":
            min_gap = 13.0
        else:
            min_gap = 0.0

        results = run_eval(conn, provider, corpus, tasks, run_id,
                           verbose=args.verbose, min_gap_s=min_gap)

        print_report(results, provider.label)
        print(f"Run ID: {run_id}  (replay with --report-only)\n")


if __name__ == "__main__":
    main()
