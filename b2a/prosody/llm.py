"""LLM prosody engine — local Ollama, structured JSON output, nothing online.

Flow:
  paragraphs (batch of N) -> Ollama /api/chat with JSON schema -> validated
  ProsodySegment list. Any failure falls back to the rule engine for that
  batch, so a cold/missing Ollama never breaks the audiobook.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import List, Optional

import requests

from ..config import Config
from ..models import Chapter, Paragraph, ProsodySegment
from .rules import RulesEngine
from .styles import get_style

log = logging.getLogger("b2a.llm")

# ---- JSON schema handed to Ollama's "structured outputs" mode -------------
SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "mood": {"type": "string",
                             "enum": ["neutral", "warm", "reflective", "urgent",
                                      "excited", "solemn", "dialogue", "question"]},
                    "emphasis": {"type": "array", "items": {"type": "string"},
                                 "maxItems": 4},
                    "rate_delta": {"type": "integer", "minimum": -15, "maximum": 15},
                    "pitch_delta": {"type": "integer", "minimum": -15, "maximum": 15},
                    "pause_after_ms": {"type": "integer", "minimum": 0, "maximum": 1500},
                },
                "required": ["id", "mood", "emphasis", "rate_delta", "pitch_delta", "pause_after_ms"],
            },
        }
    },
    "required": ["segments"],
}

GPU_MODELS_DEFAULT = ["qwen2.5:7b-instruct", "llama3.1:8b", "qwen2.5-coder:7b", "mistral:7b"]
CPU_MODELS_DEFAULT = ["qwen2.5:3b", "llama3.2:3b", "phi3.5:3.8b", "phi3:mini", "gemma2:2b"]

SYSTEM_PROMPT = """You are a audiobook narration director. For each paragraph you receive, \
decide how a warm human narrator should voice it. Reply ONLY with JSON matching the schema.

