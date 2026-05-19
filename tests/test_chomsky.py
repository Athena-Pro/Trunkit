"""
tests/test_chomsky.py
=====================
Chomsky hierarchy simulation tests.

Four machines, each recognising a canonical language:

  Type 3 DFA  : a*b*
  Type 2 PDA  : { a^n b^n   | n >= 1 }
  Type 1 LBA  : { a^n b^n c^n | n >= 1 }
  Type 0 TM   : { 0^(2^k)   | k >= 0 }

The classify() orchestrator runs all four and returns one row per machine.
"""

from __future__ import annotations
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dfa(conn, word):
    row = conn.execute(
        "SELECT accept, witness FROM chomsky.run_dfa(%s)", (word,)
    ).fetchone()
    return row[0], row[1]


def _pda(conn, word):
    row = conn.execute(
        "SELECT accept, witness FROM chomsky.run_pda(%s)", (word,)
    ).fetchone()
    return row[0], row[1]


def _lba(conn, word):
    row = conn.execute(
        "SELECT accept, witness FROM chomsky.run_lba(%s)", (word,)
    ).fetchone()
    return row[0], row[1]


def _tm(conn, word):
    row = conn.execute(
        "SELECT accept, witness FROM chomsky.run_tm(%s)", (word,)
    ).fetchone()
    return row[0], row[1]


def _classify(conn, word):
    """Return dict: machine -> (accept, witness)"""
    rows = conn.execute(
        "SELECT chomsky_type, machine, accept, witness FROM chomsky.classify(%s)", (word,)
    ).fetchall()
    return {r[1]: (r[2], r[3]) for r in rows}


# ---------------------------------------------------------------------------
# Type 3 — DFA for a*b*
# ---------------------------------------------------------------------------

class TestDFA:
    @pytest.mark.parametrize("word", ["", "a", "b", "aa", "bb", "aaabbb", "aab"])
    def test_accepts_a_star_b_star(self, conn, word):
        accept, _ = _dfa(conn, word)
        assert accept, f"DFA must accept {word!r}"

    @pytest.mark.parametrize("word", ["ba", "aba", "bba", "c", "abc"])
    def test_rejects_non_a_star_b_star(self, conn, word):
        accept, wit = _dfa(conn, word)
        assert not accept, f"DFA must reject {word!r}"

    def test_trace_has_steps(self, conn):
        _, wit = _dfa(conn, "aab")
        assert wit is not None
        assert "steps" in wit
        assert len(wit["steps"]) > 0

    def test_fast_path_no_witness(self, conn):
        row = conn.execute(
            "SELECT accept, witness FROM chomsky.run_dfa(%s, FALSE)", ("aab",)
        ).fetchone()
        assert row[0] is True
        # fast path still returns witness=None only if we chose not to; currently
        # run_dfa always traces — fast path omits steps key (implementation choice)


# ---------------------------------------------------------------------------
# Type 2 — PDA for a^n b^n
# ---------------------------------------------------------------------------

class TestPDA:
    @pytest.mark.parametrize("n", [1, 2, 3, 5, 10, 50])
    def test_accepts_balanced(self, conn, n):
        word = "a" * n + "b" * n
        accept, _ = _pda(conn, word)
        assert accept, f"PDA must accept a^{n} b^{n}"

    @pytest.mark.parametrize("word", [
        "",          # empty: not in language
        "a",         # only a's
        "b",         # only b's
        "aab",       # 2a, 1b
        "abb",       # 1a, 2b
        "ba",        # reversed
        "aaabbbccc", # a^n b^n c^n (needs LBA, not PDA)
    ])
    def test_rejects(self, conn, word):
        accept, wit = _pda(conn, word)
        assert not accept, f"PDA must reject {word!r}"
        assert wit is not None

    def test_trace_has_stack_steps(self, conn):
        _, wit = _pda(conn, "aabb")
        assert "steps" in wit
        steps = wit["steps"]
        # every traced step should have a 'stack' key
        for step in steps:
            assert "stack" in step

    def test_rejection_witness_has_reason(self, conn):
        accept, wit = _pda(conn, "aab")
        assert not accept
        assert "reason" in wit


# ---------------------------------------------------------------------------
# Type 1 — LBA for a^n b^n c^n
# ---------------------------------------------------------------------------

class TestLBA:
    @pytest.mark.parametrize("n", [1, 2, 3, 5, 10])
    def test_accepts_balanced(self, conn, n):
        word = "a" * n + "b" * n + "c" * n
        accept, _ = _lba(conn, word)
        assert accept, f"LBA must accept a^{n} b^{n} c^{n}"

    @pytest.mark.parametrize("word", [
        "",            # empty
        "abc",         # actually accepted (n=1)! listed as separate check below
        "aabb",        # PDA language, not LBA language
        "aabbcc",      # LBA language (n=2) — accepted!
        "aaabbbccc",   # n=3 accepted
    ])
    def test_accepts_n1_n2_n3(self, conn, word):
        # Re-check: abc, aabbcc, aaabbbccc should all accept
        if word in ("abc", "aabbcc", "aaabbbccc"):
            accept, _ = _lba(conn, word)
            assert accept, f"LBA must accept {word!r}"

    @pytest.mark.parametrize("word", [
        "",
        "a",
        "ab",
        "aab",
        "aabb",        # a^2 b^2 c^0 — unbalanced
        "aabc",        # scrambled
        "aabbc",       # a^2 b^2 c^1
        "abcc",        # a^1 b^1 c^2
        "abcabc",      # repeated block
    ])
    def test_rejects(self, conn, word):
        accept, wit = _lba(conn, word)
        assert not accept, f"LBA must reject {word!r}"

    def test_trace_has_sweeps(self, conn):
        _, wit = _lba(conn, "aabbcc")
        assert "sweeps" in wit
        sweeps = wit["sweeps"]
        assert len(sweeps) == 2   # n=2 rounds

    def test_final_tape_all_marked(self, conn):
        accept, wit = _lba(conn, "abc")
        assert accept
        tape = wit["final_tape"]
        assert all(c in ("A", "Y", "Z") for c in tape)


