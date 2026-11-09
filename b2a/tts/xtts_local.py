"""XTTS v2 voice-cloning backend — zero-shot cloning, 100% local.

Use YOUR OWN voice to narrate the book:

  python book2audio.py convert book.pdf --engine xtts --clone-voice my_voice.wav

- Package:  pip install coqui-tts        (pulls in PyTorch, ~2.5 GB)
- Model:    tts_models/multilingual/multi-dataset/xtts_v2 (~1.8 GB, downloaded
            ONCE into tts.xtts.data_dir, then fully offline)
- Sample:   10-60 seconds of clean, natural reading is enough for a good clone;
            quality keeps improving up to a few minutes.
- License:  Coqui Public Model License — free for personal / family use
            (exactly this project's scope). Not for commercial use.

Prosody handling:
  XTTS speaks plain text (no SSML) but has a native `speed` control, so the
  global speed setting AND the per-paragraph rate_delta from the prosody
  plan (Ollama/rules) are applied through it. Pitch deltas are ignored —
  XTTS has no pitch parameter; the natural character of the reference clip
  carries the tone instead. Pauses are inserted at assembly time.
"""
from __future__ import annotations

import contextlib
import logging
import os
import wave
from pathlib import Path
from typing import List

from .base import BaseTTS, ChunkJob, ChunkResult

log = logging.getLogger("b2a.tts.xtts")

DEFAULT_MODEL = "tts_models/multilingual/multi-dataset/xtts_v2"