Guidelines:
* mood: the emotional color of the paragraph (warm/reflective for description, \
urgent/excited for action or inspiration, solemn for serious moments, dialogue if it quotes speech).
* emphasis: 0-3 words or very short phrases that carry the emotional weight and \
MUST appear verbatim in the paragraph (lowercase preferred). Do not stress function words.
* rate_delta / pitch_delta: percentage points vs the narrator's base voice. \
Suggest -10 (slower/deeper) .. +10 (faster/brighter). Keep most paragraphs near 0; \
use positive pitch for hope/joy, negative pitch for gravity, negative rate for weighty or sad lines.
* pause_after_ms: breath before the next paragraph. 300-700 typical; \
900-1500 only before/after scene shifts or dramatic reveals."""


class OllamaProsody:
    def __init__(self, cfg: Config, style_name: str = "warm"):
        self.cfg = cfg
        self.style = get_style(style_name)
        self.url = cfg.get_path("llm.url", "http://localhost:11434").rstrip("/")
        self.timeout = int(cfg.get_path("llm.timeout_s", 120))
        self.batch = int(cfg.get_path("llm.paragraphs_per_call", 3))
        self.temperature = float(cfg.get_path("llm.temperature", 0.2))
        self.rules = RulesEngine(cfg, style_name)   # fallback + clamps
        self.model: Optional[str] = None
        self.gpu = _detect_gpu()

    # ---------------- public API ----------------
    def available(self) -> bool:
        try:
            r = requests.get(f"{self.url}/api/tags", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    BLOCKED_REASONING_PREFIXES = ("gpt-oss", "qwen3", "deepseek-r1", "qwq")

    def pick_model(self) -> Optional[str]:
        """Select an installed model. Never auto-picks reasoning models; falls back to rules if none suitable."""
        try:
            r = requests.get(f"{self.url}/api/tags", timeout=5)
            tags = [m["name"] for m in r.json().get("models", [])]
        except Exception:
            return None

        if not tags:
            return None

        explicit = self.cfg.get_path("llm.model", "auto")
        if explicit and explicit != "auto":
            # Check if explicit model is available
            for have in tags:
                if have == explicit or have.split(":")[0] == explicit.split(":")[0]:
                    return have
            log.warning("Explicit LLM model '%s' not found in Ollama (installed: %s). Falling back to rules.",
                        explicit, tags)
            return None

        # Filter out reasoning models that emit chain-of-thought tokens or freeze CPU
        safe_tags = [
            t for t in tags
            if not any(t.lower().startswith(b) for b in self.BLOCKED_REASONING_PREFIXES)
        ]

        prefs = GPU_MODELS_DEFAULT if self.gpu else CPU_MODELS_DEFAULT
        for want in prefs:
            for have in safe_tags:
                if have.split(":")[0] == want.split(":")[0]:
                    return have
        for want in prefs:
            for have in safe_tags:
                if want.split(":")[0] in have:
                    return have

        # NEVER blind-fallback to tags[0]. If no safe model matches, use rule engine
        log.info("No suitable non-reasoning model found in Ollama (installed: %s). Using rule engine.", tags)
        return None

    def plan_chapter(self, chapter: Chapter, on_batch=None) -> List[ProsodySegment]:
        """Plan a chapter; calls on_batch(done_paragraphs, total) for progress."""
        paras = chapter.paragraphs
        segments: List[ProsodySegment] = []
        total = len(paras)
        for i in range(0, total, self.batch):
            batch = paras[i:i + self.batch]
            segments.extend(self._plan_batch(batch))
            if on_batch:
                on_batch(min(i + self.batch, total), total)
        return segments

    def plan_paragraph(self, text: str, para_id: str) -> ProsodySegment:
        return self._plan_batch([Paragraph(id=para_id, text=text)])[0]

    # ---------------- internals ----------------
    def _plan_batch(self, batch: List[Paragraph]) -> List[ProsodySegment]:
        if self.model is None:
            self.model = self.pick_model()
        if not self.model:
            log.warning("No Ollama model available -> rule engine for this batch")
            return [self.rules.plan_paragraph(p.text, p.id) for p in batch]

        payload_text = "\n\n".join(
            json.dumps({"id": p.id, "text": p.text[:1200]}, ensure_ascii=False) for p in batch
        )
        prompt = (
            f"Narration style: {self.style['desc']}.\n"
            f"Paragraphs:\n{payload_text}\n"
            f"Return the segments array with one entry per id ({', '.join(p.id for p in batch)})."
        )
        try:
            response = requests.post(
                f"{self.url}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "format": SCHEMA,               # Ollama structured outputs
                    "options": {
                        "temperature": self.temperature,
                        "num_ctx": 4096,
                        "num_predict": 1024,
                    },
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            content = data["message"]["content"]
            parsed = json.loads(content)
            return self._validate(parsed, batch)
        except Exception as e:
            log.warning("LLM batch failed (%s) -> rule engine fallback", e)
            return [self.rules.plan_paragraph(p.text, p.id) for p in batch]

    def _validate(self, parsed: dict, batch: List[Paragraph]) -> List[ProsodySegment]:
        st = self.style
        got = {s.get("id"): s for s in parsed.get("segments", []) if isinstance(s, dict)}
        out: List[ProsodySegment] = []
        for p in batch:
            s = got.get(p.id)
            if not s:
                out.append(self.rules.plan_paragraph(p.text, p.id))
                continue
            seg = ProsodySegment(para_id=p.id, source="llm")
            seg.mood = str(s.get("mood", "neutral"))[:20]
            # emphasis words must appear in the paragraph text (verbatim-ish)
            lowered = p.text.lower()
            emph = []
            for w in (s.get("emphasis") or [])[:4]:
                w = str(w).strip().lower()
                if w and w in lowered and w not in emph:
                    emph.append(w)
            seg.emphasis = emph[: self.rules.max_emph + 1]
            seg.rate_delta = int(_clamp(s.get("rate_delta", 0), -15, 15)) + st["base_rate_delta"]
            seg.pitch_delta = int(_clamp(s.get("pitch_delta", 0), -15, 15)) + st["base_pitch_delta"]
            seg.rate_delta = max(-st["max_rate_delta"] - 5, min(st["max_rate_delta"] + 5, seg.rate_delta))
            seg.pitch_delta = max(-st["max_pitch_delta"] - 5, min(st["max_pitch_delta"] + 5, seg.pitch_delta))
            seg.pause_after_ms = int(_clamp(s.get("pause_after_ms", 400), 0, 1500))
            out.append(seg)
        return out


def _clamp(v, lo, hi):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return 0


def _detect_gpu() -> bool:
    """True if an NVIDIA GPU is usable (nvidia-smi present and responds)."""
    if shutil.which("nvidia-smi") is None:
        return False
    try:
        r = subprocess.run(["nvidia-smi", "-L"], capture_output=True, timeout=10)
        return r.returncode == 0 and b"GPU" in r.stdout
    except Exception:
        return False
