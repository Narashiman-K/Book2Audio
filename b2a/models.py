"""Core data models shared across the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass
class Paragraph:
    """A single paragraph of book text, already cleaned."""
    id: str                 # "c02-p003"
    text: str               # full paragraph text (no markup)
    sentences: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Chapter:
    """A chapter: ordered paragraphs + resolved title."""
    index: int              # 1-based
    title: str
    paragraphs: List[Paragraph] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "title": self.title,
            "paragraphs": [p.to_dict() for p in self.paragraphs],
        }


@dataclass
class ProsodySegment:
    """How ONE paragraph should be narrated.

    rate_delta / pitch_delta are percentage points relative to the global
    voice settings (e.g. -10 = 10% slower than global, +6 = 6% higher).
    pause_after_ms is appended AFTER the paragraph audio.
    """
    para_id: str
    mood: str = "neutral"
    emphasis: List[str] = field(default_factory=list)   # words to stress
    rate_delta: int = 0
    pitch_delta: int = 0
    pause_after_ms: int = 400
    source: str = "rules"        # rules | llm | minimal

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Book:
    """The whole parsed book."""
    title: str
    author: str
    source_path: str
    source_format: str
    chapters: List[Chapter] = field(default_factory=list)

    @property
    def total_paragraphs(self) -> int:
        return sum(len(c.paragraphs) for c in self.chapters)

    @property
    def total_chars(self) -> int:
        return sum(len(p.text) for c in self.chapters for p in c.paragraphs)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "author": self.author,
            "source_path": self.source_path,
            "source_format": self.source_format,
            "chapters": [c.to_dict() for c in self.chapters],
        }
