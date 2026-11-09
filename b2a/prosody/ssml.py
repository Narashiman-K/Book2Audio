"""SSML builders: turn a ProsodySegment + global voice settings into engine markup.

Two flavors:
* ssml  — W3C SSML 1.0 with relative prosody. Works for Windows WinRT
          (natural voices) and System.Speech fallback.
* sapi  — SAPI 5 XML (rate/pitch/silence) for the legacy SAPI desktop engine.

Emphasis technique: <emphasis> has patchy support on Windows voices, so we
express word stress as a small <prosody> lift (+pitch, -rate) around the
word — the reliable cross-engine way to "punch" a word.
"""
from __future__ import annotations

import re
from typing import List, Optional
from xml.sax.saxutils import escape

from ..models import ProsodySegment
from .styles import get_style

_SPEAK_OPEN = (
    '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
)
_SPEAK_CLOSE = "</speak>"

_WORD_RE = re.compile(r"[\w'’]+")
_ELLIPSIS_RE = re.compile(r"(\.\s*\.\s*\.|\.\.\.|\u2026)")


def compute_global_prosody(speed: float, pitch: float, cfg) -> dict:
    """Map 0-20 speed / -10..+10 pitch scales -> SSML percentages."""
    rpu = float(cfg.get_path("tts.rate_percent_per_speed_unit", 10))
    ppu = float(cfg.get_path("tts.pitch_percent_per_pitch_unit", 6))
    rate_pct = (float(speed) - 10.0) * rpu
    pitch_pct = float(pitch) * ppu
    return {
        "rate_pct": int(round(max(-60, min(80, rate_pct)))),
        "pitch_pct": int(round(max(-50, min(50, pitch_pct)))),
    }


def build_ssml(
    text: str,
    sentences: List[str],
    segment: ProsodySegment,
    global_rate_pct: int,
    global_pitch_pct: int,
    style_name: str = "warm",
    pauses: Optional[dict] = None,
) -> str:
    st = get_style(style_name)
    pauses = pauses or {}

    total_rate = global_rate_pct + segment.rate_delta
    total_pitch = global_pitch_pct + segment.pitch_delta
    attrs = []
    if total_rate:
        attrs.append(f'rate="{total_rate:+d}%"')
    if total_pitch:
        attrs.append(f'pitch="{total_pitch:+d}%"')

    body = _build_body(
        text=text,
        sentences=sentences,
        segment=segment,
        st=st,
        pauses=pauses,
    )

    if attrs:
        inner = f'<prosody {" ".join(attrs)}>{body}</prosody>'
    else:
        inner = body
    return _SPEAK_OPEN + inner + _SPEAK_CLOSE


def _build_body(text: str, sentences: List[str], segment: ProsodySegment,
                st: dict, pauses: dict) -> str:
    sent_break = int(pauses.get("sentence_ms", 380) * st["pause_scale"])
    ellipsis_break = int(pauses.get("ellipsis_ms", 800) * st["pause_scale"])
    clause_break = int(pauses.get("comma_ms", 150) * st["pause_scale"])

    use_sents = sentences if sentences else [text]
    parts: List[str] = []
    budget = len(segment.emphasis)

    for i, sent in enumerate(use_sents):
        rendered = _render_sentence(
            sent,
            emphasis=segment.emphasis if budget > 0 else [],
            st=st,
            clause_break=clause_break,
        )
        budget -= max(0, len(_find_spans(sent, segment.emphasis)))
        parts.append(rendered)
        if i < len(use_sents) - 1:
            parts.append(f'<break time="{sent_break}ms"/>')

    body = " ".join(parts)

    # dramatic beats inside the paragraph
    if ellipsis_break > 0:
        body = _ELLIPSIS_RE.sub(rf'\1<break time="{ellipsis_break}ms"/>', body)
    if clause_break > 0:
        body = re.sub(r"(\s[—–]\s)", rf'\1<break time="{clause_break}ms"/> ', body)

    # leading <break> after a prosody-open would be invalid -> keep order: text first
    return body.strip()


