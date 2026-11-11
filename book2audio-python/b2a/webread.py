"""Web reading: article extraction, narration jobs, live sentence speech, access token.

Everything here is local. The HTTP surface (see studio.py) is deliberately small so the
browser extension, the Studio "Web to Audio" tab and any future shell (Electron, web)
can share it:

  GET  /api/web/ping                no auth, identifies the server (extension port probe)
  GET  /api/web/token               same-origin only, returns the access token
  GET  /api/web/meta                voices + engine status
  POST /api/web/speak               {text, voice, speed} -> audio/wav (one sentence)
  GET  /api/web/jobs                list narration jobs
  POST /api/web/jobs                {url} | {text,title} | {title,url,blocks:[{kind,text}]}
  GET  /api/web/jobs/<id>           job + reader details
  DELETE /api/web/jobs/<id>
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import queue
import re
import secrets
import shutil
import socket
import threading
import time
import urllib.parse
import urllib.request
import uuid
import wave
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("b2a.web")

VERSION = "1.0"
ALLOWED_ENGINES = ("kokoro",)          # tests may extend this with "silent"
MAX_HTML_BYTES = 8 * 1024 * 1024
MAX_WORDS = 60000


# ----------------------------------------------------------------------
# Access token
# ----------------------------------------------------------------------
def load_token(web_root: Path) -> str:
    web_root.mkdir(parents=True, exist_ok=True)
    f = web_root / "token.txt"
    if f.is_file() and f.read_text(encoding="utf-8").strip():
        return f.read_text(encoding="utf-8").strip()
    tok = secrets.token_urlsafe(24)
    f.write_text(tok, encoding="utf-8")
    return tok


# ----------------------------------------------------------------------
# Fetching and extracting a web page
# ----------------------------------------------------------------------
def _check_public_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ValueError(f"Cannot find '{host}' ({e.strerror or e})")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ValueError("Only public web pages can be fetched here. "
                             "Use the browser extension for pages on your own network or behind a login.")


def fetch_url(url: str) -> str:
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("Enter a full http(s) address.")
    _check_public_host(p.hostname)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Suprasuta-Book2Audio/1.0",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
        "Accept-Language": "en;q=0.9"})
    with urllib.request.urlopen(req, timeout=20) as r:
        final = urllib.parse.urlparse(r.geturl())
        if final.hostname and final.hostname != p.hostname:
            _check_public_host(final.hostname)
        data = r.read(MAX_HTML_BYTES + 1)
        if len(data) > MAX_HTML_BYTES:
            raise ValueError("That page is larger than 8 MB.")
        charset = r.headers.get_content_charset()
    if not charset:
        m = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', data[:4096], re.I)
        charset = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    try:
        return data.decode(charset, "replace")
    except LookupError:
        return data.decode("utf-8", "replace")


_DROP = ("script", "style", "noscript", "nav", "footer", "aside", "form", "iframe",
         "svg", "button", "figure", "template", "select", "dialog")
_JUNK = re.compile(r"(comment|share|social|related|promo|advert|cookie|newsletter|sidebar|"
                   r"breadcrumb|subscribe|modal|popup|toolbar|byline-extra|footer|nav-)", re.I)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def extract_article(html: str, url: str = "") -> dict:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")

    def meta(*names):
        for n in names:
            t = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
            if t and t.get("content"):
                return _clean(t["content"])
        return ""

    title = meta("og:title", "twitter:title") or (soup.title.get_text() if soup.title else "")
    if not title:
        h1 = soup.find("h1")
        title = h1.get_text() if h1 else ""
    host = urllib.parse.urlparse(url).hostname or ""
    site = meta("og:site_name") or host.removeprefix("www.")
    author = meta("author", "article:author")
    for t in soup(_DROP):
        t.decompose()

    root = None
    arts = soup.find_all("article")
    if arts:
        root = max(arts, key=lambda a: len(a.get_text()))
    root = root or soup.find(attrs={"role": "main"}) or soup.find("main")
    if root is None or len(root.get_text().split()) < 80:
        scores: dict = {}
        for p in soup.find_all("p"):
            n = len(_clean(p.get_text()))
            if n < 40:
                continue
            for depth, anc in enumerate((p.parent, p.parent.parent if p.parent else None)):
                if anc is not None:
                    scores[anc] = scores.get(anc, 0) + n / (1 + depth)
        root = max(scores, key=scores.get) if scores else (soup.body or soup)

    blocks, seen = [], set()

    def junky(el) -> bool:
        for a in el.parents:
            if a is root:
                return False
            if a.name in ("body", "html"):
                return False
            cls = " ".join(a.get("class") or []) + " " + (a.get("id") or "")
            if _JUNK.search(cls):
                return True
        return False

    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "li", "blockquote"]):
        if el.name in ("li", "blockquote") and el.find("p"):
            continue
        text = _clean(el.get_text(" "))
        is_h = el.name.startswith("h")
        if len(text) < (3 if is_h else 25) or text in seen or junky(el):
            continue
        seen.add(text)
        blocks.append({"kind": "h" if is_h else "p", "text": text})
    title = _clean(title)
    for sep in (" | ", " - ", " – ", " — ", " · "):
        head, _, tail = title.rpartition(sep)
        if head and site and tail.strip().lower() in (site.lower(), host.lower(), host.removeprefix("www.").lower()):
            title = head.strip()
            break
    if blocks and blocks[0]["kind"] == "h" and blocks[0]["text"].lower() in title.lower():
        blocks.pop(0)
    return {"title": title or host or "Web page", "site": site, "author": author,
            "url": url, "blocks": blocks}


def text_to_blocks(text: str) -> list:
    out = []
    for chunk in re.split(r"\n\s*\n", text.replace("\r", "")):
        chunk = _clean(chunk)
        if not chunk:
            continue
        m = re.match(r"^#{1,4}\s+(.*)", chunk)
        out.append({"kind": "h" if m else "p", "text": m.group(1) if m else chunk})
    return out


def count_words(blocks: list) -> int:
    return sum(len(b["text"].split()) for b in blocks)


# ----------------------------------------------------------------------
# Live speech: one shared Kokoro engine so the model loads once
# ----------------------------------------------------------------------
class LiveVoice:
    def __init__(self, b2a_dir: Path, web_root: Path):
        self.b2a_dir = b2a_dir
        self.dir = web_root / "live"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.cfg = None
        self.engine = None
        self._tidy()

    def _tidy(self):
        files = sorted(self.dir.glob("*.wav"), key=lambda p: p.stat().st_mtime)
        cutoff = time.time() - 86400
        for p in files:
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
        files = sorted(self.dir.glob("*.wav"), key=lambda p: p.stat().st_mtime)
        for p in files[:-400]:
            p.unlink(missing_ok=True)

    def _ensure(self):
        if self.engine is None:
            from .config import load_config
            from .tts import create_engine
            cfg = load_config(self.b2a_dir / "config.yaml")
            cfg["tts"]["engine"] = "kokoro"
            cfg["llm"]["provider"] = "off"
            eng = create_engine(cfg, prefer="kokoro")
            if eng.name not in ALLOWED_ENGINES:
                raise RuntimeError(
                    "The Kokoro voice engine is not available, so nothing can be spoken. Check that kokoro-onnx, "
                    f"onnxruntime and soundfile are installed and models/kokoro/ holds both model files (fell back to '{eng.name}').")
            self.cfg, self.engine = cfg, eng
        return self.engine

    def ready(self) -> tuple:
        try:
            self._ensure()
            return True, ""
        except Exception as e:
            return False, str(e)

    def speak(self, text: str, voice: str, speed: float) -> tuple:
        """-> (wav path, duration seconds)."""
        from .tts.base import ChunkJob
        text = _clean(text)[:1500]
        if not text:
            raise ValueError("Nothing to speak")
        speed = max(0.6, min(1.8, float(speed)))
        key = hashlib.sha1(f"{text}|{voice}|{speed:.2f}".encode()).hexdigest()[:20]
        out = self.dir / f"{key}.wav"
        if out.is_file() and out.stat().st_size > 1000:
            return out, _wav_seconds(out)
        with self.lock:
            eng = self._ensure()
            self.cfg["tts"]["voice"] = voice
            self.cfg["tts"]["kokoro"]["voice"] = voice
            self.cfg["tts"]["kokoro"]["speed"] = speed
            res = eng.synthesize_batch([ChunkJob(id=key, text=text, ssml=text)], self.dir)
        if not res or not res[0].ok or not out.is_file():
            raise RuntimeError((res[0].error if res else "") or "Narration failed")
        return out, _wav_seconds(out)


def _wav_seconds(p: Path) -> float:
    try:
        with wave.open(str(p), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return 0.0


# ----------------------------------------------------------------------
# Narration jobs (URL / text / extension page -> MP3 + timeline)
# ----------------------------------------------------------------------
class WebJobs:
    def __init__(self, b2a_dir: Path, web_root: Path):
        self.b2a_dir = b2a_dir
        self.root = web_root / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.q: "queue.Queue[str]" = queue.Queue()
        self.stops: dict = {}
        self.lock = threading.Lock()
        for j in self.list():
            if j["status"] in ("queued", "running", "fetching"):
                self._update(j["id"], status="error", error="Interrupted: the studio was closed. Create it again.")
        threading.Thread(target=self._loop, daemon=True).start()

    # ---- storage ----
    def _jf(self, jid: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{12}", jid):
            raise KeyError(jid)
        return self.root / jid / "job.json"

    def get(self, jid: str) -> Optional[dict]:
        try:
            return json.loads(self._jf(jid).read_text(encoding="utf-8"))
        except Exception:
            return None

    def _update(self, jid: str, **kw) -> None:
        with self.lock:
            j = self.get(jid)
            if j is None:
                return
            j.update(kw)
            self._jf(jid).write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")

    def list(self) -> list:
        out = []
        for f in self.root.glob("*/job.json"):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
        return sorted(out, key=lambda j: j.get("created", ""), reverse=True)[:60]

    def article(self, jid: str) -> dict:
        try:
            return json.loads((self.root / jid / "article.json").read_text(encoding="utf-8"))
        except Exception:
            return {"blocks": []}

    def timeline(self, jid: str) -> dict:
        try:
            return json.loads((self.root / jid / "timeline.json").read_text(encoding="utf-8"))
        except Exception:
            return {}

    # ---- API ----
    def create(self, payload: dict) -> dict:
        opts = payload.get("options") or {}
        voice = str(opts.get("voice") or "af_heart")
        speed = max(0.6, min(1.8, float(opts.get("speed") or 1.0)))
        url = str(payload.get("url") or "").strip()
        title = _clean(str(payload.get("title") or ""))
        blocks = [{"kind": "h" if b.get("kind") == "h" else "p", "text": _clean(str(b.get("text", "")))}
                  for b in (payload.get("blocks") or []) if _clean(str(b.get("text", "")))]
        if not blocks and payload.get("text"):
            blocks = text_to_blocks(str(payload["text"]))
        if not blocks and not url:
            raise ValueError("Give a web address, some text, or page content.")
        if url and not re.match(r"https?://", url):
            raise ValueError("Enter a full http(s) address.")
        if not blocks:  # validate cheaply now; the fetch itself happens in the worker
            p = urllib.parse.urlparse(url)
            if not p.hostname:
                raise ValueError("Enter a full http(s) address.")
        if blocks and count_words(blocks) < 5:
            raise ValueError("That is too little text to narrate.")
        jid = uuid.uuid4().hex[:12]
        (self.root / jid).mkdir(parents=True)
        job = {"id": jid, "title": title or (urllib.parse.urlparse(url).hostname or "Pasted text"),
               "site": urllib.parse.urlparse(url).hostname or "", "url": url,
               "status": "queued", "pct": 0, "message": "Waiting for the narrator", "error": "",
               "words": count_words(blocks), "voice": voice, "speed": speed,
               "duration_s": 0, "audio_ready": False, "created": time.strftime("%Y-%m-%d %H:%M")}
        (self.root / jid / "article.json").write_text(
            json.dumps({"title": job["title"], "blocks": blocks}, ensure_ascii=False), encoding="utf-8")
        self._jf(jid).write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
        self.q.put(jid)
        return job

    def delete(self, jid: str) -> bool:
        if self.get(jid) is None:
            return False
        ev = self.stops.get(jid)
        if ev:
            ev.set()
        shutil.rmtree(self.root / jid, ignore_errors=True)
        return True

    # ---- worker ----
    def _loop(self):
        while True:
            jid = self.q.get()
            if self.get(jid) is None:
                continue
            ev = self.stops[jid] = threading.Event()
            try:
                self._run(jid, ev)
            except Exception as e:  # noqa: BLE001
                if self.get(jid) is not None:
                    log.exception("web job failed")
                    self._update(jid, status="error", error=str(e), message="Failed")
            finally:
                self.stops.pop(jid, None)

    def _run(self, jid: str, ev: threading.Event):
        from .config import load_config
        from .models import Book, Chapter, Paragraph
        from .pipeline import BuildCancelled, Pipeline
        from .tts import create_engine

        job = self.get(jid)
        art = self.article(jid)
        if not art["blocks"]:
            self._update(jid, status="fetching", message="Fetching the page")
            art = extract_article(fetch_url(job["url"]), job["url"])
            if count_words(art["blocks"]) < 40:
                raise ValueError("No readable article text was found on that page. "
                                 "Try the browser extension, which reads the page you are looking at.")
            blocks = art["blocks"]
            words = count_words(blocks)
            if words > MAX_WORDS:
                raise ValueError(f"That page has {words:,} words; the limit is {MAX_WORDS:,}.")
            (self.root / jid / "article.json").write_text(json.dumps(art, ensure_ascii=False), encoding="utf-8")
            self._update(jid, title=art["title"], site=art["site"] or job["site"], words=words)
        self._update(jid, status="running", message="Preparing the narrator", pct=1)

        cfg = load_config(self.b2a_dir / "config.yaml")
        cfg["tts"]["engine"] = "kokoro"
        cfg["tts"]["voice"] = job["voice"]
        cfg["tts"]["kokoro"]["voice"] = job["voice"]
        cfg["tts"]["kokoro"]["speed"] = job["speed"]
        cfg["tts"]["mp3_bitrate"] = "128k"
        cfg["llm"]["provider"] = "off"
        engine = create_engine(cfg, prefer="kokoro")
        if engine.name not in ALLOWED_ENGINES:
            raise RuntimeError("The Kokoro voice engine could not start, so nothing was narrated "
                               f"(fell back to '{engine.name}'). Check kokoro-onnx and models/kokoro/.")

        job = self.get(jid)
        paras = [Paragraph(id=f"c01-p{i:03d}", text=b["text"]) for i, b in enumerate(art["blocks"])]
        book = Book(title=job["title"], author=job["site"] or "Web", source_path="", source_format="web",
                    chapters=[Chapter(index=1, title=job["title"], paragraphs=paras)])
        work = self.root / jid / "render"
        pl = Pipeline(cfg, engine, work)
        pl.on_progress = lambda f, m: self._update(jid, pct=max(1, int(f * 100)), message=m)
        pl.should_stop = ev.is_set
        try:
            man = pl.render_book(book, m4b=False)
        except BuildCancelled:
            return
        ch = man["chapters"][0]
        shutil.copyfile(ch["file"], self.root / jid / "audio.mp3")
        tl = {}
        for f in work.glob("_build/*/timeline_c01.json"):
            for t in json.loads(f.read_text(encoding="utf-8")):
                tl[t["para_id"]] = [t["start"], t["end"]]
        (self.root / jid / "timeline.json").write_text(json.dumps(tl), encoding="utf-8")
        shutil.rmtree(work, ignore_errors=True)
        self._update(jid, status="done", pct=100, message="Ready", audio_ready=True,
                     duration_s=round(float(ch.get("seconds") or 0), 1))
