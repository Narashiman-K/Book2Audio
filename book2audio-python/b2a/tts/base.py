"""TTS engine base classes."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List


@dataclass
class ChunkJob:
    """One paragraph to synthesize."""
    id: str           # unique chunk id (hash) — also the output filename stem
    text: str         # plain narration text
    ssml: str         # engine-appropriate markup (SSML / SAPI XML / plain)
    rate_delta: int = 0  # per-paragraph rate delta (%); used by non-SSML engines


@dataclass
class ChunkResult:
    id: str
    ok: bool
    path: Path
    bytes: int = 0
    error: str = ""


class BaseTTS(ABC):
    name = "base"

    def __init__(self, cfg, style_name: str = "warm"):
        self.cfg = cfg
        self.style_name = style_name

    @abstractmethod
    def available(self) -> bool:
        """Can this engine run on the current machine?"""

    @abstractmethod
    def list_voices(self) -> List[dict]:
        """Installed voices [{name, language, gender, engine}]."""

    @abstractmethod
    def synthesize_batch(self, jobs: List[ChunkJob], out_dir: Path) -> List[ChunkResult]:
        """Synthesize all jobs; write one audio file per job id into out_dir."""

    @property
    def chosen_voice(self) -> str:
        return self.cfg.get_path("tts.voice", "Ava")