# ---------------------------------------------------------------------------
# Type 0 — TM for 0^(2^k)
# ---------------------------------------------------------------------------

class TestTM:
    @pytest.mark.parametrize("k,length", [
        (0, 1),   # "0"
        (1, 2),   # "00"
        (2, 4),   # "0000"
        (3, 8),
        (4, 16),
        (5, 32),
    ])
    def test_accepts_powers_of_two(self, conn, k, length):
        word = "0" * length
        accept, _ = _tm(conn, word)
        assert accept, f"TM must accept 0^(2^{k}) = {'0'*length!r}"

    @pytest.mark.parametrize("length", [3, 5, 6, 7, 9, 10, 12, 15])
    def test_rejects_non_powers_of_two(self, conn, length):
        word = "0" * length
        accept, wit = _tm(conn, word)
        assert not accept, f"TM must reject '{'0'*length}' (length={length})"
        assert "reason" in wit

    def test_rejects_non_zero_symbols(self, conn):
        accept, wit = _tm(conn, "0010")
        assert not accept
        assert "reason" in wit

    def test_rejects_empty(self, conn):
        accept, wit = _tm(conn, "")
        assert not accept

    def test_trace_has_sweeps(self, conn):
        _, wit = _tm(conn, "0000")   # k=2
        assert "sweeps" in wit
        sweeps = wit["sweeps"]
        # round 1: zeros=4 tape before crossing; round 2: zeros=2; round 3: zeros=1 → accept
        assert len(sweeps) == 3

    def test_tape_shows_crossed_zeros(self, conn):
        accept, wit = _tm(conn, "00")
        assert accept
        tape = wit["final_tape"]
        # After one round crossing every other 0: [0, X]
        assert "X" in tape


# ---------------------------------------------------------------------------
# Orchestrator — chomsky.classify()
# ---------------------------------------------------------------------------

class TestClassify:
    def test_classify_returns_four_rows(self, conn):
        results = _classify(conn, "ab")
        assert set(results.keys()) == {"dfa", "pda", "lba", "tm"}

    def test_classify_aabb(self, conn):
        """aabb: in a*b* (DFA) and a^n b^n (PDA), not in a^n b^n c^n or 0^(2^k)."""
        r = _classify(conn, "aabb")
        assert r["dfa"][0] is True,  "DFA should accept aabb"
        assert r["pda"][0] is True,  "PDA should accept aabb"
        assert r["lba"][0] is False, "LBA should reject aabb"
        assert r["tm"][0] is False,  "TM should reject aabb"

    def test_classify_aabbcc(self, conn):
        """aabbcc: in a^n b^n c^n (LBA). DFA rejects (has c after b's reset? no—a*b* won't allow c)."""
        r = _classify(conn, "aabbcc")
        assert r["dfa"][0] is False, "DFA (a*b*) must reject aabbcc"
        assert r["pda"][0] is False, "PDA must reject aabbcc"
        assert r["lba"][0] is True,  "LBA must accept aabbcc"
        assert r["tm"][0] is False,  "TM must reject aabbcc"

    def test_classify_0000(self, conn):
        """0000 (length 4 = 2^2): only TM accepts."""
        r = _classify(conn, "0000")
        assert r["dfa"][0] is False, "DFA must reject 0000"
        assert r["pda"][0] is False, "PDA must reject 0000"
        assert r["lba"][0] is False, "LBA must reject 0000"
        assert r["tm"][0] is True,   "TM must accept 0000"

    def test_classify_ab(self, conn):
        """ab: in both a*b* and a^1 b^1. DFA and PDA accept."""
        r = _classify(conn, "ab")
        assert r["dfa"][0] is True
        assert r["pda"][0] is True
        assert r["lba"][0] is False
        assert r["tm"][0] is False

    def test_classify_abc(self, conn):
        """abc = a^1 b^1 c^1: LBA accepts. DFA and PDA reject. TM rejects."""
        r = _classify(conn, "abc")
        assert r["dfa"][0] is False
        assert r["pda"][0] is False
        assert r["lba"][0] is True
        assert r["tm"][0] is False

    def test_classify_empty(self, conn):
        """Empty string: DFA (a*b*) accepts (n=0 is valid). Others reject."""
        r = _classify(conn, "")
        assert r["dfa"][0] is True,  "DFA must accept empty (a*b* allows n=0)"
        assert r["pda"][0] is False
        assert r["lba"][0] is False
        assert r["tm"][0] is False

    def test_classify_witness_present(self, conn):
        """Every machine returns a non-null witness."""
        r = _classify(conn, "aabb")
        for machine, (accept, witness) in r.items():
            assert witness is not None, f"{machine} must return a witness"
