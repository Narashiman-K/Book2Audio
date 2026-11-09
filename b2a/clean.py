"""Text cleanup for narration: strip markup noise, fix wraps, normalize speech, apply pronunciation lexicon."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None

log = logging.getLogger("b2a.clean")

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

# Boilerplate patterns to drop (copyright, TOC entries, index pages, cataloging)
_BOILERPLATE_PATTERNS = [
    re.compile(r"\b(all rights reserved|reproduced in any form|printed in the united states|isbn\s*[\d\-x]{10,18})\b", re.I),
    re.compile(r"\b(cataloging-in-publication data|library of congress|british library cataloguing)\b", re.I),
    re.compile(r"\b(published by|publishing group|typeset in|cover design by)\b", re.I),
    re.compile(r"^\s*(table of contents|contents)\s*$", re.I),
    re.compile(r"^\s*index\s*$", re.I),
    re.compile(r"^\s*(\.{3,}|\_{3,}|\-{3,})\s*$"),
    re.compile(r"^.{1,40}\.{3,}\s*\d{1,4}\s*$"),  # TOC line: "Chapter 1 .......... 15"
]


def load_lexicon(custom_path: Optional[str | Path] = None) -> Dict[str, str]:
    """Load phonetic/pronunciation replacements from lexicon.yaml if present."""
    candidates = []
    if custom_path:
        candidates.append(Path(custom_path))
    candidates.extend([
        Path.cwd() / "lexicon.yaml",
        Path(__file__).resolve().parent.parent / "lexicon.yaml",
    ])

    for path in candidates:
        if path.is_file():
            try:
                if yaml:
                    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                    repls = data.get("replacements", {})
                    if isinstance(repls, dict):
                        return {str(k): str(v) for k, v in repls.items()}
            except Exception as e:
                log.warning("Failed to parse lexicon file %s: %s", path, e)
    return {}


def apply_lexicon(text: str, lexicon: Dict[str, str]) -> str:
    """Apply pronunciation replacements with word boundaries."""
    if not lexicon or not text:
        return text
    for word, replacement in lexicon.items():
        # Escape for regex and ensure word boundaries
        pattern = rf"\b{re.escape(word)}\b"
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE if word.isupper() else 0)
    return text


def is_boilerplate_paragraph(text: str) -> bool:
    """Return True if paragraph is front matter, legal copyright, TOC, or index artifact."""
    if not text:
        return False
    # If short and matches TOC or Index or metadata
    for pat in _BOILERPLATE_PATTERNS:
        if pat.search(text):
            return True
    return False


def strip_markdown(text: str) -> str:
    """Markdown -> plain narration text."""
    text = _CODE_FENCE_RE.sub(" ", text)
    text = _IMAGE_RE.sub(" ", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _TABLE_LINE_RE.sub(" ", text)
    text = _MD_LIST_PREFIX_RE.sub("", text)
    text = _MD_INLINE_RE.sub("", text)
    return _WS_RE.sub(" ", text).strip()


def clean_paragraph_text(text: str, lexicon: Optional[Dict[str, str]] = None) -> str:
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
    # Normalize speech breaks: dashes and semicolons -> clean conversational pauses
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    text = re.sub(r";\s*", ", ", text)
    text = re.sub(r"\.{4,}", "...", text)
    text = re.sub(r"\s([,.;:!?])", r"\1", text)          # stray space before punctuation
    text = re.sub(r"([,;:])\s(?=[\w\"'])", r"\1 ", text)
    text = text.replace('""', '"')
    text = _WS_RE.sub(" ", text).strip()

    # Drop boilerplate front-matter / legal pages
    if is_boilerplate_paragraph(text):
        return ""

    # Drop blocks that are pure noise after cleaning
    if re.fullmatch(r"[\W\d_\-–—=|>*#·.]+", text or " "):
        return ""

    # A "paragraph" that is mostly digits (tables, index lines) is not speech
    letters = sum(ch.isalpha() for ch in text)
    if letters < max(4, int(0.35 * len(text))):
        return ""

    if lexicon:
        text = apply_lexicon(text, lexicon)

    return text


def clean_paragraphs_in_place(blocks: List[str], lexicon: Optional[Dict[str, str]] = None) -> List[str]:
    out = []
    for b in blocks:
        c = clean_paragraph_text(b, lexicon=lexicon)
        if c:
            out.append(c)
    return out
