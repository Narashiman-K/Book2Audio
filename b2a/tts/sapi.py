"""Legacy SAPI desktop voices (David/Zira) via System.Speech + PowerShell.

Third-choice engine: robotic compared to Ava, but present on every Windows
machine and fully offline. Supports SSML prosody via SpeakSsml().
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import List

from .base import BaseTTS, ChunkJob, ChunkResult

log = logging.getLogger("b2a.tts.sapi")

_PS_SCRIPT = Path(__file__).resolve().parent / "sapi.ps1"


class SapiTTS(BaseTTS):
    name = "sapi"

    def available(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            return bool(self.list_voices())
        except Exception as e:
            log.debug("sapi probe failed: %s", e)
            return False

    def list_voices(self) -> List[dict]:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            status = Path(td) / "status.json"
            SapiTTS._run_powershell(["-ListVoices", "-StatusFile", str(status)], timeout=60)
            if not status.exists():
                raise RuntimeError("PowerShell returned no status file")
            data = json.loads(status.read_text(encoding="utf-8-sig"))
        return data.get("voices", [])

    def synthesize_batch(self, jobs: List[ChunkJob], out_dir: Path) -> List[ChunkResult]:
        out_dir.mkdir(parents=True, exist_ok=True)
        ssml_dir = out_dir / "_ssml"
        ssml_dir.mkdir(exist_ok=True)

        job_manifest = {"out_ext": ".wav", "jobs": []}
        for job in jobs:
            ssml_path = ssml_dir / f"{job.id}.xml"
            ssml_path.write_text(job.ssml, encoding="utf-8")
            job_manifest["jobs"].append({"id": job.id, "ssml_path": str(ssml_path)})

        job_file = ssml_dir / "jobs.json"
        job_file.write_text(json.dumps(job_manifest, ensure_ascii=False), encoding="utf-8")
        status_file = ssml_dir / "status.json"
        if status_file.exists():
            status_file.unlink()

        SapiTTS._run_powershell(
            ["-JobFile", str(job_file), "-OutDir", str(out_dir),
             "-VoiceName", str(self.chosen_voice), "-StatusFile", str(status_file)],
            timeout=max(300, 30 * len(jobs)),
        )

        results: List[ChunkResult] = []
        status: dict = {}
        if status_file.exists():
            status = json.loads(status_file.read_text(encoding="utf-8-sig"))
        by_id = {r.get("id"): r for r in status.get("results", [])}

        for job in jobs:
            audio_path = out_dir / f"{job.id}.wav"
            st = by_id.get(job.id, {})
            if st.get("ok") and audio_path.exists() and audio_path.stat().st_size > 0:
                results.append(ChunkResult(id=job.id, ok=True, path=audio_path,
                                           bytes=st.get("bytes", 0)))
            else:
                results.append(ChunkResult(id=job.id, ok=False, path=audio_path,
                                           error=str(st.get("error") or "no status entry")))
        return results

    @staticmethod
    def _run_powershell(args: List[str], timeout: int = 300) -> str:
        cmd = ["powershell", "-NoProfile", "-NonInteractive",
               "-ExecutionPolicy", "Bypass", "-File", str(_PS_SCRIPT)] + [str(a) for a in args]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(
                f"PowerShell SAPI failed (rc={proc.returncode}): {proc.stderr[-500:]}")
        return proc.stdout
