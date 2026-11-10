"""book2audio Local Web Studio UI server and speed-reader API."""
from __future__ import annotations

import email.policy
import io
import json
import logging
import mimetypes
import os
import re
import shutil
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from email.parser import BytesParser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from .config import Config, load_config
from .extract import extract_book, has_identifiable_chapters
from .models import Book, Chapter, Paragraph, ProsodySegment
from .tts.base import ChunkJob
from .pipeline import Pipeline, slugify

log = logging.getLogger("b2a.studio")

_PROGRESS_LOCK = threading.Lock()
_PROGRESS_STATE = {
    "active": False,
    "percent": 0,
    "message": "Idle",
    "chapter": 1,
    "audioUrl": "",
    "m4bUrl": "",
}


def _update_progress(percent: int, message: str, active: bool = True, audio_url: str = "", m4b_url: str = ""):
    with _PROGRESS_LOCK:
        _PROGRESS_STATE["active"] = active
        _PROGRESS_STATE["percent"] = percent
        _PROGRESS_STATE["message"] = message
        if audio_url:
            _PROGRESS_STATE["audioUrl"] = audio_url
        if m4b_url:
            _PROGRESS_STATE["m4bUrl"] = m4b_url


def _get_progress() -> dict:
    with _PROGRESS_LOCK:
        return dict(_PROGRESS_STATE)


def _clean_title(title: str) -> str:
    """Strip HTML tags and excess whitespace from chapter headings."""
    clean = re.sub(r"<[^>]+>", "", title)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean or "Chapter"


