"""Kokoro-82M TTS engine backend for book2audio.

Uses kokoro-onnx for fast, high-fidelity neural speech synthesis on CPU.
Voices include:
  af_heart (warm, natural female audiobook narrator — recommended)
  af_bella, af_nicole, af_sarah, af_sky
  am_michael, am_adam (clear, natural male narrators)
  bf_emma, bf_isabella (British female)
  bm_george, bm_lewis (British male)
"""
from __future__ import annotations

import atexit
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from .base import BaseTTS, ChunkJob, ChunkResult

logger = logging.getLogger("b2a.tts.kokoro")

# Suppress Windows espeak DLL cleanup harmless error on exit
def _silence_espeak_atexit():
    for handler in list(atexit._ncallbacks()):  # type: ignore
        try:
            target = getattr(handler, "__self__", None)
            if target and "EspeakAPI" in type(target).__name__:
                atexit.unregister(handler)
        except Exception:
            pass


class KokoroTTS(BaseTTS):
    name = "kokoro"

    def __init__(self, cfg, style_name: str = "warm"):
        super().__init__(cfg, style_name)
        self._kokoro = None
        self._model_path: Optional[Path] = None
        self._voices_path: Optional[Path] = None
        self._resolve_paths()

    def _resolve_paths(self) -> None:
        cfg_model = self.cfg.get_path("tts.kokoro.model_path", "")
        cfg_voices = self.cfg.get_path("tts.kokoro.voices_path", "")

        default_dir = Path(__file__).resolve().parent.parent.parent / "models" / "kokoro"

        if cfg_model and Path(cfg_model).is_file():
            self._model_path = Path(cfg_model)
        else:
            candidates = [
                default_dir / "kokoro-v1.0.onnx",
                default_dir / "kokoro-v0_19.onnx",
                default_dir / "kokoro-v1.0.int8.onnx",
            ]
            for c in candidates:
                if c.is_file():
                    self._model_path = c
                    break

        if cfg_voices and Path(cfg_voices).is_file():
            self._voices_path = Path(cfg_voices)
        else:
            candidates = [
                default_dir / "voices-v1.0.bin",
                default_dir / "voices.bin",
                default_dir / "voices.json",
            ]
            for c in candidates:
                if c.is_file():
                    self._voices_path = c
                    break

    def available(self) -> bool:
        """Available if kokoro-onnx is installed and model files exist."""
        try:
            import kokoro_onnx  # noqa: F401
            import onnxruntime  # noqa: F401
            import soundfile  # noqa: F401
        except ImportError:
            return False

        self._resolve_paths()
        return bool(self._model_path and self._model_path.is_file() and
                    self._voices_path and self._voices_path.is_file())

    def _get_kokoro(self):
        if self._kokoro is None:
            from kokoro_onnx import Kokoro, MAX_PHONEME_LENGTH, SAMPLE_RATE

            # Patch _create_audio to pass speed as float32 to prevent ONNX type mismatch
            def patched_create_audio(inst, phonemes: str, voice, speed: float):
                phonemes = phonemes[:MAX_PHONEME_LENGTH]
                tokens = np.array(inst.tokenizer.tokenize(phonemes), dtype=np.int64)
                voice_slice = voice[len(tokens)]
                tokens_feed = [[0, *tokens, 0]]
                if "input_ids" in [i.name for i in inst.sess.get_inputs()]:
                    inputs = {
                        "input_ids": tokens_feed,
                        "style": np.array(voice_slice, dtype=np.float32),
                        "speed": np.array([speed], dtype=np.float32),
                    }
                else:
                    inputs = {
                        "tokens": tokens_feed,
                        "style": voice_slice,
                        "speed": np.ones(1, dtype=np.float32) * speed,
                    }
                audio = inst.sess.run(None, inputs)[0]
                return audio, SAMPLE_RATE

            Kokoro._create_audio = patched_create_audio

            if not (self._model_path and self._voices_path):
                raise RuntimeError("Kokoro model or voices file not found.")
            logger.info("Loading Kokoro ONNX model from %s", self._model_path)
            self._kokoro = Kokoro(str(self._model_path), str(self._voices_path))
            try:
                _silence_espeak_atexit()
            except Exception:
                pass
        return self._kokoro

    def list_voices(self) -> List[dict]:
        if not self.available():
            return []
        try:
            kokoro = self._get_kokoro()
            names = kokoro.get_voices()
            results = []
            for n in names:
                gender = "female" if n.startswith(("af_", "bf_", "zf_")) else "male"
                lang = "en-gb" if n.startswith(("bf_", "bm_")) else "en-us"
                desc = "audiobook narrator" if n in ("af_heart", "am_michael", "am_adam") else "neural"
                results.append({
                    "name": n,
                    "language": lang,
                    "gender": gender,
                    "engine": "kokoro",
                    "description": desc,
                })
            return results
        except Exception as e:
            logger.warning("Failed to list Kokoro voices: %s", e)
            return []

    def synthesize_batch(self, jobs: List[ChunkJob], out_dir: Path) -> List[ChunkResult]:
        import soundfile as sf

        out_dir.mkdir(parents=True, exist_ok=True)
        kokoro = self._get_kokoro()
        voice = self.chosen_voice
        base_speed = float(self.cfg.get_path("tts.speed", 1.0))

        known_voices = kokoro.get_voices()
        if voice not in known_voices:
            if "heart" in voice.lower():
                voice = "af_heart"
            elif "michael" in voice.lower():
                voice = "am_michael"
            elif "adam" in voice.lower():
                voice = "am_adam"
            elif "bella" in voice.lower():
                voice = "af_bella"
            elif "emma" in voice.lower():
                voice = "bf_emma"
            else:
                voice = "af_heart" if "af_heart" in known_voices else known_voices[0]

        results: List[ChunkResult] = []
        tag_re = re.compile(r"<[^>]+>")

        for job in jobs:
            out_file = out_dir / f"{job.id}.wav"
            if out_file.exists() and out_file.stat().st_size > 1000:
                results.append(ChunkResult(id=job.id, ok=True, path=out_file, bytes=out_file.stat().st_size))
                continue

            try:
                rate_multiplier = 1.0 + (job.rate_delta / 100.0)
                speed = max(0.5, min(2.0, base_speed * rate_multiplier))

                # Strip XML / SSML tags so neural model reads clean natural prose
                clean_text = tag_re.sub("", job.text).strip()
                if not clean_text:
                    clean_text = "..."

                samples, sample_rate = kokoro.create(clean_text, voice=voice, speed=speed)
                sf.write(str(out_file), samples, sample_rate)
                results.append(ChunkResult(id=job.id, ok=True, path=out_file, bytes=out_file.stat().st_size))
            except Exception as e:
                logger.error("Failed synthesizing chunk %s with Kokoro: %s", job.id, e)
                results.append(ChunkResult(id=job.id, ok=False, path=out_file, error=str(e)))

        return results

    @property
    def chosen_voice(self) -> str:
        return self.cfg.get_path("tts.voice", "af_heart")
