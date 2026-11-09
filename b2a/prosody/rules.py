"""Rule-based prosody engine (no LLM needed — fast, deterministic fallback).

Detects, per paragraph:
* mood        (dialogue / question / excited / reflective / neutral)
* emphasis    (power words, CAPS words, quoted key phrases)
* rate/pitch  deltas from punctuation signals
* pause_after (ellipsis, dash, dialogue, paragraph position)
"""
from __future__ import annotations

import re
from typing import List

from ..config import Config
from ..models import Chapter, ProsodySegment
from .styles import get_style

_QUOTE_RE = re.compile(r'[“"]([^”"]{3,})[”"]')
_CAPS_WORD_RE = re.compile(r"\b[A-Z]{3,}\b")
_ELLIPSIS_RE = re.compile(r"(\.\s*\.\s*\.|\u2026|\.\.\.)")
_EXCL_RE = re.compile(r"!")
_Q_END_RE = re.compile(r"\?\s*[\"'”’)\]]?\s*$")
_DASH_RE = re.compile(r"\s[—–]\s")


class RulesEngine:
    def __init__(self, cfg: Config, style_name: str = "warm"):
        self.cfg = cfg
        self.style = get_style(style_name)
        words = cfg.get_path("emphasis.power_words", []) or []
        self.power_words = {w.lower() for w in words}
        self.max_emph = int(cfg.get_path("emphasis.max_per_sentence", 2))

    def plan_chapter(self, chapter: Chapter) -> List[ProsodySegment]:
        return [self.plan_paragraph(p.text, p.id) for p in chapter.paragraphs]

    def plan_paragraph(self, text: str, para_id: str) -> ProsodySegment:
        st = self.style
        seg = ProsodySegment(para_id=para_id, source="rules")
        seg.rate_delta = st["base_rate_delta"]
        seg.pitch_delta = st["base_pitch_delta"]

        # ---------- mood ----------
        quote = _QUOTE_RE.search(text)
        if quote:
            seg.mood = "dialogue"
            seg.pitch_delta += 2
        elif _Q_END_RE.search(text):
            seg.mood = "question"
            seg.pitch_delta += 2
            seg.rate_delta -= 2
        elif text.count("!") >= 1:
            seg.mood = "excited"
            seg.rate_delta += 4
            seg.pitch_delta += 3
        elif _ELLIPSIS_RE.search(text) or text.rstrip().endswith((".", "…")) and len(text) > 240:
            seg.mood = "reflective"
            seg.rate_delta -= 4

        # ---------- emphasis ----------
        picked: List[str] = []
        seen = set()
        # 1) shouted CAPS words always win
        for m in _CAPS_WORD_RE.finditer(text):
            w = m.group(0).lower()
            if w not in seen and len(w) > 3:
                picked.append(m.group(0))
                seen.add(w)
        # 2) power words
        lowered = text.lower()
        for w in self.power_words:
            if len(picked) >= self.max_emph * 2:
                break
            if re.search(rf"\b{re.escape(w)}\b", lowered) and w not in seen:
                picked.append(w)
                seen.add(w)
        # 3) short key phrase inside quotes (the emotional core of dialogue)
        if quote and len(quote.group(1).split()) <= 4:
            picked.append(quote.group(1))
        seg.emphasis = picked[: max(self.max_emph, 1) + 1]

        # ---------- pauses ----------
        ps = float(st["pause_scale"])
        if _ELLIPSIS_RE.search(text):
            seg.pause_after_ms = int(800 * ps)
        elif _DASH_RE.search(text):
            seg.pause_after_ms = int(600 * ps)
        elif seg.mood == "dialogue":
            seg.pause_after_ms = int(550 * ps)
        elif seg.mood == "excited":
            seg.pause_after_ms = int(450 * ps)
        else:
            seg.pause_after_ms = int(420 * ps)

        # ---------- clamps ----------
        seg.rate_delta = max(-st["max_rate_delta"], min(st["max_rate_delta"], seg.rate_delta))
        seg.pitch_delta = max(-st["max_pitch_delta"], min(st["max_pitch_delta"], seg.pitch_delta))
        seg.pause_after_ms = max(0, min(1500, seg.pause_after_ms))
        return seg
