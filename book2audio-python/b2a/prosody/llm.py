"""LLM prosody engine — local Ollama, lean JSON schema, selective expressivity, nothing online.

Flow:
  1. Once per chapter: fast tone arc summary query.
  2. Selective paragraph filtering: expressive paragraphs (dialogue, short punchy lines,
     questions/exclamations, scene transitions) are planned by Ollama; plain exposition
     falls back instantly to the rule engine (huge CPU speedup).
  3. Lean schema: output only mood, emphasis words, and pause bucket. A lookup table
     maps mood to rate/pitch deltas, cutting output tokens by ~50% and eliminating numerical hallucinations.
  4. Paragraph cache: SHA1 hashes of paragraph text ensure instant zero-token reruns.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
from typing import Dict, List, Optional

import requests

from ..config import Config
from ..models import Chapter, Paragraph, ProsodySegment
from .rules import RulesEngine
from .styles import get_style

log = logging.getLogger("b2a.llm")

# ---- Lean JSON schema handed to Ollama's "structured outputs" mode -------------
SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "mood": {
                        "type": "string",
                        "enum": [
                            "neutral", "warm", "reflective", "urgent",
                            "excited", "solemn", "dialogue", "question",
                        ],
                    },
                    "emphasis": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 3,
                    },
                    "pause": {
                        "type": "string",
                        "enum": ["short", "medium", "long", "dramatic"],
                    },
                },
                "required": ["id", "mood", "emphasis", "pause"],
            },
        }
    },
    "required": ["segments"],
}

MOOD_PROSODY_MAP: Dict[str, dict] = {
    "neutral": {"rate_delta": 0, "pitch_delta": 0},
    "warm": {"rate_delta": -2, "pitch_delta": 1},
    "reflective": {"rate_delta": -5, "pitch_delta": -2},
    "urgent": {"rate_delta": 6, "pitch_delta": 2},
    "excited": {"rate_delta": 5, "pitch_delta": 4},
    "solemn": {"rate_delta": -6, "pitch_delta": -4},
    "dialogue": {"rate_delta": 1, "pitch_delta": 2},
    "question": {"rate_delta": -2, "pitch_delta": 3},
}

PAUSE_BUCKET_MAP: Dict[str, int] = {
    "short": 250,
    "medium": 450,
    "long": 650,
    "dramatic": 950,
}

GPU_MODELS_DEFAULT = ["qwen2.5:7b-instruct", "llama3.1:8b", "qwen2.5-coder:7b", "mistral:7b"]
CPU_MODELS_DEFAULT = ["qwen2.5:3b", "llama3.2:3b", "phi3.5:3.8b", "phi3:mini", "gemma2:2b"]

SYSTEM_PROMPT = """You are an audiobook narration director. For each paragraph, select the emotional mood, \
up to 3 words to emphasize, and the trailing pause duration. Reply ONLY with JSON conforming to the schema.

