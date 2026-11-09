"""Text cleanup for narration: strip markup noise, fix wraps, normalize speech."""
from __future__ import annotations

import re
from typing import List

# --- regexes ---------------------------------------------------------------
_CODE_FENCE_RE = re.compile(r"```.*?```", re.S)
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+")
_FOOTNOTE_RE = re.compile(r"\[\d+\]|\{\d+\}|\[\d+[a-z]?\]")
_MD_INLINE_RE = re.compile(r"(\*\*\*|\*\*|\*|__|_|`)")
_MD_LIST_PREFIX_RE = re.compile(r"^\s{0,6}(?:[-*+]\s+|\d+[.)]\s+|>\s?|#{1,6}\s+)")
_HYPHEN_WRAP_RE = re.compile(r"(\w+)-\n(\w+)")
_WS_RE = re.compile(r"[ \t]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")
_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")

# Speech-friendly expansions (kept minimal & unambiguous)
_ABBREV_EXPANSIONS = [
    (re.compile(r"\bDr\.(?=\s)", re.I), "Doctor"),
    (re.compile(r"\bMr\.(?=\s)", re.I), "Mister"),
    (re.compile(r"\bMrs\.(?=\s)", re.I), "Missus"),
    (re.compile(r"\bMs\.(?=\s)", re.I), "Miss"),
    (re.compile(r"\be\.g\.,?", re.I), "for example,"),
    (re.compile(r"\bi\.e\.,?", re.I), "that is,"),
    (re.compile(r"\betc\.(?=\s|$)", re.I), "etcetera"),
    (re.compile(r"\bvs\.?(?=\s)", re.I), "versus"),
    (re.compile(r"\bFig\.(?=\s)", re.I), "Figure"),
    (re.compile(r"\bSt\.(?=\s[A-Z])", re.I), "Saint"),
]


def strip_markdown(text: str) -> str:
    """Markdown -> plain narration text."""
    text = _CODE_FENCE_RE.sub(" ", text)
    text = _IMAGE_RE.sub(" ", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _TABLE_LINE_RE.sub(" ", text)
    text = _MD_LIST_PREFIX_RE.sub("", text)
    text = _MD_INLINE_RE.sub("", text)
    return _WS_RE.sub(" ", text).strip()


def clean_paragraph_text(text: str) -> str:
    """One raw block -> clean, TTS-ready paragraph text."""
    if not text:
        return ""
    text = strip_markdown(text)
    text = _URL_RE.sub(" ", text)
    text = _FOOTNOTE_RE.sub("", text)
    for pattern, repl in _ABBREV_EXPANSIONS:
        text = pattern.sub(repl, text)
    text = _HYPHEN_WRAP_RE.sub(r"\1\2 ", text)          # word-\nwrap -> wordwrap
    text = text.replace("\u00ad", "")                    # soft hyphen
    text = text.replace("\n", " ")
    text = re.sub(r"\s([,.;:!?])", r"\1", text)          # stray space before punctuation
    text = re.sub(r"([,;:])\s(?=[\w\"'])", r"\1 ", text)
    text = text.replace('""', '"')
    text = _WS_RE.sub(" ", text).strip()
    # drop blocks that are pure noise after cleaning
    if re.fullmatch(r"[\W\d_\-–—=|>*#·.]+", text or " "):
        return ""
    # a "paragraph" that is mostly digits (tables, index lines) is not speech
    letters = sum(ch.isalpha() for ch in text)
    if letters < max(4, int(0.35 * len(text))):
        return ""
    return text


def clean_paragraphs_in_place(blocks: List[str]) -> List[str]:
    out = []
    for b in blocks:
        c = clean_paragraph_text(b)
        if c:
            out.append(c)
    return out
