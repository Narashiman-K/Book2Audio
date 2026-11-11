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


def _replace_file(src: Path, dst: Path) -> None:
    """os.replace with retries: on Windows a player or browser may still hold the
    old file open for a moment."""
    import time
    for attempt in range(8):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == 7:
                raise RuntimeError(f"{dst.name} is in use by another program (a player?). "
                                   "Close it and try again.")
            time.sleep(1.0)


def _ffmeta_escape(value: str) -> str:
    """Escape a value for an FFMETADATA1 file (= ; # \\ and newline)."""
    value = (value or "").replace("\r", " ")
    return re.sub(r"([=;#\\\n])", r"\\\1", value)


def _concat_line(path: Path) -> str:
    p = Path(path).resolve().as_posix().replace("'", "'\\''")
    return f"file '{p}'"


def prepare_cover_jpeg(src: Path, dst: Path, max_px: int = 1400) -> Optional[Path]:
    """Normalise any cover image (jpg/png/webp/gif) to a baseline JPEG that
    every audiobook player accepts. Returns None if the image is unusable."""
    try:
        _run_ffmpeg([
            "-i", str(src),
            "-vf", f"scale='min({max_px},iw)':-2",
            "-frames:v", "1", "-q:v", "2", "-pix_fmt", "yuvj420p",
            str(dst),
        ], timeout=60)
        return dst if dst.is_file() and dst.stat().st_size > 0 else None
    except Exception as e:
        log.warning("cover image unusable (%s): %s", src, e)
        return None


def _meta_header(meta: dict) -> List[str]:
    """Global FFMETADATA lines from a metadata dict.

    Keys: title, author, narrator, album, year, genre, description, publisher.
    Narrator goes in 'composer' (the convention Apple Books, Audiobookshelf and
    VLC read); description goes in both 'description' and 'comment'.
    """
    title = meta.get("title") or "Audiobook"
    author = meta.get("author") or ""
    lines = [
        ";FFMETADATA1",
        f"title={_ffmeta_escape(title)}",
        f"artist={_ffmeta_escape(author)}",
        f"album_artist={_ffmeta_escape(author)}",
        f"album={_ffmeta_escape(meta.get('album') or title)}",
        f"genre={_ffmeta_escape(meta.get('genre') or 'Audiobook')}",
        "media_type=2",
    ]
    if meta.get("narrator"):
        lines.append(f"composer={_ffmeta_escape(meta['narrator'])}")
    if meta.get("year"):
        lines.append(f"date={_ffmeta_escape(str(meta['year']))}")
    if meta.get("publisher"):
        lines.append(f"publisher={_ffmeta_escape(meta['publisher'])}")
    desc = meta.get("description") or "Made with book2audio - personal/family use only"
    lines.append(f"description={_ffmeta_escape(desc)}")
    lines.append(f"comment={_ffmeta_escape(desc)}")
    return lines


def _chapter_lines(marks: List[dict]) -> List[str]:
    out: List[str] = []
    for m in marks:
        out += ["[CHAPTER]", "TIMEBASE=1/1000",
                f"START={int(round(m['start'] * 1000))}",
                f"END={int(round(m['end'] * 1000))}",
                f"title={_ffmeta_escape(m['title'])}", ""]
    return out


def compute_chapter_marks(chapter_files: List[dict]) -> Tuple[List[dict], List[Path]]:
    """Chapter start/end in seconds, measured from the exact files that will be
    concatenated (never from the MP3 duration, which differs by encoder padding
    and made chapter markers drift)."""
    use_wav = all(ch.get("wav_file") and Path(ch["wav_file"]).is_file() for ch in chapter_files)
    marks: List[dict] = []
    sources: List[Path] = []
    cur = 0.0
    for ch in chapter_files:
        src = Path(ch["wav_file"] if use_wav else ch["file"])
        dur = probe_duration(src)
        if dur <= 0:
            raise RuntimeError(f"cannot read duration of {src.name}")
        title = ch.get("title") or f"Chapter {ch.get('chapter', '')}"
        marks.append({"index": ch.get("chapter"), "title": re.sub(r"<[^>]+>", "", title).strip(),
                      "start": round(cur, 3), "end": round(cur + dur, 3)})
        sources.append(src)
        cur += dur
    return marks, sources


