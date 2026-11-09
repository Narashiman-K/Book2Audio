"""Audio assembly: chunks -> chapter MP3 with pauses, loudness normalization
and ID3 metadata. ffmpeg does the heavy lifting; pydub stitches.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from pydub import AudioSegment

log = logging.getLogger("b2a.audio")


def _is_working_ffmpeg(path: str) -> bool:
    try:
        proc = subprocess.run([path, "-version"], capture_output=True, timeout=5)
        return proc.returncode == 0
    except Exception:
        return False


def find_ffmpeg_info(config_path: str = "") -> Tuple[str, str]:
    """Locate working ffmpeg binary and return (binary_path, source).
    Lookup order:
    1. Explicit config path (tools.ffmpeg / audio.ffmpeg_path / B2A_FFMPEG_PATH)
    2. System PATH
    3. Standard Program Files installations
    4. WinGet package directories
    """
    if config_path and Path(config_path).is_file() and _is_working_ffmpeg(config_path):
        return str(Path(config_path).resolve()), "config"

    env_cfg = os.environ.get("B2A_FFMPEG_PATH", "")
    if env_cfg and Path(env_cfg).is_file() and _is_working_ffmpeg(env_cfg):
        return str(Path(env_cfg).resolve()), "config (env)"

    found = shutil.which("ffmpeg")
    if found and _is_working_ffmpeg(found):
        return found, "PATH"

    prog_files = [os.environ.get("ProgramFiles", r"C:\Program Files"),
                  os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    for pf in prog_files:
        if pf:
            for rel in ["ffmpeg/bin/ffmpeg.exe", "ffmpeg/ffmpeg.exe", "ShareX/ffmpeg.exe", "Gyan/ffmpeg.exe"]:
                cand = Path(pf) / rel
                if cand.is_file() and _is_working_ffmpeg(str(cand)):
                    bin_dir = str(cand.parent)
                    if bin_dir not in os.environ.get("PATH", ""):
                        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
                    return str(cand), "Program Files"

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        winget_pkgs = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if winget_pkgs.exists():
            for p in winget_pkgs.glob("**/ffmpeg.exe"):
                if p.is_file() and _is_working_ffmpeg(str(p)):
                    bin_dir = str(p.parent)
                    if bin_dir not in os.environ.get("PATH", ""):
                        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
                    return str(p), "winget"

    return "ffmpeg", "system default"


def find_ffmpeg(config_path: str = "") -> str:
    path, _ = find_ffmpeg_info(config_path)
    return path


def find_ffprobe() -> str:
    found = shutil.which("ffprobe")
    if found:
        return found
    ffmpeg = find_ffmpeg()
    if ffmpeg and ffmpeg != "ffmpeg":
        cand = Path(ffmpeg).parent / "ffprobe.exe"
        if cand.exists():
            return str(cand)
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        winget_pkgs = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if winget_pkgs.exists():
            for p in winget_pkgs.glob("**/ffprobe.exe"):
                if p.is_file():
                    return str(p)
    return "ffprobe"


def ffmpeg_available() -> bool:
    exe = find_ffmpeg()
    return shutil.which(exe) is not None or Path(exe).exists()


def _run_ffmpeg(args: List[str], timeout: int = 900) -> None:
    cmd = [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y"] + args
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        err_msg = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"ffmpeg failed (rc={proc.returncode}): {err_msg[-1000:]}")


def assemble_chapter(
    chunks: List[Tuple[Path, int]],   # (audio file, pause_after_ms)
    out_mp3: Path,
    title: str,
    album: str,
    artist: str,
    track: int,
    total_tracks: int,
    lead_silence_ms: int = 1200,
    target_lufs: float = -19.0,
    bitrate: str = "96k",
    tempo: float = 1.0,
    progress_cb=None,
    out_wav: Optional[Path] = None,
) -> float:
    """Stitch paragraph audio into one normalized chapter MP3 (and optional master WAV).

    Performs two-pass EBU R128 loudness normalization for consistent volume across chapters.
    Returns the chapter duration in seconds.
    """
    if not chunks:
        raise ValueError("no chunks to assemble")

    ref_rate = None
    for path, _ in chunks:
        try:
            sample_seg = AudioSegment.from_file(str(path), format="wav")
            ref_rate = sample_seg.frame_rate
            break
        except Exception:
            try:
                sample_seg = AudioSegment.from_file(str(path))
                ref_rate = sample_seg.frame_rate
                break
            except Exception:
                continue
    if ref_rate is None:
        ref_rate = 24000

    chapter = AudioSegment.silent(duration=lead_silence_ms, frame_rate=ref_rate)

    for i, (path, pause_ms) in enumerate(chunks):
        try:
            seg = AudioSegment.from_file(str(path), format="wav")
        except Exception:
            try:
                seg = AudioSegment.from_file(str(path))
            except Exception as e:
                log.warning("chunk %s unreadable (%s) — skipped", path.name, e)
                continue
        if ref_rate is None:
            ref_rate = seg.frame_rate
        seg = seg.set_frame_rate(ref_rate).set_channels(1)
        chapter += seg
        if pause_ms > 0:
            chapter += AudioSegment.silent(duration=pause_ms, frame_rate=ref_rate)
        if progress_cb and (i % 25 == 0 or i == len(chunks) - 1):
            progress_cb(i + 1, len(chunks))

    with tempfile.TemporaryDirectory() as td:
        tmp_wav = Path(td) / "chapter_raw.wav"
        chapter.export(str(tmp_wav), format="wav")

        # Two-pass loudness normalization:
        # Pass 1: Measure input integrated loudness, true peak, and dynamic range
        filters = ["highpass=f=90", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"]
        try:
            pass1_cmd = [
                find_ffmpeg(), "-hide_banner", "-y",
                "-i", str(tmp_wav),
                "-af", f"highpass=f=90,loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=json",
                "-f", "null", "-",
            ]
            p1 = subprocess.run(pass1_cmd, capture_output=True, text=True, timeout=300)
            m = re.search(r"\{\s*\"input_i\"[\s\S]*?\}", p1.stderr or p1.stdout or "")
            if m:
                s = json.loads(m.group(0))
                # Validate that measured stats are finite numbers (failsafe for silent test audio)
                valid = True
                for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
                    v = float(s.get(k, 0))
                    if math.isinf(v) or math.isnan(v):
                        valid = False
                        break
                if valid and -99.0 <= float(s.get("input_i", 0)) <= 0.0:
                    filters = [
                        "highpass=f=90",
                        f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:"
                        f"measured_I={s.get('input_i', -24)}:"
                        f"measured_TP={s.get('input_tp', -2)}:"
                        f"measured_LRA={s.get('input_lra', 7)}:"
                        f"measured_thresh={s.get('input_thresh', -34)}:"
                        f"offset={s.get('target_offset', 0)}:linear=true",
                    ]
        except Exception as e:
            log.debug("Loudnorm pass 1 failed (%s) — using single-pass filter", e)

        if tempo and abs(tempo - 1.0) > 0.01:
            t = max(0.5, min(2.0, tempo))
            filters.append(f"atempo={t}")

        meta = [
            "-metadata", f"title={title}",
            "-metadata", f"album={album}",
            "-metadata", f"artist={artist}",
            "-metadata", f"track={track}/{total_tracks}",
            "-metadata", "comment=Made with book2audio - personal/family use only",
        ]

        if out_wav:
            out_wav.parent.mkdir(parents=True, exist_ok=True)
            _run_ffmpeg([
                "-i", str(tmp_wav),
                "-af", ",".join(filters),
                "-ar", str(ref_rate), "-ac", "1",
                str(out_wav),
            ])
            # Encode MP3 directly from the pristine normalized WAV
            _run_ffmpeg([
                "-i", str(out_wav),
                "-codec:a", "libmp3lame", "-b:a", bitrate,
                *meta,
                str(out_mp3),
            ])
        else:
            _run_ffmpeg([
                "-i", str(tmp_wav),
                "-af", ",".join(filters),
                "-ar", str(ref_rate), "-ac", "1",
                "-codec:a", "libmp3lame", "-b:a", bitrate,
                *meta,
                str(out_mp3),
            ])

    return len(chapter) / 1000.0


def assemble_m4b(
    chapter_files: List[dict],
    out_m4b: Path,
    book_title: str,
    author: str,
    bitrate: str = "96k",
    cover_image: Optional[Path] = None,
) -> Path:
    """Concatenate chapter audio into a single chaptered .m4b (AAC) container.

    Prefers lossless chapter WAV files when available to avoid MP3 transcoding degradation.
    Optionally embeds cover artwork.
    """
    if not chapter_files:
        raise ValueError("no chapters to assemble into M4B")

    with tempfile.TemporaryDirectory() as td:
        concat_list = Path(td) / "concat.txt"
        meta_file = Path(td) / "metadata.txt"

        lines = []
        for ch in chapter_files:
            # Prefer pristine WAV if it exists, fallback to MP3
            src = ch.get("wav_file") or ch.get("file")
            if not src or not Path(src).exists():
                src = ch.get("file")
            p = Path(src).resolve().as_posix()
            lines.append(f"file '{p}'")
        concat_list.write_text("\n".join(lines), encoding="utf-8")

        meta_lines = [
            ";FFMETADATA1",
            f"title={book_title}",
            f"artist={author}",
            f"album={book_title}",
            "genre=Audiobook",
            "comment=Made with book2audio - personal/family use only",
            "",
        ]

        current_ms = 0
        for ch in chapter_files:
            dur_ms = int(round(float(ch.get("seconds") or 0.0) * 1000))
            end_ms = current_ms + dur_ms
            clean_title = ch.get("title") or f"Chapter {ch.get('chapter', '')}"
            meta_lines.extend([
                "[CHAPTER]",
                "TIMEBASE=1/1000",
                f"START={current_ms}",
                f"END={end_ms}",
                f"title={clean_title}",
                "",
            ])
            current_ms = end_ms

        meta_file.write_text("\n".join(meta_lines), encoding="utf-8")

        cmd = [
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-i", str(meta_file),
        ]

        if cover_image and Path(cover_image).is_file():
            cmd.extend(["-i", str(Path(cover_image).resolve())])
            cmd.extend([
                "-map", "0:a",
                "-map", "2:v",
                "-c:v", "copy",
                "-disposition:v:0", "attached_pic",
            ])
        else:
            cmd.extend(["-map", "0:a", "-vn"])

        cmd.extend([
            "-map_metadata", "1",
            "-c:a", "aac",
            "-b:a", bitrate,
            str(out_m4b),
        ])

        _run_ffmpeg(cmd)

    return out_m4b


def probe_duration(path: Path) -> float:
    """Chapter duration in seconds via ffprobe or ffmpeg."""
    ffprobe = find_ffprobe()
    if ffprobe and (shutil.which(ffprobe) or Path(ffprobe).exists()):
        try:
            r = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, timeout=60)
            val = float(r.stdout.strip() or 0.0)
            if val > 0:
                return val
        except Exception:
            pass
    try:
        ffmpeg_bin = find_ffmpeg()
        r = subprocess.run([ffmpeg_bin, "-i", str(path)], capture_output=True, text=True, timeout=30)
        import re
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", r.stderr or r.stdout or "")
        if m:
            hours, mins, secs = float(m.group(1)), float(m.group(2)), float(m.group(3))
            return hours * 3600 + mins * 60 + secs
    except Exception:
        pass
    return 0.0


def human_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s"
