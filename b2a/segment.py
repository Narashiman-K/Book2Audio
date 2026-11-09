"""Sentence segmentation for narration.

A tiny dependency-free splitter tuned for books:
* abbreviation guard-list (Mr., Dr., e.g., Vol., etc.)
* decimal numbers, initials, ellipses
* split-overrun sentences at clause boundaries (max_sentence_chars)
"""
from __future__ import annotations

import re
from typing import List

from .models import Paragraph

_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "st", "sr", "jr", "vs", "etc", "e.g", "i.e",
    "fig", "no", "vol", "ch", "pp", "p", "approx", "mt", "ft", "capt", "lt",
    "sgt", "col", "gen", "rev", "hon", "esq", "ltd", "co", "inc", "dept",
    "univ", "assn", "bro", "fr", "rev", "cf", "al", "ed", "eds", "trans",
}

_SENT_END_RE = re.compile(r"([.!?]+[\"'”’)\]]?)\s+")
_DECIMAL_RE = re.compile(r"\d\.\d")
_INITIAL_RE = re.compile(r"\b[A-Z]\.\s")
_ELLIPSIS_RE = re.compile(r"(\.\.\.|\u2026)")

_CLAUSE_SPLIT_RE = re.compile(r"(?<=[;:—–])(\s+)|(?<=,)\s+(?=(?:but|yet|however|although|though|while|whereas|and then|so that)\b)", re.I)


def split_sentences(text: str, max_chars: int = 400) -> List[str]:
    """Split a paragraph into narration-friendly sentences."""
    if not text or not text.strip():
        return []

    protected = _protect_abbreviations(text)
    raw_parts = _SENT_END_RE.split(protected)
    sentences: List[str] = []
    # re.split with one capture group -> [text, SEP, text, SEP, ..., text]
    # so text chunks sit at EVEN indices and separators at ODD indices
    for i in range(0, len(raw_parts), 2):
        chunk = raw_parts[i]
        end = raw_parts[i + 1] if i + 1 < len(raw_parts) else ""
        s = (chunk + end).strip()
        s = _restore_abbreviations(s)
        if s:
            sentences.extend(_split_overrun(s, max_chars))
    return [s.strip() for s in sentences if s.strip()]


def _protect_abbreviations(text: str) -> str:
    """Replace '.' inside known abbreviations/decimals/ellipses with a sentinel
    ('\x00' / '\x01') so the sentence-ending regex can't see them."""
    def guard(m: re.Match) -> str:
        token = m.group(0)
        stem = token.rstrip(".")
        if stem.lower() in _ABBREVIATIONS:
            return stem + "\x00"
        return token

    # ellipsis is a dramatic pause, not a sentence end
    out = re.sub(r"\.\s*\.\s*\.|\u2026", "\x01", text)
    out = re.sub(r"\b(?:[A-Za-z]\.)+[A-Za-z]?\.?", lambda m: m.group(0).replace(".", "\x00"), out)  # e.g./i.e.
    out = _DECIMAL_RE.sub(lambda m: m.group(0).replace(".", "\x00"), out)
    out = re.sub(r"\b([A-Za-z]+)\.", guard, out)
    out = _INITIAL_RE.sub(lambda m: m.group(0).replace(".", "\x00"), out)
    return out


def _restore_abbreviations(s: str) -> str:
    return s.replace("\x00", ".").replace("\x01", "...")


def _split_overrun(sentence: str, max_chars: int) -> List[str]:
    if len(sentence) <= max_chars:
        return [sentence]
    # try clause boundaries (conjunctions), then commas, then words
    parts = [p for p in _CLAUSE_SPLIT_RE.split(sentence) if p and p.strip()]
    if len(parts) <= 1:
        parts = [p for p in re.split(r",\s+", sentence) if p.strip()]
    if len(parts) <= 1:
        return _hard_word_split(sentence, max_chars)

    out: List[str] = []
    buf = ""
    for part in parts:
        part = part.strip()
        if len(part) > max_chars:
            if buf:
                out.append(buf)
                buf = ""
            out.extend(_split_overrun(part, max_chars))
            continue
        if buf and len(buf) + 1 + len(part) > max_chars:
            out.append(buf)
            buf = part
        else:
            buf = f"{buf} {part}".strip() if buf else part
    if buf:
        out.append(buf)
    return [p for p in out if p.strip()]


def _hard_word_split(sentence: str, max_chars: int) -> List[str]:
    out: List[str] = []
    buf = ""
    for word in sentence.split(" "):
        if buf and len(buf) + 1 + len(word) > max_chars:
            out.append(buf)
            buf = word
        else:
            buf = f"{buf} {word}".strip()
    if buf:
        out.append(buf)
    return out


def paragraph_sentences(para: Paragraph, max_chars: int = 400) -> Paragraph:
    para.sentences = split_sentences(para.text, max_chars)
    return para
