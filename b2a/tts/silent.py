"""Silent backend: no voice at all, just timed silence per paragraph.

Purpose: end-to-end pipeline testing (CI, dry audio runs, duration estimates)
on machines with no TTS stack. ~60ms of audio per word.
"""
from __future__ import annotations

import wave
from pathlib import Path
from typing import List

from .base import BaseTTS, ChunkJob, ChunkResult

_MS_PER_WORD = 320


class SilentTTS(BaseTTS):
    name = "silent"

    def available(self) -> bool:
        return True

    def list_voices(self) -> List[dict]:
        return [{"name": "silence (test engine)", "engine": "silent",
                 "language": "en", "gender": "", "id": "silent"}]

    def synthesize_batch(self, jobs: List[ChunkJob], out_dir: Path) -> List[ChunkResult]:
        out_dir.mkdir(parents=True, exist_ok=True)
        results: List[ChunkResult] = []
        for job in jobs:
            wav_path = out_dir / f"{job.id}.wav"
            n_words = max(1, len(job.text.split()))
            duration_ms = min(n_words * _MS_PER_WORD, 120_000)
            try:
                with wave.open(str(wav_path), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(22050)
                    wf.writeframes(b"\x00\x00" * int(22050 * duration_ms / 1000))
                results.append(ChunkResult(id=job.id, ok=True, path=wav_path,
                                           bytes=wav_path.stat().st_size))
            except Exception as e:
                results.append(ChunkResult(id=job.id, ok=False, path=wav_path,
                                           error=str(e)[:300]))
        return results
