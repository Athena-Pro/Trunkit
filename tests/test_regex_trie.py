"""Literal-list construction must retain regex semantics and minimization evidence."""

import psycopg
import pytest


@pytest.mark.parametrize("pattern", ["cat|car|dog", "(cat|car|dog)", "((cat|car|dog))"])
def test_literal_alternation_uses_trie_and_retains_partition(conn, pattern):
    before = conn.execute("SELECT count(*) FROM nerode.automata").fetchone()[0]
    aid = conn.execute(
        "SELECT nerode.from_regex(%s, 'word_list')", (pattern,)
    ).fetchone()[0]
    name, source, provenance = conn.execute(
        "SELECT name, source_regex, provenance FROM nerode.automata WHERE id = %s", (aid,)
    ).fetchone()
    assert name == "word_list"
    assert source == pattern
    assert provenance["construction"] == "literal_trie"
    assert provenance["nfa_id"] is None
    assert conn.execute("SELECT count(*) FROM nerode.automata").fetchone()[0] == before + 1
    partition = conn.execute(
        "SELECT result->'partition' FROM nerode.construction_log "
        "WHERE automaton_id = %s AND operation = 'minimize'", (aid,)
    ).fetchone()[0]
    assert partition
    for word in ("cat", "car", "dog", "", "ca", "cats", "do", "cart", "dogcat"):
        accepted = conn.execute(
            "SELECT accept FROM nerode.run(%s, %s)", (aid, word)
        ).fetchone()[0]
        assert accepted == (word in {"cat", "car", "dog"})


def test_duplicate_and_prefix_words_match_general_pipeline(conn):
    fast = conn.execute("SELECT nerode.from_regex('(a|ab|abc|a|_2)')").fetchone()[0]
    general = conn.execute("SELECT nerode.from_regex('(a)|(ab)|(abc)|(a)|(_2)')").fetchone()[0]
    assert conn.execute(
        "SELECT equivalent FROM nerode.equivalent(%s, %s)", (fast, general)
    ).fetchone()[0]
    assert conn.execute(
        "SELECT provenance->>'construction' FROM nerode.automata WHERE id = %s", (general,)
    ).fetchone()[0] == "thompson_subset"


@pytest.mark.parametrize("symbols, accepted", [(["a", "b"], {"a", "ab"}), (["z"], set())])
def test_explicit_alphabet_keeps_only_consumable_words(conn, symbols, accepted):
    aid = conn.execute(
        "SELECT nerode.from_regex('(a|ab|cat)', NULL, %s)", (symbols,)
    ).fetchone()[0]
    for word in ("", "a", "ab", "cat", "z", "aa"):
        result = conn.execute(
            "SELECT accept FROM nerode.run(%s, %s)", (aid, word)
        ).fetchone()[0]
        assert result == (word in accepted)


def test_nonliteral_operators_still_use_general_pipeline(conn):
    aid = conn.execute("SELECT nerode.from_regex('(cat|dog)+')").fetchone()[0]
    assert conn.execute(
        "SELECT provenance->>'construction' FROM nerode.automata WHERE id = %s", (aid,)
    ).fetchone()[0] == "thompson_subset"
    for word in ("cat", "dogcat", "catdogdog", "", "ca", "dogs"):
        result = conn.execute(
            "SELECT accept FROM nerode.run(%s, %s)", (aid, word)
        ).fetchone()[0]
        assert result == (word in {"cat", "dogcat", "catdogdog"})


@pytest.mark.parametrize("pattern", ["cat$", "(cat|dog", "cat|dog)", "cat|dog\n"])
def test_literal_detection_does_not_hide_invalid_syntax(conn, pattern):
    with pytest.raises(psycopg.Error):
        conn.execute("SELECT nerode.from_regex(%s)", (pattern,))