def assemble_m4b(
    chapter_files: List[dict],
    out_m4b: Path,
    book_title: str = "",
    author: str = "",
    bitrate: str = "96k",
    cover_image: Optional[Path] = None,
    meta: Optional[dict] = None,
    marks_out: Optional[list] = None,
    progress_cb=None,
) -> Path:
    """Concatenate chapter audio into one chaptered .m4b (AAC in an ipod/MP4 muxer).

    - chapter markers are measured from the real source files
    - cover art is normalised to JPEG and attached
    - tags: title, author, narrator (composer), year, genre, description
    - written to a temp file and moved into place, so a failed or cancelled
      build never leaves a half-written .m4b behind
    """
    if not chapter_files:
        raise ValueError("no chapters to assemble into M4B")

    meta = dict(meta or {})
    meta.setdefault("title", book_title)
    meta.setdefault("author", author)

    marks, sources = compute_chapter_marks(chapter_files)
    if marks_out is not None:
        marks_out[:] = marks
    total = marks[-1]["end"]

    out_m4b = Path(out_m4b)
    out_m4b.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = out_m4b.with_name(out_m4b.stem + ".partial.m4b")

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "concat.txt").write_text("\n".join(_concat_line(p) for p in sources), encoding="utf-8")
        (td / "meta.txt").write_text("\n".join(_meta_header(meta) + [""] + _chapter_lines(marks)),
                                     encoding="utf-8")
        cover_jpg = None
        if cover_image and Path(cover_image).is_file():
            cover_jpg = prepare_cover_jpeg(Path(cover_image), td / "cover.jpg")

        args = ["-f", "concat", "-safe", "0", "-i", str(td / "concat.txt"),
                "-i", str(td / "meta.txt")]
        if cover_jpg:
            args += ["-i", str(cover_jpg), "-map", "0:a", "-map", "2:v", "-c:v", "copy",
                     "-disposition:v:0", "attached_pic"]
        else:
            args += ["-map", "0:a", "-vn"]
        args += ["-map_metadata", "1", "-map_chapters", "1",
                 "-c:a", "aac", "-b:a", bitrate, "-ac", "1",
                 "-metadata:s:a:0", "language=eng",
                 "-movflags", "+faststart", "-f", "ipod", str(tmp_out)]

        cmd = [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostats",
               "-progress", "pipe:1", "-y"] + args
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, bufsize=1)
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                if line.startswith("out_time_us=") and progress_cb and total > 0:
                    try:
                        progress_cb(min(1.0, int(line.split("=")[1]) / 1e6 / total))
                    except ValueError:
                        pass
            err = proc.stderr.read()  # type: ignore[union-attr]
            rc = proc.wait(timeout=60)
        except Exception:
            proc.kill()
            tmp_out.unlink(missing_ok=True)
            raise
        if rc != 0 or not tmp_out.is_file() or tmp_out.stat().st_size == 0:
            tmp_out.unlink(missing_ok=True)
            raise RuntimeError(f"ffmpeg could not build the M4B (rc={rc}): {(err or '').strip()[-800:]}")

    _replace_file(tmp_out, out_m4b)
    return out_m4b


def retag_m4b(m4b: Path, meta: dict, marks: List[dict], cover_image: Optional[Path] = None) -> Path:
    """Rewrite tags, chapter titles and cover on an existing .m4b WITHOUT
    re-encoding (stream copy, takes a second or two)."""
    m4b = Path(m4b)
    tmp_out = m4b.with_name(m4b.stem + ".retag.m4b")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "meta.txt").write_text("\n".join(_meta_header(meta) + [""] + _chapter_lines(marks)),
                                     encoding="utf-8")
        cover_jpg = None
        if cover_image and Path(cover_image).is_file():
            cover_jpg = prepare_cover_jpeg(Path(cover_image), td / "cover.jpg")
        args = ["-i", str(m4b), "-i", str(td / "meta.txt")]
        if cover_jpg:
            args += ["-i", str(cover_jpg), "-map", "0:a", "-map", "2:v", "-c:v", "copy",
                     "-disposition:v:0", "attached_pic"]
        else:
            args += ["-map", "0:a", "-map", "0:v?", "-c:v", "copy"]  # keep existing cover
        args += ["-map_metadata", "1", "-map_chapters", "1", "-c:a", "copy",
                 "-movflags", "+faststart", "-f", "ipod", str(tmp_out)]
        try:
            _run_ffmpeg(args, timeout=600)
        except Exception:
            tmp_out.unlink(missing_ok=True)
            raise
    _replace_file(tmp_out, m4b)
    return m4b


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