class StudioHandler(SimpleHTTPRequestHandler):
    """Custom HTTP request handler serving Studio Single Page App and REST APIs."""

    def __init__(self, *args, **kwargs):
        self.b2a_dir = Path(__file__).resolve().parent.parent
        self.workspace_dir = self.b2a_dir.parent
        self.web_dir = Path(__file__).resolve().parent / "web"
        super().__init__(*args, **kwargs)

    @property
    def books_library_dir(self) -> Path:
        """Authoritative Books-Library folder in the workspace root."""
        lib = self.workspace_dir / "Books-Library"
        lib.mkdir(parents=True, exist_ok=True)
        return lib

    def log_message(self, format, *args):
        if args and str(args[1]).startswith(("4", "5")):
            log.warning("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)
        else:
            log.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            return self._serve_index()
        elif path == "/api/status":
            return self._handle_status()
        elif path == "/api/voices":
            return self._handle_voices()
        elif path == "/api/books":
            return self._handle_books()
        elif path == "/api/book_details":
            return self._handle_book_details(query)
        elif path == "/api/chapter_content":
            return self._handle_chapter_content(query)
        elif path == "/api/progress":
            return self._handle_progress()
        elif path.startswith("/api/audio/"):
            return self._handle_audio_stream(path[len("/api/audio/"):])
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/audition":
            return self._handle_audition()
        elif path == "/api/synthesize":
            return self._handle_synthesize()
        elif path == "/api/upload":
            return self._handle_upload()
        elif path == "/api/read_paragraph":
            return self._handle_read_paragraph()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")

    # ------------------------------------------------------------------
    # HTTP Handlers
    # ------------------------------------------------------------------
    def _serve_index(self):
        index_file = self.web_dir / "index.html"
        if not index_file.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "index.html not found")
            return
        data = index_file.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(data)

    def _handle_status(self):
        cfg = load_config(self.b2a_dir / "config.yaml")
        res = {
            "ok": True,
            "engine": cfg.get_path("tts.engine", "kokoro"),
            "sample_rate": 24000,
            "speed": cfg.get_path("tts.kokoro.speed", 1.15),
            "pitch": cfg.get_path("tts.kokoro.pitch", 4),
            "bitrate": cfg.get_path("tts.mp3_bitrate", "320k"),
            "voices_total": 54,
        }
        self._send_json(res)

    def _handle_voices(self):
        voices = [
            {"id": "af_heart", "name": "af_heart", "category": "Kokoro American Female", "label": "af_heart (Warm & Studio - Recommended)", "gender": "Female"},
            {"id": "af_bella", "name": "af_bella", "category": "Kokoro American Female", "label": "af_bella (Dynamic Narrative)", "gender": "Female"},
            {"id": "af_nicole", "name": "af_nicole", "category": "Kokoro American Female", "label": "af_nicole (Calm & Reflective)", "gender": "Female"},
            {"id": "af_sarah", "name": "af_sarah", "category": "Kokoro American Female", "label": "af_sarah (Crisp Presentation)", "gender": "Female"},
            {"id": "af_sky", "name": "af_sky", "category": "Kokoro American Female", "label": "af_sky (Gentle)", "gender": "Female"},
            {"id": "am_michael", "name": "am_michael", "category": "Kokoro American Male", "label": "am_michael (Deep Narrative)", "gender": "Male"},
            {"id": "am_adam", "name": "am_adam", "category": "Kokoro American Male", "label": "am_adam (Conversational)", "gender": "Male"},
            {"id": "am_eric", "name": "am_eric", "category": "Kokoro American Male", "label": "am_eric (Energetic)", "gender": "Male"},
            {"id": "am_onyx", "name": "am_onyx", "category": "Kokoro American Male", "label": "am_onyx (Rich Baritone)", "gender": "Male"},
            {"id": "bf_emma", "name": "bf_emma", "category": "Kokoro British Female", "label": "bf_emma (British Female · Elegant)", "gender": "Female"},
            {"id": "bm_george", "name": "bm_george", "category": "Kokoro British Male", "label": "bm_george (British Male · Scholarly)", "gender": "Male"},
            {"id": "bm_lewis", "name": "bm_lewis", "category": "Kokoro British Male", "label": "bm_lewis (British Male · BBC Style)", "gender": "Male"},
            {"id": "AvaNatural", "name": "AvaNatural", "category": "Windows Natural (WinRT)", "label": "AvaNatural (Natural Neural)", "gender": "Female"},
            {"id": "JennyNatural", "name": "JennyNatural", "category": "Windows Natural (WinRT)", "label": "JennyNatural (Conversational)", "gender": "Female"},
            {"id": "GuyNatural", "name": "GuyNatural", "category": "Windows Natural (WinRT)", "label": "GuyNatural (Natural Male)", "gender": "Male"},
        ]
        self._send_json({"ok": True, "voices": voices})

    def _handle_books(self):
        """Scan Books-Library for candidate book documents."""
        books = []
        seen_paths = set()

        search_dirs = [
            self.books_library_dir,
            self.b2a_dir / "Books-Library",
        ]

        skip_names = {"requirements.txt", "requirements-dev.txt", "PLAN.md", "README.md", "CLAUDE.md", "AGENTS.md"}

        for sdir in search_dirs:
            if not sdir.exists():
                continue
            for p in sorted(sdir.glob("*.*")):
                if p.name in skip_names or p.name.startswith("."):
                    continue
                if p.suffix.lower() in (".epub", ".pdf", ".md", ".markdown", ".txt"):
                    resolved = str(p.resolve())
                    if resolved in seen_paths:
                        continue
                    seen_paths.add(resolved)

                    # Generate clean label
                    label = p.stem.replace("_", " ").replace("-", " ")
                    if len(label) > 60:
                        label = label[:57] + "..."

                    books.append({
                        "filename": p.name,
                        "label": f"{label} ({p.suffix.upper()})",
                        "path": str(p),
                        "size_mb": round(p.stat().st_size / (1024 * 1024), 2),
                    })

        self._send_json({"ok": True, "books": books})

    def _resolve_book_path(self, raw_path: str) -> Optional[Path]:
        if not raw_path:
            return None
        p = Path(raw_path)
        if p.is_file():
            return p
        # Check in Books-Library first
        for base in [self.books_library_dir, self.b2a_dir / "Books-Library", self.workspace_dir, self.b2a_dir]:
            if not base.exists():
                continue
            cand = base / raw_path
            if cand.is_file():
                return cand
            # Check by filename inside base
            cand_name = base / p.name
            if cand_name.is_file():
                return cand_name
            # Try by pattern match
            for found in base.glob(f"*{p.name}*"):
                if found.is_file():
                    return found
        return None

    def _handle_book_details(self, query: dict):
        raw_path = query.get("path", [""])[0]
        book_path = self._resolve_book_path(raw_path)

        if not book_path or not book_path.exists():
            return self._send_json({"ok": False, "error": f"Book not found: {raw_path}"})

        cfg = load_config(self.b2a_dir / "config.yaml")
        try:
            book = extract_book(book_path, cfg)
            has_real_chapters = has_identifiable_chapters(book.chapters)

            total_paras = sum(len(c.paragraphs) for c in book.chapters)
            total_words = sum(sum(len(p.text.split()) for p in c.paragraphs) for c in book.chapters)
            total_mins = max(1, round(total_words / 160))
            total_dur_str = f"{total_mins} min" if total_mins < 60 else f"{total_mins // 60}h {total_mins % 60}m"

            # Check if .m4b already exists for this book
            out_root = self.b2a_dir / "output_kokoro"
            book_slug = slugify(book.title)
            book_out_dir = out_root / book_slug
            m4b_url = ""
            if book_out_dir.exists():
                for cand in book_out_dir.glob("*.m4b"):
                    if cand.is_file():
                        rel = cand.relative_to(self.workspace_dir)
                        m4b_url = f"/api/audio/{rel.as_posix()}"
                        break
            if not m4b_url and out_root.exists():
                for cand in out_root.glob(f"*{book_slug}*.m4b"):
                    if cand.is_file():
                        rel = cand.relative_to(self.workspace_dir)
                        m4b_url = f"/api/audio/{rel.as_posix()}"
                        break

            # Collect all paragraphs across the entire book for seamless continuous reading
            all_paragraphs = []
            cur_sec = 0.5
            for c in book.chapters:
                for p in c.paragraphs:
                    words = len(p.text.split())
                    dur = max(1.5, words / 3.0)
                    p_start = cur_sec
                    p_end = cur_sec + dur
                    cur_sec = p_end + 0.4
                    all_paragraphs.append({
                        "id": p.id,
                        "text": p.text,
                        "start": round(p_start, 2),
                        "end": round(p_end, 2),
                    })

            self._send_json({
                "ok": True,
                "title": _clean_title(book.title),
                "author": _clean_title(book.author) or "Unknown Author",
                "filename": book_path.name,
                "path": str(book_path),
                "has_real_chapters": has_real_chapters,
                "has_m4b": bool(m4b_url),
                "m4b_url": m4b_url,
                "total_paragraphs": total_paras,
                "total_words": total_words,
                "total_duration": total_dur_str,
                "paragraphs": all_paragraphs,
            })
        except Exception as e:
            log.exception("Error extracting book details: %s", e)
            self._send_json({"ok": False, "error": str(e)})

    def _handle_chapter_content(self, query: dict):
        raw_path = query.get("path", [""])[0]
        chapter_idx = int(query.get("chapter", ["0"])[0])

        book_path = self._resolve_book_path(raw_path)

        # Fallback to first available book in Books-Library if path is empty or not found
        if not book_path or not book_path.exists():
            for cand_dir in [self.books_library_dir, self.b2a_dir / "Books-Library"]:
                if cand_dir.exists():
                    first_file = next((f for f in cand_dir.glob("*.*") if f.suffix.lower() in (".epub", ".pdf", ".md", ".markdown", ".txt")), None)
                    if first_file:
                        book_path = first_file
                        break

        if not book_path or not book_path.exists():
            return self._send_json({"ok": False, "error": "Book file not found"})

        cfg = load_config(self.b2a_dir / "config.yaml")
        try:
            book = extract_book(book_path, cfg)
            has_real = has_identifiable_chapters(book.chapters)

            # FULL BOOK MODE (Either requested chapter_idx == 0 or document has no real chapters)
            if chapter_idx == 0 or not has_real:
                all_paragraphs = []
                cur_sec = 0.5
                p_counter = 0
                for c in book.chapters:
                    for p in c.paragraphs:
                        words = len(p.text.split())
                        dur = max(1.5, words / 3.0)
                        p_start = cur_sec
                        p_end = cur_sec + dur
                        cur_sec = p_end + 0.4
                        all_paragraphs.append({
                            "id": p.id,
                            "text": p.text,
                            "start": round(p_start, 2),
                            "end": round(p_end, 2),
                        })
                        p_counter += 1

                # Look for full synthesized audiobook (.m4b or merged audio)
                audio_url = ""
                m4b_url = ""
                out_root = self.b2a_dir / "output_kokoro"
                book_slug = slugify(book.title)
                book_out_dir = out_root / book_slug

                if book_out_dir.exists():
                    for cand in book_out_dir.glob("*.m4b"):
                        if cand.is_file():
                            rel = cand.relative_to(self.workspace_dir)
                            audio_url = f"/api/audio/{rel.as_posix()}"
                            m4b_url = audio_url
                            break
                    if not audio_url:
                        for cand in sorted(book_out_dir.glob("*.mp3")):
                            if cand.is_file():
                                rel = cand.relative_to(self.workspace_dir)
                                audio_url = f"/api/audio/{rel.as_posix()}"
                                break

                if not audio_url and out_root.exists():
                    for cand in out_root.glob(f"*{book_slug}*.m4b"):
                        if cand.is_file():
                            rel = cand.relative_to(self.workspace_dir)
                            audio_url = f"/api/audio/{rel.as_posix()}"
                            m4b_url = audio_url
                            break

                return self._send_json({
                    "ok": True,
                    "book_title": _clean_title(book.title),
                    "chapter_index": 0,
                    "chapter_title": "📖 Entire Book (Full Text)",
                    "paragraphs": all_paragraphs,
                    "total_paragraphs": len(all_paragraphs),
                    "audio_url": audio_url,
                    "m4b_url": m4b_url,
                    "timeline": [],
                    "is_full_book": True,
                })

            # CHAPTER-SPECIFIC MODE
            target_ch = None
            for ch in book.chapters:
                if ch.index == chapter_idx:
                    target_ch = ch
                    break
            if not target_ch and book.chapters:
                target_ch = book.chapters[0]

            if not target_ch:
                return self._send_json({"ok": False, "error": "Chapter not found"})

            # Check if there is a generated timeline for synchronized speed-reading
            timeline_file = None
            for candidate_dir in [
                self.b2a_dir / "output_kokoro" / "_build",
                self.b2a_dir / "output_kokoro",
            ]:
                if candidate_dir.exists():
                    for t_path in candidate_dir.glob(f"**/timeline_c{target_ch.index:02d}.json"):
                        if t_path.exists():
                            timeline_file = t_path
                            break

            timeline_data = []
            if timeline_file and timeline_file.exists():
                try:
                    timeline_data = json.loads(timeline_file.read_text(encoding="utf-8"))
                except Exception:
                    pass

            timeline_by_para = {t.get("para_id"): t for t in timeline_data}
            paragraphs = []
            current_sec = 0.5
            for idx, p in enumerate(target_ch.paragraphs):
                cached_time = timeline_by_para.get(p.id)
                if cached_time:
                    p_start = cached_time.get("start", current_sec)
                    p_end = cached_time.get("end", current_sec + 4.0)
                    current_sec = p_end + 0.4
                else:
                    words = len(p.text.split())
                    dur = max(1.5, words / 3.0)  # ~180 wpm
                    p_start = current_sec
                    p_end = current_sec + dur
                    current_sec = p_end + 0.4

                paragraphs.append({
                    "id": p.id,
                    "text": p.text,
                    "start": round(p_start, 2),
                    "end": round(p_end, 2),
                })

            # Search exclusively for real synthesized audio for this exact book and chapter
            audio_url = ""
            out_root = self.b2a_dir / "output_kokoro"
            ch_idx_str = f"{target_ch.index:02d}"
            book_slug = slugify(book.title)
            book_out_dir = out_root / book_slug

            if book_out_dir.exists():
                for cand in book_out_dir.glob(f"*{ch_idx_str}*.mp3"):
                    if cand.is_file():
                        rel = cand.relative_to(self.workspace_dir)
                        audio_url = f"/api/audio/{rel.as_posix()}"
                        break

            res = {
                "ok": True,
                "book_title": _clean_title(book.title),
                "chapter_index": target_ch.index,
                "chapter_title": _clean_title(target_ch.title),
                "paragraphs": paragraphs,
                "total_paragraphs": len(paragraphs),
                "audio_url": audio_url,
                "timeline": timeline_data,
            }
            return self._send_json(res)
        except Exception as e:
            log.exception("Error extracting chapter content: %s", e)
            return self._send_json({"ok": False, "error": str(e)})

    def _handle_progress(self):
        self._send_json(_get_progress())

    def _handle_audio_stream(self, relative_path: str):
        target_path = None
        decoded = urllib.parse.unquote(relative_path)

        for base in [self.workspace_dir, self.b2a_dir]:
            c = (base / decoded).resolve()
            if c.exists() and c.is_file():
                target_path = c
                break

        if not target_path or not target_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "Audio file not found")
            return

        file_size = target_path.stat().st_size
        range_header = self.headers.get("Range")

        mime_type, _ = mimetypes.guess_type(str(target_path))
        if target_path.suffix.lower() == ".m4b":
            mime_type = "audio/mp4"
        elif target_path.suffix.lower() == ".mp3":
            mime_type = "audio/mpeg"
        elif target_path.suffix.lower() in (".wav", ".wave"):
            mime_type = "audio/wav"
        elif not mime_type:
            mime_type = "audio/mp4"

        if range_header:
            m = re.match(r"bytes=(\d+)-(\d*)", range_header)
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else file_size - 1
                end = min(end, file_size - 1)
                length = end - start + 1

                self.send_response(HTTPStatus.PARTIAL_CONTENT)
                self.send_header("Content-Type", mime_type)
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()

                with open(target_path, "rb") as f:
                    f.seek(start)
                    self.wfile.write(f.read(length))
                return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(file_size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

        with open(target_path, "rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def _handle_audition(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        req = json.loads(body.decode("utf-8"))

        text = req.get("text", "Happiness is not something you inherit or choose.")
        voice = req.get("voice", "af_heart")
        speed = float(req.get("speed", 1.15))
        pitch = int(req.get("pitch", 4))

        cfg = load_config(self.b2a_dir / "config.yaml")
        cfg["tts"]["engine"] = "kokoro"
        cfg["tts"]["voice"] = voice
        cfg["tts"]["kokoro"]["voice"] = voice
        cfg["tts"]["kokoro"]["speed"] = speed
        cfg["tts"]["kokoro"]["pitch"] = pitch

        from .tts import create_engine
        engine = create_engine(cfg, prefer="kokoro")

        audition_dir = self.b2a_dir / "audition"
        audition_dir.mkdir(exist_ok=True)
        out_wav = audition_dir / "preview.wav"

        job = ChunkJob(id="preview", text=text, ssml=text)
        results = engine.synthesize_batch([job], audition_dir)
        res = results[0] if results else None

        if res and res.ok and out_wav.exists():
            self._send_json({"ok": True, "audioUrl": "/api/audio/audition/preview.wav"})
        else:
            self._send_json({"ok": False, "error": (res.error if res else "Audition generation failed") or "Audition generation failed"})

    def _handle_synthesize(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        req = json.loads(body.decode("utf-8"))

        raw_path = req.get("path", "")
        chapter_idx = int(req.get("chapter", 1))
        voice = req.get("voice", "af_heart")
        speed = float(req.get("speed", 1.15))
        pitch = int(req.get("pitch", 4))
        is_m4b = req.get("m4b", False) or chapter_idx == 0 or req.get("all_chapters", False)

        book_file = self._resolve_book_path(raw_path)
        if not book_file or not book_file.exists():
            return self._send_json({"ok": False, "error": f"Book file not found: {raw_path}"})

        def _worker():
            mode_desc = "full audiobook (.m4b)" if is_m4b else f"Chapter {chapter_idx}"
            _update_progress(10, f"Initializing Kokoro neural engine for {book_file.stem} ({mode_desc})...")
            cfg = load_config(self.b2a_dir / "config.yaml")
            cfg["tts"]["engine"] = "kokoro"
            cfg["tts"]["voice"] = voice
            cfg["tts"]["kokoro"]["voice"] = voice
            cfg["tts"]["kokoro"]["speed"] = speed
            cfg["tts"]["kokoro"]["pitch"] = pitch
            cfg["tts"]["mp3_bitrate"] = "320k"
            cfg["llm"]["provider"] = "off"

            from .tts import create_engine
            engine = create_engine(cfg, prefer="kokoro")
            out_root = self.b2a_dir / "output_kokoro"
            pipeline = Pipeline(cfg, engine, out_root)

            _update_progress(25, f"Synthesizing {mode_desc} with Kokoro/{voice}...")

            if is_m4b:
                manifest = pipeline.convert(
                    book_file,
                    force=True,
                    m4b=True,
                    chapters=None,
                    progress=lambda msg: _update_progress(55, msg)
                )
            else:
                manifest = pipeline.convert(
                    book_file,
                    force=True,
                    m4b=False,
                    chapters=[chapter_idx],
                    progress=lambda msg: _update_progress(60, msg)
                )

            audio_url = ""
            m4b_url = ""
            m4b_file = manifest.get("m4b_file")
            if m4b_file and Path(m4b_file).exists():
                try:
                    rel = Path(m4b_file).relative_to(self.workspace_dir)
                    m4b_url = f"/api/audio/{rel.as_posix()}"
                except Exception:
                    m4b_url = f"/api/audio/output_kokoro/{Path(m4b_file).name}"
                audio_url = m4b_url

            ch_files = manifest.get("chapters", [])
            if not audio_url and ch_files:
                out_mp3_path = ch_files[0].get("file")
                if out_mp3_path:
                    try:
                        rel = Path(out_mp3_path).relative_to(self.workspace_dir)
                        audio_url = f"/api/audio/{rel.as_posix()}"
                    except Exception:
                        audio_url = f"/api/audio/output_kokoro/{Path(out_mp3_path).name}"

            _update_progress(100, f"{mode_desc.capitalize()} complete!", active=False, audio_url=audio_url, m4b_url=m4b_url)

        threading.Thread(target=_worker, daemon=True).start()
        self._send_json({"ok": True, "message": "Synthesis initiated"})

    def _handle_read_paragraph(self):
        import hashlib
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        req = json.loads(body.decode("utf-8"))

        text = req.get("text", "").strip()
        if not text:
            return self._send_json({"ok": False, "error": "Empty paragraph text"})
        voice = req.get("voice", "af_heart")
        speed = float(req.get("speed", 1.15))
        pitch = int(req.get("pitch", 4))

        cfg = load_config(self.b2a_dir / "config.yaml")
        cfg["tts"]["engine"] = "kokoro"
        cfg["tts"]["voice"] = voice
        cfg["tts"]["kokoro"]["voice"] = voice
        cfg["tts"]["kokoro"]["speed"] = speed
        cfg["tts"]["kokoro"]["pitch"] = pitch

        from .tts import create_engine
        engine = create_engine(cfg, prefer="kokoro")

        p_dir = self.b2a_dir / "audition"
        p_dir.mkdir(exist_ok=True)
        h = hashlib.sha256(f"{text}-{voice}-{speed}-{pitch}".encode()).hexdigest()[:12]
        out_wav = p_dir / f"para_{h}.wav"

        job = ChunkJob(id=f"para_{h}", text=text, ssml=text)
        results = engine.synthesize_batch([job], p_dir)
        res = results[0] if results else None

        if res and res.ok and out_wav.exists():
            self._send_json({"ok": True, "audioUrl": f"/api/audio/audition/{out_wav.name}"})
        else:
            self._send_json({"ok": False, "error": (res.error if res else "Paragraph narration failed") or "Paragraph narration failed"})

    def _handle_upload(self):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            return self._send_json({"ok": False, "error": "Invalid Content-Type"})

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        full_payload = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
        msg = BytesParser(policy=email.policy.default).parsebytes(full_payload)

        file_bytes = None
        orig_filename = "uploaded_book.epub"

        if msg.is_multipart():
            for part in msg.iter_parts():
                cd = str(part.get("content-disposition", ""))
                fn = part.get_filename()
                if "book_file" in cd or fn:
                    file_bytes = part.get_payload(decode=True)
                    if fn:
                        orig_filename = fn
                    break

        if not file_bytes:
            return self._send_json({"ok": False, "error": "No file uploaded"})

        uploaded_path = self.books_library_dir / orig_filename
        uploaded_path.write_bytes(file_bytes)

        cfg = load_config(self.b2a_dir / "config.yaml")
        try:
            book = extract_book(uploaded_path, cfg)
            ch_list = []
            for c in book.chapters:
                word_count = sum(len(p.text.split()) for p in c.paragraphs)
                mins = max(1, round(word_count / 160))
                ch_list.append({
                    "index": c.index,
                    "title": _clean_title(c.title),
                    "paragraphs": len(c.paragraphs),
                    "words": word_count,
                    "duration": f"{mins} min",
                })

            self._send_json({
                "ok": True,
                "title": _clean_title(book.title),
                "author": _clean_title(book.author),
                "filename": orig_filename,
                "path": str(uploaded_path),
                "chapters": ch_list,
            })
        except Exception as e:
            log.exception("Error extracting uploaded book: %s", e)
            self._send_json({"ok": False, "error": str(e)})

    def _send_json(self, payload: dict, status: int = HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)


def run_studio(host: str = "127.0.0.1", port: int = 8080, open_browser: bool = True):
    """Run the local Book2Audio Studio web server."""
    actual_port = port
    for p in range(port, port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex((host, p)) != 0:
                actual_port = p
                break

    server = ThreadingHTTPServer((host, actual_port), StudioHandler)
    url = f"http://{host}:{actual_port}"
    print(f"\n=======================================================")
    print(f"  🎧 Book2Audio Studio Web UI is Live!")
    print(f"  URL: {url}")
    print(f"  Speed-Reading Sync View: Active")
    print(f"  Press Ctrl+C to stop the studio server.")
    print(f"=======================================================\n")

    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Book2Audio Studio server...")
        server.server_close()


if __name__ == "__main__":
    run_studio()
