"""book2audio Local Web Studio: UI server, reader API and M4B build jobs."""
from __future__ import annotations

import email.policy
import json
import logging
import mimetypes
import re
import shutil
import socket
import threading
import time
import urllib.parse
import webbrowser
from email.parser import BytesParser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from .audio import retag_m4b
from .config import load_config
from .cover import find_cover
from .extract import extract_book, has_identifiable_chapters
from .models import Book, Chapter
from .pipeline import BuildCancelled, Pipeline, slugify
from .tts.base import ChunkJob
from . import webread

log = logging.getLogger("b2a.studio")

# ----------------------------------------------------------------------
# Build job state (one build at a time)
# ----------------------------------------------------------------------
_JOB_LOCK = threading.Lock()
_STOP = threading.Event()
_JOB = {"state": "idle", "active": False, "percent": 0, "message": "Idle",
        "error": "", "audioUrl": "", "m4bUrl": "", "book": "", "started": 0.0}


def _job_set(**kw) -> None:
    with _JOB_LOCK:
        _JOB.update(kw)


def _job_get() -> dict:
    with _JOB_LOCK:
        d = dict(_JOB)
    d["elapsed"] = round(time.time() - d["started"], 1) if d["active"] and d["started"] else 0
    return d


# ----------------------------------------------------------------------
# Book cache: extracting a PDF/EPUB is slow, so do it once per file version
# ----------------------------------------------------------------------
_BOOK_LOCK = threading.Lock()
_BOOK_CACHE: dict = {}
_EXTRACT_LOCKS: dict = {}


def secrets_equal(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a.encode(), b.encode())


def _clean_title(title: str) -> str:
    clean = re.sub(r"<[^>]+>", "", title or "")
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean or "Chapter"


def _regroup(book: Book, target_words: int = 3500) -> Book:
    """PDFs without real chapters come out as one 'chapter' per page. Merge them
    into Part 1, Part 2... so the reader, the build and the M4B stay sane."""
    if has_identifiable_chapters(book.chapters) or len(book.chapters) <= 1:
        return book
    groups, cur, words = [], [], 0
    for c in book.chapters:
        cur.append(c)
        words += sum(len(p.text.split()) for p in c.paragraphs)
        if words >= target_words:
            groups.append(cur)
            cur, words = [], 0
    if cur:
        if groups and words < target_words / 3:
            groups[-1].extend(cur)
        else:
            groups.append(cur)
    book.chapters = [
        Chapter(index=i + 1, title=f"Part {i + 1}",
                paragraphs=[p for c in g for p in c.paragraphs])
        for i, g in enumerate(groups)
    ]
    return book


def load_book(path: Path, cfg) -> Book:
    path = Path(path)
    st = path.stat()
    key = (str(path.resolve()), st.st_mtime_ns, st.st_size)
    with _BOOK_LOCK:
        hit = _BOOK_CACHE.get(key)
        lock = _EXTRACT_LOCKS.setdefault(key, threading.Lock())
    if hit is not None:
        return hit
    with lock:  # two requests for the same book extract only once
        with _BOOK_LOCK:
            hit = _BOOK_CACHE.get(key)
        if hit is not None:
            return hit
        t0 = time.time()
        book = _regroup(extract_book(path, cfg))
        log.info("Extracted %s in %.1fs (%d chapters)", path.name, time.time() - t0, len(book.chapters))
        with _BOOK_LOCK:
            for k in [k for k in _BOOK_CACHE if k[0] == key[0]]:
                _BOOK_CACHE.pop(k, None)  # drop older versions of this file
            _BOOK_CACHE[key] = book
        return book


# ----------------------------------------------------------------------
# Web reading services (token, narration jobs, live voice), created once
# ----------------------------------------------------------------------
_WEB: dict = {}
_WEB_LOCK = threading.Lock()


def web_services(b2a_dir: Path) -> dict:
    with _WEB_LOCK:
        if not _WEB:
            root = b2a_dir / "output_kokoro" / "_web"
            _WEB.update(root=root, token=webread.load_token(root),
                        jobs=webread.WebJobs(b2a_dir, root), live=webread.LiveVoice(b2a_dir, root))
        return _WEB


