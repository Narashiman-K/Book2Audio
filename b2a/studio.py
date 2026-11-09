"""book2audio Local Web Studio UI server and speed-reader API."""
from __future__ import annotations

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
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from .config import Config, load_config
from .extract import extract_book
from .models import Book, Chapter, Paragraph, ProsodySegment
from .tts.base import ChunkJob
from .pipeline import Pipeline

log = logging.getLogger("b2a.studio")

_PROGRESS_LOCK = threading.Lock()
_PROGRESS_STATE = {
    "active": False,
    "percent": 0,
    "message": "Idle",
    "chapter": 1,
    "audioUrl": "",
}


def _update_progress(percent: int, message: str, active: bool = True, audio_url: str = ""):
    with _PROGRESS_LOCK:
        _PROGRESS_STATE["active"] = active
        _PROGRESS_STATE["percent"] = percent
        _PROGRESS_STATE["message"] = message
        if audio_url:
            _PROGRESS_STATE["audioUrl"] = audio_url


def _get_progress() -> dict:
    with _PROGRESS_LOCK:
        return dict(_PROGRESS_STATE)


class StudioHandler(SimpleHTTPRequestHandler):
    """Custom HTTP request handler serving Studio Single Page App and REST APIs."""

    def __init__(self, *args, **kwargs):
        self.b2a_dir = Path(__file__).resolve().parent.parent
        self.workspace_dir = self.b2a_dir.parent
        self.web_dir = Path(__file__).resolve().parent / "web"
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        # Keep studio server output clean unless error
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
        books = []
        for root in [self.workspace_dir, self.b2a_dir]:
            for p in root.glob("*.*"):
                if p.suffix.lower() in (".epub", ".pdf", ".md", ".txt"):
                    books.append({
                        "filename": p.name,
                        "path": str(p),
                        "size_mb": round(p.stat().st_size / (1024 * 1024), 2),
                    })
        self._send_json({"ok": True, "books": books})

    def _handle_chapter_content(self, query: dict):
        book_key = query.get("book", ["Naval_Ravikant"])[0]
        chapter_idx = int(query.get("chapter", ["1"])[0])

        cfg = load_config(self.b2a_dir / "config.yaml")
        book_file = None

        if "Naval" in book_key:
            for p in [self.workspace_dir / "The-Almanack-of-Naval-Ravikant.md",
                      self.workspace_dir / "The-Almanack-of-Naval-Ravikant.epub"]:
                if p.exists():
                    book_file = p
                    break
        elif "Assert" in book_key:
            for p in [self.workspace_dir / "Assertiveness Training Mastering Assertive Communication.md"]:
                if p.exists():
                    book_file = p
                    break

        if not book_file or not book_file.exists():
            return self._send_json({"ok": False, "error": "Book file not found"})

        try:
            book = extract_book(book_file, cfg)
            target_ch = None
            for ch in book.chapters:
                if ch.index == chapter_idx:
                    target_ch = ch
                    break
            if not target_ch and book.chapters:
                target_ch = book.chapters[0]

            if not target_ch:
                return self._send_json({"ok": False, "error": "Chapter not found"})

            # Check if there is a generated timeline for word/sentence speed-reading sync
            timeline_file = None
            for candidate_dir in [
                self.b2a_dir / "output_kokoro" / "_build",
                self.workspace_dir / "samples",
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

            # Estimate timestamps if timeline not yet generated
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

            res = {
                "ok": True,
                "chapter_index": target_ch.index,
                "chapter_title": target_ch.title,
                "paragraphs": paragraphs,
                "total_paragraphs": len(paragraphs),
                "timeline": timeline_data,
            }
            return self._send_json(res)
        except Exception as e:
            log.exception("Error extracting chapter content: %s", e)
            return self._send_json({"ok": False, "error": str(e)})

    def _handle_progress(self):
        self._send_json(_get_progress())

    def _handle_audio_stream(self, relative_path: str):
        # Resolve target audio file
        target_path = None
        decoded = urllib.parse.unquote(relative_path)

        if decoded == "sample_naval":
            cand = self.workspace_dir / "samples" / "Naval_Ravikant_Ch01_Studio_Audio.mp3"
            if cand.exists():
                target_path = cand
        elif decoded == "sample_naval_m4b":
            cand = self.workspace_dir / "samples" / "The_Almanack_of_Naval_Ravikant_Audiobook.m4b"
            if cand.exists():
                target_path = cand
        elif decoded == "sample_assertive":
            cand = self.workspace_dir / "samples" / "Assertiveness_Training_Ch01_Studio_Audio.mp3"
            if cand.exists():
                target_path = cand
        else:
            for base in [self.workspace_dir, self.b2a_dir]:
                c = (base / decoded).resolve()
                if c.exists() and c.is_file():
                    target_path = c
                    break

        if not target_path or not target_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "Audio file not found")
            return

        # Handle HTTP Range Requests for browser seeking/scrubbing
        file_size = target_path.stat().st_size
        range_header = self.headers.get("Range")

        mime_type, _ = mimetypes.guess_type(str(target_path))
        if not mime_type:
            mime_type = "audio/mpeg" if target_path.suffix == ".mp3" else "audio/wav"

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

        book_key = req.get("book", "Naval_Ravikant")
        chapter_idx = int(req.get("chapter", 1))
        voice = req.get("voice", "af_heart")
        speed = float(req.get("speed", 1.15))
        pitch = int(req.get("pitch", 4))

        def _worker():
            _update_progress(10, "Initializing Kokoro neural engine...")
            cfg = load_config(self.b2a_dir / "config.yaml")
            cfg["tts"]["engine"] = "kokoro"
            cfg["tts"]["voice"] = voice
            cfg["tts"]["kokoro"]["voice"] = voice
            cfg["tts"]["kokoro"]["speed"] = speed
            cfg["tts"]["kokoro"]["pitch"] = pitch
            cfg["tts"]["mp3_bitrate"] = "320k"

            book_file = None
            if "Naval" in book_key:
                book_file = self.workspace_dir / "The-Almanack-of-Naval-Ravikant.md"
            elif "Assert" in book_key:
                book_file = self.workspace_dir / "Assertiveness Training Mastering Assertive Communication.md"

            if not book_file or not book_file.exists():
                _update_progress(0, "Error: Book source file not found", active=False)
                return

            from .tts import create_engine
            engine = create_engine(cfg, "kokoro", voice=voice)
            out_root = self.b2a_dir / "output_kokoro"
            pipeline = Pipeline(cfg, engine, out_root)

            _update_progress(30, f"Synthesizing Chapter {chapter_idx} with Kokoro/{voice}...")
            manifest = pipeline.convert(
                book_file,
                force=True,
                m4b=False,
                chapters=[chapter_idx],
                progress=lambda msg: _update_progress(60, msg)
            )

            ch_files = manifest.get("chapters", [])
            audio_url = ""
            if ch_files:
                out_mp3_path = ch_files[0].get("file")
                if out_mp3_path:
                    rel = Path(out_mp3_path).relative_to(self.workspace_dir)
                    audio_url = f"/api/audio/{rel.as_posix()}"

            _update_progress(100, "Synthesis complete!", active=False, audio_url=audio_url)

        threading.Thread(target=_worker, daemon=True).start()
        self._send_json({"ok": True, "message": "Synthesis initiated"})

    def _handle_upload(self):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            return self._send_json({"ok": False, "error": "Invalid Content-Type"})

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        # Parse multipart form data using standard library email.parser (Python 3.14+ PEP 594 compliant)
        import email.policy
        from email.parser import BytesParser

        full_payload = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
        msg = BytesParser(policy=email.policy.default).parsebytes(full_payload)

        file_bytes = None
        orig_filename = "uploaded_book.epub"

        if msg.is_multipart():
            for part in msg.iter_parts():
                if part.get_param("name", header="content-disposition") == "book_file" or part.get_filename():
                    file_bytes = part.get_payload(decode=True)
                    if part.get_filename():
                        orig_filename = part.get_filename()
                    break

        if not file_bytes:
            return self._send_json({"ok": False, "error": "No file uploaded"})

        upload_dir = self.b2a_dir / "uploads"
        upload_dir.mkdir(exist_ok=True)
        uploaded_path = upload_dir / orig_filename
        uploaded_path.write_bytes(file_bytes)

        cfg = load_config(self.b2a_dir / "config.yaml")
        try:
            book = extract_book(uploaded_path, cfg)
            ch_list = [{"index": c.index, "title": c.title, "paragraphs": len(c.paragraphs)} for c in book.chapters]
            self._send_json({
                "ok": True,
                "title": book.title,
                "chapters": ch_list,
            })
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)})

    def _send_json(self, payload: dict, status: int = HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_studio(host: str = "127.0.0.1", port: int = 8080, open_browser: bool = True):
    """Run the local Book2Audio Studio web server."""
    # Find next open port if default is occupied
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
