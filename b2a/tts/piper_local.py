"""Piper TTS backend — fully offline neural voices for any machine (CPU is fine).

Used on Linux/macOS, or on Windows when natural voices are not installed.
Piper speaks plain text (no SSML), so global speed is handled with ffmpeg
atempo post-processing instead.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from typing import List

from .base import BaseTTS, ChunkJob, ChunkResult

log = logging.getLogger("b2a.tts.piper")


class PiperTTS(BaseTTS):
    name = "piper"

    def __init__(self, cfg, style_name: str = "warm"):
        super().__init__(cfg, style_name)
        self.voice_name = cfg.get_path("tts.piper.voice", "en_US-lessac-medium")
        self.data_dir = Path(cfg.get_path("tts.piper.data_dir", "models/piper"))
        self._voice = None

    # ---------- availability ----------
    def available(self) -> bool:
        try:
            import piper  # noqa: F401
            return True
        except Exception:
            return False

    def list_voices(self) -> List[dict]:
        return [{
            "name": self.voice_name,
            "engine": "piper",
            "language": self.voice_name.split("_")[1].split("-")[0] if "_" in self.voice_name else "en",
            "gender": "",
            "id": self.voice_name,
        }]

    @property
    def chosen_voice(self) -> str:
        return self.voice_name

    # ---------- model management ----------
    def _model_path(self) -> Path:
        return self.data_dir / f"{self.voice_name}.onnx"

    def ensure_model(self) -> bool:
        if self._model_path().exists():
            return True
        self.data_dir.mkdir(parents=True, exist_ok=True)
        log.info("Downloading Piper voice '%s' (one-time, ~60 MB) ...", self.voice_name)
        import time

        for attempt in (1, 2, 3):
            try:
                cmd = [sys.executable, "-m", "piper.download_voices", self.voice_name,
                       "--data-dir", str(self.data_dir)]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                if r.returncode == 0 and self._model_path().exists():
                    return True
                err = (r.stderr or r.stdout or "")[-200:].strip()
                if "429" in err or "Too Many Requests" in err:
                    wait = 20 * attempt
                    log.warning("Piper voice download rate-limited (HTTP 429), "
                                "retrying in %ds (%d/3) ...", wait, attempt)
                    time.sleep(wait)
                    continue
                log.error("piper.download_voices failed: %s", err)
                return False
            except Exception as e:
                log.error("Piper model download failed (attempt %d): %s", attempt, e)
                time.sleep(10)
        return False

    def _load(self):
        if self._voice is None:
            from piper import PiperVoice

            self._voice = PiperVoice.load(str(self._model_path()))
        return self._voice

    # ---------- synthesis ----------
    def synthesize_batch(self, jobs: List[ChunkJob], out_dir: Path) -> List[ChunkResult]:
        if not self.ensure_model():
            return [ChunkResult(id=j.id, ok=False, path=out_dir / f"{j.id}.wav",
                                error="piper model missing") for j in jobs]

        out_dir.mkdir(parents=True, exist_ok=True)
        voice = self._load()
        results: List[ChunkResult] = []
        for job in jobs:
            wav_path = out_dir / f"{job.id}.wav"
            try:
                # plain text — Piper has no markup; punctuation carries the prosody
                with wave.open(str(wav_path), "wb") as wf:
                    voice.synthesize_wav(job.text, wf)
                results.append(ChunkResult(id=job.id, ok=True, path=wav_path,
                                           bytes=wav_path.stat().st_size))
            except Exception as e:
                results.append(ChunkResult(id=job.id, ok=False, path=wav_path,
                                           error=str(e)[:300]))
        return results