class XttsTTS(BaseTTS):
    name = "xtts"

    def __init__(self, cfg, style_name: str = "warm"):
        super().__init__(cfg, style_name)
        self.clone_wav = str(cfg.get_path("tts.xtts.clone_voice", "") or "")
        self.speaker = str(cfg.get_path("tts.xtts.speaker", "Claribel Dervla"))
        self.language = str(cfg.get_path("tts.xtts.language", "en"))
        self.model_name = str(cfg.get_path("tts.xtts.model", DEFAULT_MODEL))
        self.data_dir = Path(cfg.get_path("tts.xtts.data_dir", "models/xtts"))
        self._tts = None

    # ---------- availability ----------
    def available(self) -> bool:
        try:
            import TTS  # noqa: F401  (coqui-tts package)
            return True
        except Exception:
            return False

    def list_voices(self) -> List[dict]:
        if self.clone_wav:
            return [{"name": f"{Path(self.clone_wav).stem} (cloned)",
                     "engine": "xtts", "language": self.language,
                     "gender": "", "id": self.clone_wav}]
        return [{"name": f"{self.speaker} (built-in)", "engine": "xtts",
                 "language": self.language, "gender": "", "id": self.speaker}]

    @property
    def chosen_voice(self) -> str:
        if self.clone_wav:
            return f"{Path(self.clone_wav).stem} (cloned)"
        return self.speaker

    # ---------- speed mapping ----------
    def _speed_for(self, rate_delta: int) -> float:
        """Global speed scale + per-paragraph rate delta -> XTTS `speed`.

        speed 12 with 10%/unit -> +20% -> native speed 1.2.
        """
        base_pct = ((float(self.cfg.get_path("tts.speed", 10)) - 10.0)
                    * float(self.cfg.get_path("tts.rate_percent_per_speed_unit", 10)))
        total_pct = base_pct + float(rate_delta or 0)
        return max(0.5, min(2.0, 1.0 + total_pct / 100.0))

    # ---------- model management ----------
    def _find_model_paths(self):
        """Locate model_path and config_path from local data_dir or HF cache."""
        candidate = self.data_dir / "tts" / "tts_models--multilingual--multi-dataset--xtts_v2"
        if (candidate / "model.pth").exists():
            return str(candidate), str(candidate / "config.json")
        if (self.data_dir / "model.pth").exists():
            return str(self.data_dir), str(self.data_dir / "config.json")
        hf_cache = Path.home() / ".cache" / "huggingface" / "hub" / "models--tts-hub--XTTS-v2" / "snapshots"
        if hf_cache.exists():
            for snap in hf_cache.iterdir():
                if (snap / "model.pth").exists():
                    return str(snap), str(snap / "config.json")
        return None, None

    def _load(self):
        if self._tts is None:
            # Agree to the Coqui model license non-interactively and keep the
            # big model download inside the project's models/ dir.
            os.environ.setdefault("COQUI_TOS_AGREED", "1")
            os.environ.setdefault("TTS_HOME", str(self.data_dir.resolve()))

            # Patch torchaudio load/save to use soundfile directly, preventing
            # torchcodec DLL loading failures on systems without shared ffmpeg DLLs.
            try:
                import soundfile as sf
                import torch
                import torchaudio

                def _sf_load(uri, *args, **kwargs):
                    data, sr = sf.read(uri)
                    t = torch.from_numpy(data).float()
                    if t.ndim == 1:
                        t = t.unsqueeze(0)
                    elif t.ndim == 2:
                        t = t.t()
                    return t, sr

                def _sf_save(uri, src, sample_rate, channels_first=True, **kwargs):
                    if isinstance(src, torch.Tensor):
                        src = src.detach().cpu().numpy()
                    if channels_first and src.ndim == 2:
                        src = src.T
                    sf.write(uri, src, sample_rate)

                torchaudio.load = _sf_load
                torchaudio.save = _sf_save
            except Exception as e:
                log.debug("Soundfile torchaudio patch skipped: %s", e)

            from TTS.api import TTS  # type: ignore

            try:
                import torch  # noqa: F401
                log.info("Torch device: %s",
                         "GPU (cuda)" if torch.cuda.is_available() else "CPU (slower, still works)")
            except Exception:
                pass

            m_path, c_path = self._find_model_paths()
            try:
                if m_path and c_path:
                    log.info("Loading XTTS model from local snapshot '%s' ...", m_path)
                    self._tts = TTS(model_path=m_path, config_path=c_path)
                else:
                    log.info("Loading XTTS model '%s' (first run downloads ~1.8 GB, "
                             "then it is fully offline) ...", self.model_name)
                    self._tts = TTS(self.model_name)
            except TypeError:
                # Handle test stubs or custom wrappers that only accept model_name
                self._tts = TTS(self.model_name)
        return self._tts

    # ---------- cloning sample ----------
    @staticmethod
    def sample_duration_seconds(path) -> float:
        """Duration of a WAV clone sample in seconds (0.0 if unreadable)."""
        try:
            with contextlib.closing(wave.open(str(path), "rb")) as wf:
                return wf.getnframes() / float(wf.getframerate() or 1)
        except Exception:
            return 0.0

    def _check_sample(self) -> None:
        if not self.clone_wav:
            return
        sample_path = Path(self.clone_wav)
        if not sample_path.exists():
            raise FileNotFoundError(
                f"Clone voice sample not found: {self.clone_wav}")

        # Auto-convert m4a / mp3 / other audio containers to clean 22050Hz mono WAV
        if sample_path.suffix.lower() != ".wav":
            target_wav = sample_path.with_suffix(".wav")
            if not target_wav.exists() or target_wav.stat().st_mtime < sample_path.stat().st_mtime:
                import subprocess
                from ..audio import find_ffmpeg
                ffmpeg_exe = find_ffmpeg()
                log.info("Converting %s to %s for XTTS cloning ...", sample_path.name, target_wav.name)
                cmd = [ffmpeg_exe, "-y", "-hide_banner", "-loglevel", "error",
                       "-i", str(sample_path), "-ar", "22050", "-ac", "1", str(target_wav)]
                subprocess.run(cmd, check=True)
            self.clone_wav = str(target_wav)

        dur = self.sample_duration_seconds(self.clone_wav)
        if dur and dur < 6.0:
            log.warning("Clone sample is only %.1fs of speech — 10-60 s of clean "
                        "reading gives a much better clone.", dur)

    @staticmethod
    def _trim_trailing_silence(wav_path: Path, threshold_db: float = -40.0) -> None:
        """Trim trailing low-energy samples/breath intake from the synthesized WAV clip."""
        try:
            import soundfile as sf
            import numpy as np
            data, sr = sf.read(str(wav_path))
            if len(data) == 0:
                return
            abs_data = np.abs(data)
            max_val = np.max(abs_data)
            if max_val == 0:
                return
            thresh_val = max_val * (10 ** (threshold_db / 20.0))
            indices = np.where(abs_data > thresh_val)[0]
            if len(indices) > 0:
                # Keep 50ms smooth decay padding after speech to avoid abrupt cut
                pad = int(sr * 0.05)
                end_idx = min(len(data), indices[-1] + pad)
                trimmed = data[:end_idx]
                sf.write(str(wav_path), trimmed, sr)
        except Exception:
            pass

    # ---------- synthesis ----------
    def synthesize_batch(self, jobs: List[ChunkJob], out_dir: Path) -> List[ChunkResult]:
        self._check_sample()
        out_dir.mkdir(parents=True, exist_ok=True)
        tts = self._load()

        if self.clone_wav:
            speaker_kwargs = {"speaker_wav": str(self.clone_wav)}
        else:
            speaker_kwargs = {"speaker": self.speaker}

        results: List[ChunkResult] = []
        for job in jobs:
            wav_path = out_dir / f"{job.id}.wav"
            try:
                # plain text (XTTS has no SSML); punctuation + native speed carry prosody.
                # Lower temperature (0.65) and higher repetition penalty (2.5) cut babbling/stutter.
                try:
                    tts.tts_to_file(
                        text=job.text,
                        language=self.language,
                        file_path=str(wav_path),
                        speed=self._speed_for(job.rate_delta),
                        temperature=0.65,
                        repetition_penalty=2.5,
                        **speaker_kwargs,
                    )
                except TypeError:
                    tts.tts_to_file(
                        text=job.text,
                        language=self.language,
                        file_path=str(wav_path),
                        speed=self._speed_for(job.rate_delta),
                        **speaker_kwargs,
                    )

                # Post-process: Trim trailing vocoder breath/rumble (< -40 dB) from chunk
                self._trim_trailing_silence(wav_path)

                results.append(ChunkResult(id=job.id, ok=True, path=wav_path,
                                           bytes=wav_path.stat().st_size))
            except Exception as e:
                results.append(ChunkResult(id=job.id, ok=False, path=wav_path,
                                           error=str(e)[:300]))
        return results