Guidelines:
* mood: emotional color (warm/reflective for description, urgent/excited for action/inspiration, \
solemn for serious moments, dialogue for quoted speech, question for inquiries).
* emphasis: 0-3 words or short phrases that carry emotional weight (MUST appear verbatim in paragraph).
* pause: 'short' (brisk transition, ~250ms), 'medium' (normal narrative pace, ~450ms), \
'long' (reflection/scene break, ~650ms), 'dramatic' (revelation or cliffhanger, ~950ms)."""


def is_expressive_paragraph(text: str, index: int, total: int) -> bool:
    """Determine if a paragraph warrants LLM prosody analysis vs fast deterministic rules."""
    # First and last paragraph of chapter
    if index == 0 or index == total - 1:
        return True
    # Quoted dialogue
    if '"' in text or '“' in text or '”' in text:
        return True
    stripped = text.strip()
    # Punchy lines or scene shifts
    if len(stripped) < 70:
        return True
    # Questions / Exclamations
    if '?' in stripped or '!' in stripped:
        return True
    return False


class OllamaProsody:
    def __init__(self, cfg: Config, style_name: str = "warm"):
        self.cfg = cfg
        self.style_name = style_name
        self.style = get_style(style_name)
        self.url = cfg.get_path("llm.url", "http://localhost:11434").rstrip("/")
        self.timeout = int(cfg.get_path("llm.timeout_s", 120))
        self.batch = int(cfg.get_path("llm.paragraphs_per_call", 6))
        self.temperature = float(cfg.get_path("llm.temperature", 0.2))
        self.rules = RulesEngine(cfg, style_name)   # fallback + clamps
        self.model: Optional[str] = None
        self.gpu = _detect_gpu()
        self._cache: Dict[str, ProsodySegment] = {}

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

        log.info("No suitable non-reasoning model found in Ollama (installed: %s). Using rule engine.", tags)
        return None

    def get_chapter_tone_arc(self, chapter: Chapter) -> str:
        """Query LLM once per chapter for a concise emotional tone arc."""
        if not self.model or not chapter.paragraphs:
            return "Even, thoughtful storytelling."
        sample_text = "\n".join(p.text for p in chapter.paragraphs[:3])[:800]
        prompt = (
            f"Chapter: {chapter.title}\n"
            f"Opening text:\n{sample_text}\n\n"
            f"Describe the tone arc for this chapter in one short phrase (under 12 words), "
            f"e.g. 'Calm reflection building toward urgent resolve'."
        )
        try:
            r = requests.post(
                f"{self.url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_predict": 30, "temperature": 0.2},
                    "keep_alive": "15m",
                },
                timeout=15,
            )
            if r.status_code == 200:
                arc = r.json().get("response", "").strip()
                if arc:
                    return arc
        except Exception as e:
            log.debug("Tone arc query skipped: %s", e)
        return "Calm, engaging narration."

    def plan_chapter(self, chapter: Chapter, on_batch=None) -> List[ProsodySegment]:
        """Plan a chapter; selective LLM for expressive lines, deterministic rules for narrative."""
        paras = chapter.paragraphs
        total = len(paras)
        if total == 0:
            return []

        tone_arc = self.get_chapter_tone_arc(chapter)
        segments: List[Optional[ProsodySegment]] = [None] * total
        llm_batch: List[Paragraph] = []
        llm_indices: List[int] = []
        prev_mood = "neutral"

        for idx, p in enumerate(paras):
            ck = hashlib.sha1((p.text.strip() + ":" + self.style_name).encode()).hexdigest()
            if ck in self._cache:
                segments[idx] = self._cache[ck]
                continue

            if is_expressive_paragraph(p.text, idx, total):
                llm_batch.append(p)
                llm_indices.append(idx)
            else:
                seg = self.rules.plan_paragraph(p.text, p.id)
                segments[idx] = seg
                self._cache[ck] = seg

            if len(llm_batch) >= self.batch:
                batch_segs = self._plan_batch(llm_batch, tone_arc=tone_arc, prev_mood=prev_mood)
                for b_idx, s in zip(llm_indices, batch_segs):
                    segments[b_idx] = s
                    k = hashlib.sha1((paras[b_idx].text.strip() + ":" + self.style_name).encode()).hexdigest()
                    self._cache[k] = s
                    prev_mood = s.mood
                llm_batch = []
                llm_indices = []
                if on_batch:
                    on_batch(idx + 1, total)

        if llm_batch:
            batch_segs = self._plan_batch(llm_batch, tone_arc=tone_arc, prev_mood=prev_mood)
            for b_idx, s in zip(llm_indices, batch_segs):
                segments[b_idx] = s
                k = hashlib.sha1((paras[b_idx].text.strip() + ":" + self.style_name).encode()).hexdigest()
                self._cache[k] = s
            if on_batch:
                on_batch(total, total)

        return [s or self.rules.plan_paragraph(paras[i].text, paras[i].id) for i, s in enumerate(segments)]

    def plan_paragraph(self, text: str, para_id: str) -> ProsodySegment:
        return self._plan_batch([Paragraph(id=para_id, text=text)])[0]

    # ---------------- internals ----------------
    def _plan_batch(self, batch: List[Paragraph], tone_arc: str = "", prev_mood: str = "") -> List[ProsodySegment]:
        if self.model is None:
            self.model = self.pick_model()
        if not self.model:
            log.warning("No Ollama model available -> rule engine for this batch")
            return [self.rules.plan_paragraph(p.text, p.id) for p in batch]

        payload_text = "\n\n".join(
            json.dumps({"id": p.id, "text": p.text[:1200]}, ensure_ascii=False) for p in batch
        )
        context_hint = f"Chapter emotional arc: {tone_arc}. " if tone_arc else ""
        if prev_mood:
            context_hint += f"Previous mood: {prev_mood}. "

        prompt = (
            f"Narration style: {self.style['desc']}.\n"
            f"{context_hint}"
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
                    "keep_alive": "15m",
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
            mood = str(s.get("mood", "neutral")).lower()
            if mood not in MOOD_PROSODY_MAP:
                mood = "neutral"
            seg.mood = mood

            # Map mood to rate/pitch deltas
            deltas = MOOD_PROSODY_MAP[mood]
            # If model supplied direct rate_delta/pitch_delta (legacy compatibility), use them
            r_delta = s.get("rate_delta", deltas["rate_delta"])
            p_delta = s.get("pitch_delta", deltas["pitch_delta"])

            seg.rate_delta = int(_clamp(r_delta, -15, 15)) + st["base_rate_delta"]
            seg.pitch_delta = int(_clamp(p_delta, -15, 15)) + st["base_pitch_delta"]
            seg.rate_delta = max(-st["max_rate_delta"] - 5, min(st["max_rate_delta"] + 5, seg.rate_delta))
            seg.pitch_delta = max(-st["max_pitch_delta"] - 5, min(st["max_pitch_delta"] + 5, seg.pitch_delta))

            # Map pause bucket to milliseconds
            pause_bucket = str(s.get("pause", "medium")).lower()
            if "pause_after_ms" in s:
                pause_ms = int(s["pause_after_ms"])
            else:
                pause_ms = PAUSE_BUCKET_MAP.get(pause_bucket, 450)
            seg.pause_after_ms = int(_clamp(pause_ms, 0, 1500))

            # emphasis words must appear in paragraph text
            lowered = p.text.lower()
            emph = []
            for w in (s.get("emphasis") or [])[:3]:
                w = str(w).strip().lower()
                if w and w in lowered and w not in emph:
                    emph.append(w)
            seg.emphasis = emph[: self.rules.max_emph + 1]
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
