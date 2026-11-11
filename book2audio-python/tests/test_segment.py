import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from b2a.segment import split_sentences


def test_basic_split():
    s = split_sentences("One. Two. Three!")
    assert s == ["One.", "Two.", "Three!"]


def test_abbreviation_guard():
    s = split_sentences("Mr. Smith met Dr. Jones at 3.30 pm. They talked.")
    assert len(s) == 2
    assert s[0].startswith("Mr. Smith")
    assert "3.30" in s[0]


def test_ellipsis_kept():
    s = split_sentences("He paused... then spoke again. Done.")
    assert len(s) == 2


def test_overrun_sentence_split_at_clause():
    long = ("She wanted to leave, " * 30) + "but the storm had other plans entirely."
    s = split_sentences(long, max_chars=200)
    assert all(len(x) <= 260 for x in s)
    assert len(s) > 1


def test_quote_sentence():
    s = split_sentences('"You are late," she said. "But never too late."')
    assert len(s) == 2