def _find_spans(sentence: str, emphasis: List[str]) -> List[tuple]:
    """Case-insensitive spans of emphasis phrases inside a sentence."""
    spans: List[tuple] = []
    low = sentence.lower()
    for phrase in emphasis:
        phrase = phrase.strip().lower()
        if not phrase:
            continue
        start = 0
        while True:
            idx = low.find(phrase, start)
            if idx == -1:
                break
            end = idx + len(phrase)
            # word-boundary check
            before_ok = idx == 0 or not (low[idx - 1].isalnum())
            after_ok = end >= len(low) or not (low[end].isalnum())
            if before_ok and after_ok:
                spans.append((idx, end))
                start = end
            else:
                start = idx + 1
    spans.sort()
    # drop overlaps
    out: List[tuple] = []
    last_end = -1
    for s, e in spans:
        if s >= last_end:
            out.append((s, e))
            last_end = e
    return out


def _render_sentence(sentence: str, emphasis: List[str], st: dict, clause_break: int) -> str:
    """Escape sentence, wrap emphasized spans in prosody, add clause breaks."""
    # minimal style (or zero emphasis deltas): never wrap words
    if st["emphasis_rate_delta"] == 0 and st["emphasis_pitch_delta"] == 0:
        return _clause_breaks(sentence, clause_break)
    spans = _find_spans(sentence, emphasis)[:2]  # hard cap: 2 stressed words/sentence

    out: List[str] = []
    pos = 0
    for (s, e) in spans:
        out.append(_clause_breaks(sentence[pos:s], clause_break))
        word = sentence[s:e]
        out.append(
            f'<prosody rate="{st["emphasis_rate_delta"]:+d}%" '
            f'pitch="{st["emphasis_pitch_delta"]:+d}%">{escape(word)}</prosody>'
        )
        pos = e
    out.append(_clause_breaks(sentence[pos:], clause_break))
    return "".join(out)


def _clause_breaks(text: str, clause_break_ms: int) -> str:
    escaped = escape(text)
    if clause_break_ms > 0:
        escaped = re.sub(r"([;:])\s+", rf'\1<break time="{clause_break_ms}ms"/> ', escaped)
    return escaped


# ---------------------------------------------------------------------------
# SAPI 5 XML (legacy desktop voices: David, Zira)
# rate: -10..10   pitch: -10..10   silence: msec
# ---------------------------------------------------------------------------

def build_sapi_xml(
    text: str,
    sentences: List[str],
    segment: ProsodySegment,
    global_rate_pct: int,
    global_pitch_pct: int,
    style_name: str = "warm",
    pauses: Optional[dict] = None,
) -> str:
    st = get_style(style_name)
    pauses = pauses or {}

    # SSML percentages -> SAPI -10..10 scales
    sapi_rate = max(-10, min(10, round(global_rate_pct / 10 + segment.rate_delta / 10)))
    sapi_pitch = max(-10, min(10, round(global_pitch_pct / 6 + segment.pitch_delta / 6)))

    parts: List[str] = []
    if sapi_rate:
        parts.append(f'<rate speed="{sapi_rate}">')
    if sapi_pitch:
        parts.append(f'<pitch middle="{sapi_pitch}">')

    use_sents = sentences if sentences else [text]
    sent_break = int(pauses.get("sentence_ms", 380) * st["pause_scale"])
    for i, sent in enumerate(use_sents):
        parts.append(escape(_strip_emphasis_words(sent, segment.emphasis)))
        if i < len(use_sents) - 1:
            parts.append(f'<silence msec="{sent_break}"/>')
    body = " ".join(parts)

    if sapi_pitch:
        parts.append("</pitch>")
    if sapi_rate:
        parts.append("</rate>")
    return "".join(parts) if (sapi_rate or sapi_pitch) else body


def _strip_emphasis_words(text: str, emphasis: List[str]) -> str:
    """SAPI <emph> support is unreliable; leave text plain."""
    return text


def build_plain_text(text: str, sentences: List[str], segment: ProsodySegment) -> str:
    """For engines without markup support (Piper): punctuation does the work."""
    return " ".join(sentences) if sentences else text