# ----------------------------------------------------------------------
# Per-book metadata (user edits) and M4B sidecar helpers
# ----------------------------------------------------------------------
_META_KEYS = ("title", "author", "narrator", "year", "genre", "description")


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _find_m4b(out_dir: Path, slug: str):
    """(m4b path, sidecar dict). Prefers the full-book file, else the newest."""
    if not out_dir.exists():
        return None, {}
    cands = [p for p in out_dir.glob("*.m4b")
             if p.is_file() and ".partial" not in p.name and ".retag" not in p.name]
    if not cands:
        return None, {}
    full = [p for p in cands if p.stem == slug]
    m4b = full[0] if full else max(cands, key=lambda p: p.stat().st_mtime)
    return m4b, _read_json(m4b.with_suffix(".b2a.json"))


def _meta_signature(meta: dict) -> dict:
    return {k: str(meta.get(k) or "") for k in _META_KEYS} | {
        "cover": str(meta.get("cover_file") or ""),
        "titles": json.dumps(meta.get("chapter_titles") or {}, sort_keys=True),
    }


class StudioHandler(SimpleHTTPRequestHandler):
    """HTTP handler for the Studio single-page app and its JSON API."""

    def __init__(self, *args, **kwargs):
        self.b2a_dir = Path(__file__).resolve().parent.parent
        self.workspace_dir = self.b2a_dir.parent
        self.web_dir = Path(__file__).resolve().parent / "web"
        super().__init__(*args, **kwargs)

    @property
    def books_library_dir(self) -> Path:
        lib = self.workspace_dir / "Books-Library"
        lib.mkdir(parents=True, exist_ok=True)
        return lib

    @property
    def out_root(self) -> Path:
        return self.b2a_dir / "output_kokoro"

    def log_message(self, format, *args):
        if args and str(args[1]).startswith(("4", "5")):
            log.warning("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)
        else:
            log.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    # ---------------- routing ----------------
    @property
    def web(self) -> dict:
        return web_services(self.b2a_dir)

    def _guard(self, allow_token: bool = True) -> bool:
        """Allow same-origin pages (the Studio itself), or a valid extension token.
        Blocks other websites from driving the local server."""
        host = (self.headers.get("Host") or "").split(":")[0]
        ok = host in ("127.0.0.1", "localhost")
        if ok:
            tok = self.headers.get("X-B2A-Token", "")
            site = self.headers.get("Sec-Fetch-Site")
            if allow_token and tok and secrets_equal(tok, self.web["token"]):
                return True
            if site in ("same-origin", "none") or (site is None and not self.headers.get("Origin")):
                return True
        self._send_json({"ok": False, "error": "Not allowed. Open the Studio itself, or add the access token."},
                        HTTPStatus.FORBIDDEN)
        return False

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        try:
            if path.startswith("/api/web/"):
                return self._web_get(path[len("/api/web/"):])
            if path in ("/", "/index.html"):
                return self._serve_index()
            if path.startswith("/assets/"):
                return self._serve_asset(path[len("/assets/"):])
            if path == "/api/status":
                return self._handle_status()
            if path == "/api/voices":
                return self._handle_voices()
            if path == "/api/books":
                return self._handle_books()
            if path == "/api/book_details":
                return self._handle_book_details(query)
            if path == "/api/progress":
                return self._send_json(_job_get())
            if path == "/api/metadata":
                return self._handle_metadata_get(query)
            if path.startswith("/api/audio/"):
                return self._handle_audio_stream(path[len("/api/audio/"):])
            self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path.startswith("/api/web/jobs/") and self._guard():
                ok = self.web["jobs"].delete(path.rsplit("/", 1)[1])
                return self._send_json({"ok": ok})
            self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if not self._guard():
                return
            if path.startswith("/api/web/"):
                return self._web_post(path[len("/api/web/"):])
            routes = {
                "/api/audition": self._handle_audition,
                "/api/synthesize": self._handle_synthesize,
                "/api/cancel": self._handle_cancel,
                "/api/upload": self._handle_upload,
                "/api/read_paragraph": self._handle_read_paragraph,
                "/api/metadata": self._handle_metadata_post,
                "/api/cover": self._handle_cover,
            }
            fn = routes.get(path)
            if not fn:
                return self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")
            fn()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log.exception("POST %s failed", path)
            self._send_json({"ok": False, "error": str(e)})

    # ---------------- helpers ----------------
    def _json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def _parse_multipart(self):
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            raise ValueError("Invalid Content-Type")
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        msg = BytesParser(policy=email.policy.default).parsebytes(
            f"Content-Type: {ctype}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body)
        fields, files = {}, {}
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            fn = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if fn:
                files[name] = (Path(fn).name, payload)
            else:
                fields[name] = payload.decode("utf-8", "replace")
        return fields, files

    def _book_out_dir(self, book: Book) -> Path:
        return self.out_root / slugify(book.title)

    def _rel_url(self, p: Path) -> str:
        try:
            return "/api/audio/" + p.resolve().relative_to(self.workspace_dir.resolve()).as_posix()
        except ValueError:
            return "/api/audio/" + p.resolve().relative_to(self.b2a_dir.resolve()).as_posix()

    def _serve_index(self):
        index_file = self.web_dir / "index.html"
        if not index_file.exists():
            return self.send_error(HTTPStatus.NOT_FOUND, "index.html not found")
        data = index_file.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    def _serve_asset(self, name: str):
        base = (self.web_dir / "assets").resolve()
        target = (base / urllib.parse.unquote(name)).resolve()
        if base not in target.parents or not target.is_file():
            return self.send_error(HTTPStatus.NOT_FOUND, "Asset not found")
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def _handle_status(self):
        cfg = load_config(self.b2a_dir / "config.yaml")
        self._send_json({
            "ok": True,
            "engine": cfg.get_path("tts.engine", "kokoro"),
            "sample_rate": 24000,
            "speed": cfg.get_path("tts.kokoro.speed", 1.15),
            "pitch": cfg.get_path("tts.kokoro.pitch", 4),
            "bitrate": cfg.get_path("tts.mp3_bitrate", "320k"),
            "voices_total": 54,
        })

    def _voices_list(self) -> list:
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
        return voices

    def _handle_voices(self):
        voices = self._voices_list()
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

    # ---------------- book details ----------------
    def _effective_meta(self, book: Book, saved: dict, side_meta: Optional[dict] = None) -> dict:
        side_meta = side_meta or {}
        eff = {
            "title": _clean_title(book.title),
            "author": _clean_title(book.author) if book.author else "",
            "narrator": side_meta.get("narrator", ""),
            "year": "", "genre": "Audiobook", "description": "",
        }
        for k in _META_KEYS:
            if str(saved.get(k) or "").strip():
                eff[k] = str(saved[k]).strip()
        eff["chapter_titles"] = saved.get("chapter_titles") or {}
        eff["cover_file"] = saved.get("cover_file") or ""
        return eff

    def _handle_book_details(self, query: dict):
        raw_path = query.get("path", [""])[0]
        book_path = self._resolve_book_path(raw_path)
        if not book_path or not book_path.exists():
            return self._send_json({"ok": False, "error": f"Book not found: {raw_path}"})
        cfg = load_config(self.b2a_dir / "config.yaml")
        try:
            book = load_book(book_path, cfg)
        except Exception as e:
            log.exception("Error extracting book details: %s", e)
            return self._send_json({"ok": False, "error": f"Could not read this book: {e}"})

        out_dir = self._book_out_dir(book)
        slug = slugify(book.title)
        saved = _read_json(out_dir / "book_meta.json")
        m4b, side = _find_m4b(out_dir, slug)
        timeline = side.get("timeline") or {}
        eff = self._effective_meta(book, saved, side.get("meta"))

        chapters, paragraphs = [], []
        total_words = 0
        side_ch = {c.get("index"): c for c in side.get("chapters", [])}
        for ch in book.chapters:
            words = sum(len(p.text.split()) for p in ch.paragraphs)
            total_words += words
            mp3 = next(iter(sorted(out_dir.glob(f"{ch.index:02d} - *.mp3"))), None) if out_dir.exists() else None
            sc = side_ch.get(ch.index)
            chapters.append({
                "index": ch.index,
                "title": (eff["chapter_titles"].get(str(ch.index)) or _clean_title(ch.title)),
                "paragraphs": len(ch.paragraphs),
                "words": words,
                "narrated": bool(mp3),
                "in_m4b": bool(sc),
                "start": sc["start"] if sc else None,
                "end": sc["end"] if sc else None,
                "mp3_url": self._rel_url(mp3) if mp3 else "",
            })
            for p in ch.paragraphs:
                t = timeline.get(p.id)
                paragraphs.append({"id": p.id, "ch": ch.index, "text": p.text,
                                   "start": t[0] if t else None, "end": t[1] if t else None})

        stale = False
        if m4b and side:
            sm = side.get("meta", {})
            e, s = _meta_signature(eff), _meta_signature(sm)
            if not eff["narrator"]:
                e["narrator"] = s["narrator"]
            stale = e != s
        cover = None
        user_cover = out_dir / eff["cover_file"] if eff["cover_file"] else None
        if user_cover and user_cover.is_file():
            cover = user_cover
        else:
            cover = find_cover(book_path, out_dir)
        total_min = max(1, round(total_words / 160))
        self._send_json({
            "ok": True,
            "title": _clean_title(book.title),
            "author": _clean_title(book.author) if book.author else "Unknown Author",
            "filename": book_path.name,
            "path": str(book_path),
            "regrouped": not has_identifiable_chapters(book.chapters) and len(book.chapters) > 0
                         and book.chapters[0].title.startswith("Part "),
            "chapters": chapters,
            "paragraphs": paragraphs,
            "total_words": total_words,
            "total_duration": f"{total_min} min" if total_min < 60 else f"{total_min // 60}h {total_min % 60}m",
            "meta": eff,
            "has_m4b": bool(m4b),
            "m4b_url": self._rel_url(m4b) if m4b else "",
            "m4b_name": m4b.name if m4b else "",
            "m4b_duration": side.get("duration", 0),
            "m4b_has_timeline": bool(timeline),
            "m4b_stale": stale,
            "m4b_settings": side.get("settings", {}),
            "cover_url": (self._rel_url(cover) + f"?v={int(cover.stat().st_mtime)}") if cover else "",
        })

    # ---------------- metadata / cover ----------------
    def _handle_metadata_get(self, query: dict):
        book_path = self._resolve_book_path(query.get("path", [""])[0])
        if not book_path:
            return self._send_json({"ok": False, "error": "Book not found"})
        book = load_book(book_path, load_config(self.b2a_dir / "config.yaml"))
        saved = _read_json(self._book_out_dir(book) / "book_meta.json")
        self._send_json({"ok": True, "meta": self._effective_meta(book, saved)})

    def _save_meta(self, out_dir: Path, update: dict) -> dict:
        out_dir.mkdir(parents=True, exist_ok=True)
        f = out_dir / "book_meta.json"
        cur = _read_json(f)
        cur.update(update)
        f.write_text(json.dumps(cur, indent=1, ensure_ascii=False), encoding="utf-8")
        return cur

    def _handle_metadata_post(self):
        req = self._json_body()
        book_path = self._resolve_book_path(req.get("path", ""))
        if not book_path:
            return self._send_json({"ok": False, "error": "Book not found"})
        book = load_book(book_path, load_config(self.b2a_dir / "config.yaml"))
        out_dir = self._book_out_dir(book)
        incoming = req.get("meta", {})
        upd = {k: str(incoming.get(k, "")).strip() for k in _META_KEYS if k in incoming}
        if "chapter_titles" in incoming:
            default = {str(c.index): _clean_title(c.title) for c in book.chapters}
            upd["chapter_titles"] = {k: v.strip() for k, v in incoming["chapter_titles"].items()
                                     if str(v).strip() and str(v).strip() != default.get(str(k))}
        saved = self._save_meta(out_dir, upd)

        retagged = False
        if req.get("update_m4b"):
            if _job_get()["active"]:
                return self._send_json({"ok": False, "error": "A build is running; try again when it finishes."})
            m4b, side = _find_m4b(out_dir, slugify(book.title))
            if not m4b or not side:
                return self._send_json({"ok": False, "error": "No M4B built yet. Metadata saved; it will be used on the next build."})
            eff = self._effective_meta(book, saved, side.get("meta"))
            marks = [{"title": eff["chapter_titles"].get(str(c["index"])) or c["title"],
                      "start": c["start"], "end": c["end"]} for c in side["chapters"]]
            cover = out_dir / eff["cover_file"] if eff["cover_file"] else None
            if not (cover and cover.is_file()):
                cp = side.get("cover_path")
                cover = Path(cp) if cp and Path(cp).is_file() else None
            meta_full = dict(eff)
            meta_full["album"] = eff["title"]
            retag_m4b(m4b, meta_full, marks, cover)
            side["meta"] = meta_full
            for c, m in zip(side["chapters"], marks):
                c["title"] = m["title"]
            if cover:
                side["cover_path"], side["cover"] = str(cover), cover.name
            m4b.with_suffix(".b2a.json").write_text(json.dumps(side, indent=1, ensure_ascii=False), encoding="utf-8")
            retagged = True
        self._send_json({"ok": True, "retagged": retagged})

    def _handle_cover(self):
        fields, files = self._parse_multipart()
        book_path = self._resolve_book_path(fields.get("path", ""))
        if not book_path:
            return self._send_json({"ok": False, "error": "Book not found"})
        book = load_book(book_path, load_config(self.b2a_dir / "config.yaml"))
        out_dir = self._book_out_dir(book)
        if fields.get("clear"):
            self._save_meta(out_dir, {"cover_file": ""})
            return self._send_json({"ok": True})
        if "cover_file" not in files:
            return self._send_json({"ok": False, "error": "No image received"})
        name, data = files["cover_file"]
        ext = Path(name).suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            return self._send_json({"ok": False, "error": "Cover must be a JPG, PNG or WebP image"})
        if len(data) > 15 * 1024 * 1024:
            return self._send_json({"ok": False, "error": "Cover image is over 15 MB"})
        out_dir.mkdir(parents=True, exist_ok=True)
        for old in out_dir.glob("cover_user.*"):
            old.unlink(missing_ok=True)
        dest = out_dir / f"cover_user{ext}"
        dest.write_bytes(data)
        self._save_meta(out_dir, {"cover_file": dest.name})
        self._send_json({"ok": True, "cover_url": self._rel_url(dest) + f"?v={int(time.time())}"})

    # ---------------- audio streaming ----------------
    def _handle_audio_stream(self, relative_path: str):
        decoded = urllib.parse.unquote(relative_path)
        target = None
        for base in (self.workspace_dir, self.b2a_dir):
            root = base.resolve()
            c = (root / decoded).resolve()
            if root in c.parents and c.is_file():  # no path traversal
                target = c
                break
        if not target:
            return self.send_error(HTTPStatus.NOT_FOUND, "File not found")

        size = target.stat().st_size
        ext = target.suffix.lower()
        mime = {".m4b": "audio/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".wave": "audio/wav"}.get(
            ext) or mimetypes.guess_type(str(target))[0] or "application/octet-stream"

        start, end, status = 0, size - 1, HTTPStatus.OK
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
                else:  # suffix range: last N bytes
                    start = max(0, size - int(m.group(2)))
                if start >= size:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = HTTPStatus.PARTIAL_CONTENT
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        with open(target, "rb") as f:  # stream in 256 KB pieces, never the whole file in RAM
            f.seek(start)
            left = length
            while left > 0:
                buf = f.read(min(262144, left))
                if not buf:
                    break
                self.wfile.write(buf)
                left -= len(buf)

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

    # ---------------- web reading ----------------
    def _web_get(self, sub: str):
        w = self.web
        if sub == "ping":
            return self._send_json({"ok": True, "app": "b2a-studio", "version": webread.VERSION})
        if sub == "token":
            origin = self.headers.get("Origin")
            if self.headers.get("Sec-Fetch-Site") not in ("same-origin", None) or origin:
                return self._send_json({"ok": False, "error": "Same-origin only"}, HTTPStatus.FORBIDDEN)
            return self._send_json({"ok": True, "token": w["token"]})
        if not self._guard():
            return
        if sub == "meta":
            ready, why = w["live"].ready()
            voices = self._voices_list()
            return self._send_json({"ok": True, "version": webread.VERSION, "engine_ready": ready,
                                    "engine_error": why, "voices": [v for v in voices if v["id"][:3] in
                                    ("af_", "am_", "bf_", "bm_")]})
        if sub == "jobs":
            return self._send_json({"ok": True, "jobs": w["jobs"].list()})
        if sub.startswith("jobs/"):
            job = w["jobs"].get(sub[5:]) if re.fullmatch(r"[0-9a-f]{12}", sub[5:]) else None
            if not job:
                return self._send_json({"ok": False, "error": "Job not found"})
            return self._send_json(self._web_job_details(job))
        self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")

    def _web_post(self, sub: str):
        w = self.web
        length = int(self.headers.get("Content-Length", 0))
        if length > 20 * 1024 * 1024:
            return self._send_json({"ok": False, "error": "Request too large"})
        if sub == "jobs":
            try:
                return self._send_json({"ok": True, "job": w["jobs"].create(self._json_body())})
            except ValueError as e:
                return self._send_json({"ok": False, "error": str(e)})
        if sub == "article":  # fetch and clean a page (or split pasted text) so the Studio can read it aloud at once
            req = self._json_body()
            url = str(req.get("url") or "").strip()
            try:
                if url:
                    art = webread.extract_article(webread.fetch_url(url), url)
                else:
                    blocks = webread.text_to_blocks(str(req.get("text") or ""))
                    art = {"title": str(req.get("title") or "Pasted text"), "site": "", "blocks": blocks}
                if webread.count_words(art["blocks"]) < 5:
                    raise ValueError("No readable article text was found on that page. "
                                     "Try the browser extension, which reads the page you are looking at.")
                if webread.count_words(art["blocks"]) > webread.MAX_WORDS:
                    raise ValueError(f"That page is longer than {webread.MAX_WORDS:,} words.")
            except Exception as e:  # noqa: BLE001
                return self._send_json({"ok": False, "error": str(e)})
            return self._send_json({"ok": True, "title": art["title"], "site": art.get("site", ""), "url": url,
                                    "blocks": art["blocks"]})
        if sub == "speak":
            req = self._json_body()
            try:
                wav, dur = w["live"].speak(str(req.get("text", "")), str(req.get("voice") or "af_heart"),
                                           float(req.get("speed") or 1.0))
            except Exception as e:  # noqa: BLE001
                log.warning("speak failed: %s", e)
                return self._send_json({"ok": False, "error": str(e)}, HTTPStatus.SERVICE_UNAVAILABLE)
            data = wav.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-B2A-Duration", f"{dur:.3f}")
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Endpoint not found")

    def _web_job_details(self, job: dict) -> dict:
        """Shape a finished job like a one-chapter book so the Studio reader can open it."""
        jid = job["id"]
        base = {"ok": True, "job": job}
        if not job.get("audio_ready"):
            return base
        art, tl = self.web["jobs"].article(jid), self.web["jobs"].timeline(jid)
        audio = self.web["root"] / "jobs" / jid / "audio.mp3"
        paras = []
        for i, b in enumerate(art["blocks"]):
            pid = f"c01-p{i:03d}"
            t = tl.get(pid)
            paras.append({"id": pid, "ch": 1, "text": b["text"], "start": t[0] if t else None,
                          "end": t[1] if t else None})
        dur = float(job.get("duration_s") or 0)
        words = job.get("words", 0)
        mins = max(1, round(words / 160))
        url = self._rel_url(audio)
        base.update({
            "title": job["title"], "author": job.get("site") or "Web", "path": f"web:{jid}", "filename": job["title"],
            "chapters": [{"index": 1, "title": job["title"], "paragraphs": len(paras), "words": words,
                          "narrated": True, "in_m4b": True, "start": 0, "end": dur, "mp3_url": url}],
            "paragraphs": paras, "total_words": words, "total_duration": f"{mins} min",
            "meta": {"title": job["title"], "author": job.get("site") or "Web", "narrator": "", "year": "",
                     "genre": "Web", "description": job.get("url", ""), "chapter_titles": {}, "cover_file": ""},
            "has_m4b": True, "m4b_url": url, "m4b_name": f"{jid}.mp3", "m4b_duration": dur,
            "m4b_has_timeline": bool(tl), "m4b_stale": False,
            "m4b_settings": {"voice": job.get("voice", "")}, "cover_url": "", "web": True,
        })
        return base

    # ---------------- build job ----------------
    def _handle_cancel(self):
        if _job_get()["active"]:
            _STOP.set()
            _job_set(message="Stopping after the current paragraph...")
        self._send_json({"ok": True})

    def _handle_synthesize(self):
        req = self._json_body()
        book_file = self._resolve_book_path(req.get("path", ""))
        if not book_file or not book_file.exists():
            return self._send_json({"ok": False, "error": f"Book file not found: {req.get('path', '')}"})
        if _job_get()["active"]:
            return self._send_json({"ok": False, "error": "A build is already running."})

        voice = str(req.get("voice", "af_heart"))
        speed = float(req.get("speed", 1.15))
        pitch = int(req.get("pitch", 4))
        bitrate = str(req.get("bitrate") or "128k")
        if not re.fullmatch(r"\d{2,3}k", bitrate):
            bitrate = "128k"
        chapters = [int(c) for c in (req.get("chapters") or [])] or None
        force = bool(req.get("force", False))

        # mark the job active BEFORE the thread starts so the UI never polls a stale "idle"
        _STOP.clear()
        _job_set(state="running", active=True, percent=1, message="Preparing the narrator...",
                 error="", audioUrl="", m4bUrl="", started=time.time(), book=book_file.name)

        def _worker():
            try:
                cfg = load_config(self.b2a_dir / "config.yaml")
                cfg["tts"]["engine"] = "kokoro"
                cfg["tts"]["voice"] = voice
                cfg["tts"]["kokoro"]["voice"] = voice
                cfg["tts"]["kokoro"]["speed"] = speed
                cfg["tts"]["kokoro"]["pitch"] = pitch
                cfg["tts"]["mp3_bitrate"] = bitrate
                cfg["llm"]["provider"] = "off"

                from .tts import create_engine
                engine = create_engine(cfg, prefer="kokoro")
                if engine.name != "kokoro":
                    raise RuntimeError(
                        "The Kokoro voice engine could not start, so the build was stopped instead of "
                        f"producing silent audio (fell back to '{engine.name}'). Check that kokoro-onnx, "
                        "onnxruntime and soundfile are installed and models/kokoro/ holds both model files.")

                _job_set(message="Reading the book...")
                book = load_book(book_file, cfg)
                out_dir = self._book_out_dir(book)
                saved = _read_json(out_dir / "book_meta.json")
                meta = self._effective_meta(book, saved)
                if not meta["narrator"]:
                    meta.pop("narrator")
                user_cover = out_dir / meta["cover_file"] if meta["cover_file"] else None
                if user_cover and not user_cover.is_file():
                    user_cover = None

                pipeline = Pipeline(cfg, engine, self.out_root)
                pipeline.on_progress = lambda f, m: _job_set(percent=max(1, int(f * 100)), message=m)
                pipeline.should_stop = _STOP.is_set
                manifest = pipeline.render_book(
                    book, force=force, m4b=True, chapters=chapters,
                    say=lambda m: log.info(m), meta=meta, cover=user_cover)

                m4b_file = manifest.get("m4b_file")
                url = self._rel_url(Path(m4b_file)) if m4b_file else ""
                _job_set(state="done", active=False, percent=100, message="Audiobook ready",
                         audioUrl=url, m4bUrl=url)
            except BuildCancelled:
                _job_set(state="cancelled", active=False,
                         message="Stopped. Finished chapters are kept; press Build to continue from here.")
            except Exception as e:
                log.exception("Build failed")
                _job_set(state="error", active=False, error=str(e), message=f"Build failed: {e}")

        threading.Thread(target=_worker, daemon=True).start()
        self._send_json({"ok": True, "message": "Build started"})

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

    # ---------------- upload ----------------
    def _handle_upload(self):
        fields, files = self._parse_multipart()
        if "book_file" not in files:
            return self._send_json({"ok": False, "error": "No file uploaded"})
        name, data = files["book_file"]
        if Path(name).suffix.lower() not in (".epub", ".pdf", ".md", ".markdown", ".txt"):
            return self._send_json({"ok": False, "error": "Supported formats: PDF, EPUB, MD, TXT"})
        dest = self.books_library_dir / name
        dest.write_bytes(data)
        try:  # extract once now so opening the book is instant
            book = load_book(dest, load_config(self.b2a_dir / "config.yaml"))
        except Exception as e:
            log.exception("Error extracting uploaded book: %s", e)
            return self._send_json({"ok": False, "error": f"Could not read this book: {e}"})
        self._send_json({"ok": True, "title": _clean_title(book.title), "filename": name,
                         "path": str(dest), "chapters": len(book.chapters)})

    def _send_json(self, payload: dict, status: int = HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
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
    if actual_port != port:
        print("\n  !! Port %d is already in use. If that is an OLD Book2Audio Studio window, close it:" % port)
        print("  !! otherwise the browser tab on port %d keeps talking to the old version." % port)
        print("  !! This new Studio is starting on port %d instead.\n" % actual_port)

    server = ThreadingHTTPServer((host, actual_port), StudioHandler)
    url = f"http://{host}:{actual_port}"
    print("\n=======================================================")
    print("  Book2Audio Studio is live")
    print(f"  URL: {url}")
    print(f"  Extension token: {web_services(Path(__file__).resolve().parent.parent)['token']}")
    print("  Press Ctrl+C to stop the studio server.")
    print("=======================================================\n")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Book2Audio Studio server...")
        server.server_close()


if __name__ == "__main__":
    run_studio()
