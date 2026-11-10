"""Pipeline orchestrator: book file -> chapter MP3s, with caching & resume.

build/<slug>/            (one per book+settings hash)
  state.json             progress metadata
  plan_c01.json          prosody plan per chapter (cached)
  chunks/<hash>.audio    per-paragraph audio (enables --resume)
output/<Book Title>/
  01 - Chapter.mp3 ...
  manifest.json          durations & settings for the user
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .audio import assemble_chapter, assemble_m4b, human_duration, probe_duration
from .config import Config
from .extract import extract_book
from .models import Book
from .prosody.llm import OllamaProsody
from .prosody.rules import RulesEngine
from .prosody.ssml import build_plain_text, build_sapi_xml, build_ssml, compute_global_prosody
from .segment import split_sentences
from .tts.base import BaseTTS, ChunkJob

log = logging.getLogger("b2a.pipeline")


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text).strip()
    text = re.sub(r"[\s_]+", "-", text)
    return text[:60] or "book"


def settings_hash(cfg: Config) -> str:
    material = json.dumps({
        "voice": cfg.get_path("tts.voice"),
        "speed": cfg.get_path("tts.speed"),
        "pitch": cfg.get_path("tts.pitch"),
        "tempo": cfg.get_path("tts.tempo"),
        "engine": cfg.get_path("tts.engine"),
        "style": cfg.get_path("style"),
        "llm_model": cfg.get_path("llm.model"),
        "piper_voice": cfg.get_path("tts.piper.voice"),
        "xtts_clone": cfg.get_path("tts.xtts.clone_voice"),
        "xtts_speaker": cfg.get_path("tts.xtts.speaker"),
        "pauses": cfg.get_path("pauses", {}),
        "power_words": cfg.get_path("emphasis.power_words", []),
        "max_per_sentence": cfg.get_path("emphasis.max_per_sentence", 2),
        "volume_dbfs_target": cfg.get_path("tts.volume_dbfs_target", -19),
        "mp3_bitrate": cfg.get_path("tts.mp3_bitrate", "96k"),
    }, sort_keys=True)
    return hashlib.sha1(material.encode()).hexdigest()[:8]


class Pipeline:
    def __init__(self, cfg: Config, engine: BaseTTS, out_root: Path):
        self.cfg = cfg
        self.engine = engine
        self.out_root = Path(out_root)
        self.force: bool = False
        self.prosody = self._make_prosody()

    def _make_prosody(self):
        provider = (self.cfg.get_path("llm.provider", "auto") or "auto").lower()
        if provider == "off":
            return RulesEngine(self.cfg, self.cfg.get_path("style", "warm"))
        llm = OllamaProsody(self.cfg, self.cfg.get_path("style", "warm"))
        if llm.available():
            model = llm.pick_model()
            if model:
                llm.model = model
                log.info("Prosody brain: Ollama '%s' (%s)", model,
                         "GPU" if llm.gpu else "CPU")
                return llm
            log.warning("Ollama reachable but no model pulled -> rule engine. "
                        "Run: ollama pull qwen2.5:3b")
            return RulesEngine(self.cfg, self.cfg.get_path("style", "warm"))
        if provider == "ollama":
            log.warning("Ollama unreachable (llm.provider=ollama forced) -> rule engine")
        else:
            log.info("Ollama not running -> rule-based prosody (still fully local)")
        return RulesEngine(self.cfg, self.cfg.get_path("style", "warm"))

    # ------------------------------------------------------------------
    def convert(
        self,
        book_path: Path,
        force: bool = False,
        m4b: bool = False,
        chapters: Optional[List[int]] = None,
        progress: Optional[Callable[[str], None]] = None,
    ) -> dict:
        def say(msg: str):
            log.info(msg)
            if progress:
                progress(msg)

        book = extract_book(book_path, self.cfg)
        say(f"Parsed '{book.title}' — {len(book.chapters)} chapters, "
            f"{book.total_paragraphs} paragraphs")

        return self.render_book(book, force=force, m4b=m4b, chapters=chapters, say=say)

    def render_book(
        self,
        book: Book,
        force: bool = False,
        m4b: bool = False,
        chapters: Optional[List[int]] = None,
        say=None,
    ) -> dict:
        say = say or (lambda m: None)
        self.force = force
        out_book_dir = self.out_root / slugify(book.title)
        build_dir = self.out_root / "_build" / f"{slugify(book.title)}-{settings_hash(self.cfg)}"
        build_dir.mkdir(parents=True, exist_ok=True)

        gpros = compute_global_prosody(
            self.cfg.get_path("tts.speed", 12), self.cfg.get_path("tts.pitch", 2), self.cfg)
        pauses = self.cfg.get_path("pauses", {}) or {}
        style_name = self.cfg.get_path("style", "warm")
        tempo = float(self.cfg.get_path("tts.tempo", 1.0))
        bitrate = self.cfg.get_path("tts.mp3_bitrate", "96k")
        lufs = float(self.cfg.get_path("tts.volume_dbfs_target", -19))

        state_path = build_dir / "state.json"
        state: dict = {}

        chapter_results: List[dict] = []
        total_t0 = time.time()

        target_chapters = book.chapters
        if chapters:
            target_chapters = [c for c in book.chapters if c.index in chapters]
            if not target_chapters:
                say(f"Warning: Requested chapter(s) {chapters} not found; available: 1..{len(book.chapters)}")
                target_chapters = book.chapters[:1]

        for chapter in target_chapters:
            out_name = f"{chapter.index:02d} - {_safe(chapter.title)}.mp3"
            out_path = out_book_dir / out_name
            ch_wav = build_dir / f"chapter_{chapter.index:02d}.wav"
            if out_path.exists() and not force:
                dur = probe_duration(out_path)
                chapter_results.append({
                    "chapter": chapter.index, "title": chapter.title,
                    "file": str(out_path),
                    "wav_file": str(ch_wav) if ch_wav.exists() else None,
                    "seconds": dur, "skipped": True,
                })
                say(f"Chapter {chapter.index}: exists, skipped ({human_duration(dur)})")
                continue

            t0 = time.time()
            mp3, ch_wav, n_paras = self._render_chapter(
                book, chapter, build_dir, gpros, pauses, style_name,
                out_path=out_path, tempo=tempo, bitrate=bitrate, lufs=lufs, say=say)
            dur = probe_duration(mp3)
            chapter_results.append({
                "chapter": chapter.index, "title": chapter.title,
                "file": str(mp3),
                "wav_file": str(ch_wav) if ch_wav.exists() else None,
                "seconds": dur, "paragraphs": n_paras,
            })
            say(f"Chapter {chapter.index} '{chapter.title}': {n_paras} paragraphs -> "
                f"{human_duration(dur)}  ({time.time() - t0:.1f}s)")

        manifest = {
            "book": book.title,
            "author": book.author,
            "source": book.source_path,
            "engine": self.engine.name,
            "voice": self.engine.chosen_voice,
            "prosody": self.prosody.__class__.__name__,
            "style": style_name,
            "speed": self.cfg.get_path("tts.speed"),
            "pitch": self.cfg.get_path("tts.pitch"),
            "rate_pct": gpros["rate_pct"],
            "pitch_pct": gpros["pitch_pct"],
            "chapters": chapter_results,
            "total_seconds": sum(c.get("seconds") or 0 for c in chapter_results),
            "elapsed_s": round(time.time() - total_t0, 1),
        }
        if m4b and chapter_results:
            m4b_path = out_book_dir / f"{slugify(book.title)}.m4b"
            # Discover cover image if present
            cover_cand = None
            explicit_cover = self.cfg.get_path("book.cover")
            if explicit_cover and Path(explicit_cover).is_file():
                cover_cand = Path(explicit_cover)
            elif book.source_path:
                src_dir = Path(book.source_path).parent
                for name in ("cover.jpg", "cover.png", "cover.jpeg", "folder.jpg"):
                    if (src_dir / name).is_file():
                        cover_cand = src_dir / name
                        break
            if not cover_cand:
                for name in ("cover.jpg", "cover.png", "cover.jpeg"):
                    if Path(name).is_file():
                        cover_cand = Path(name)
                        break

            say(f"Assembling M4B audiobook (direct lossless WAV -> AAC): {m4b_path.name} ...")
            assemble_m4b(chapter_results, m4b_path, book.title, book.author,
                         bitrate=bitrate, cover_image=cover_cand)
            manifest["m4b_file"] = str(m4b_path)
            if cover_cand:
                manifest["cover_image"] = str(cover_cand)
                say(f"M4B audiobook ready with embedded cover art: {m4b_path.name}")
            else:
                say(f"M4B audiobook ready: {m4b_path.name}")

        out_book_dir.mkdir(parents=True, exist_ok=True)
        (out_book_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

        _save_state(state_path, {"last_settings_hash": settings_hash(self.cfg)})
        return manifest

    # ------------------------------------------------------------------
    def _render_chapter(self, book: Book, chapter, build_dir: Path,
                        gpros: dict, pauses: dict, style_name: str,
                        out_path: Path, tempo: float, bitrate: str, lufs: float,
                        say) -> tuple:
        chunks_dir = build_dir / "chunks"
        chunks_dir.mkdir(exist_ok=True)

        # ---- 1. prosody plan (cached) ----
        plan_path = build_dir / f"plan_c{chapter.index:02d}.json"
        if plan_path.exists():
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        else:
            if isinstance(self.prosody, OllamaProsody):
                say(f"Chapter {chapter.index}: planning prosody with "
                    f"Ollama '{self.prosody.model}' ...")
                t0 = time.time()
                segments = self.prosody.plan_chapter(chapter)
                say(f"Chapter {chapter.index}: prosody planned "
                    f"({time.time() - t0:.1f}s)")
            else:
                segments = self.prosody.plan_chapter(chapter)
            plan = [s.to_dict() for s in segments]
            plan_path.write_text(json.dumps(plan, indent=1, ensure_ascii=False),
                                 encoding="utf-8")
        plan_by_id = {p["para_id"]: p for p in plan}

        # ---- 2. build SSML per paragraph (skip already-synthesized chunks) ----
        jobs: List[ChunkJob] = []
        for para in chapter.paragraphs:
            seg_dict = plan_by_id.get(para.id, {})
            from .models import ProsodySegment
            seg = ProsodySegment(
                para_id=para.id,
                mood=seg_dict.get("mood", "neutral"),
                emphasis=seg_dict.get("emphasis", []),
                rate_delta=int(seg_dict.get("rate_delta", 0)),
                pitch_delta=int(seg_dict.get("pitch_delta", 0)),
                pause_after_ms=int(seg_dict.get("pause_after_ms", 400)),
                source=seg_dict.get("source", "rules"),
            )
            sentences = split_sentences(para.text,
                                        int(self.cfg.get_path("extract.max_sentence_chars", 400)))
            chunk_id = self._chunk_id(para.text, gpros, seg)
            chunk_file = self._chunk_file(chunks_dir, chunk_id)
            if chunk_file is not None and not self.force:
                continue  # cached audio
            if self.engine.name == "sapi":
                markup = build_sapi_xml(para.text, sentences, seg,
                                        gpros["rate_pct"], gpros["pitch_pct"],
                                        style_name, pauses)
            elif self.engine.name in ("piper", "xtts", "silent", "kokoro"):
                # no-SSML neural engines: plain text, prosody carried by punctuation
                # and per-paragraph rate_delta
                markup = build_plain_text(para.text, sentences, seg)
            else:
                markup = build_ssml(para.text, sentences, seg,
                                    gpros["rate_pct"], gpros["pitch_pct"],
                                    style_name, pauses)
            jobs.append(ChunkJob(id=chunk_id, text=para.text, ssml=markup,
                                 rate_delta=seg.rate_delta))

        # ---- 3. synthesize pending chunks ----
        if jobs:
            say(f"Chapter {chapter.index}: synthesizing {len(jobs)} paragraphs "
                f"with {self.engine.name}/{self.engine.chosen_voice} ...")
            results = self.engine.synthesize_batch(jobs, chunks_dir)
            failed = [r for r in results if not r.ok]
            for r in failed:
                log.error("chunk %s failed: %s", r.id, r.error)
            if failed and len(failed) == len(results):
                raise RuntimeError(
                    f"All {len(results)} chunks failed on engine '{self.engine.name}': "
                    f"{failed[0].error}")

        # ---- 4. assemble (in paragraph order!) ----
        from .audio import ffmpeg_available
        if not ffmpeg_available():
            raise RuntimeError("ffmpeg not found in PATH. Install: winget install Gyan.FFmpeg")

        ordered = []
        for para in chapter.paragraphs:
            seg_dict = plan_by_id.get(para.id, {})
            chunk_id = self._chunk_id(para.text, gpros, _shim(seg_dict))
            path = self._chunk_file(chunks_dir, chunk_id)
            if path is None:
                raise RuntimeError(f"chunk audio missing for {para.id} ({chunk_id})")
            ordered.append((path, int(seg_dict.get("pause_after_ms", 400))))

        ch_wav = build_dir / f"chapter_{chapter.index:02d}.wav"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        duration = assemble_chapter(
            ordered, out_path,
            title=f"{chapter.index:02d} {chapter.title}",
            album=book.title,
            artist=book.author,
            track=chapter.index,
            total_tracks=len(book.chapters),
            lead_silence_ms=int(pauses.get("chapter_gap_ms", 1200)),
            target_lufs=lufs,
            bitrate=bitrate,
            tempo=tempo,
            out_wav=ch_wav,
        )
        return out_path, ch_wav, len(chapter.paragraphs)

    # ------------------------------------------------------------------
    def _chunk_id(self, text: str, gpros: dict, seg) -> str:
        from .models import ProsodySegment
        if isinstance(seg, dict):
            seg = ProsodySegment(
                para_id=seg.get("para_id", ""), emphasis=seg.get("emphasis", []),
                rate_delta=seg.get("rate_delta", 0), pitch_delta=seg.get("pitch_delta", 0),
                pause_after_ms=seg.get("pause_after_ms", 400))
        speed_val = self.cfg.get_path("tts.kokoro.speed") if self.engine.name == "kokoro" else self.cfg.get_path("tts.speed", 10)
        material = json.dumps({
            "t": text, "r": gpros["rate_pct"], "p": gpros["pitch_pct"],
            "e": seg.emphasis, "sr": seg.rate_delta, "sp": seg.pitch_delta,
            "engine": self.engine.name, "voice": self.engine.chosen_voice,
            "speed": speed_val,
            "pauses": self.cfg.get_path("pauses", {}),
        }, sort_keys=True)
        return hashlib.sha1(material.encode()).hexdigest()[:16]

    def _chunk_file(self, chunks_dir: Path, chunk_id: str):
        for ext in (".audio", ".wav"):
            p = chunks_dir / f"{chunk_id}{ext}"
            if p.exists() and p.stat().st_size > 0:
                return p
        return None


def _shim(seg_dict: dict):
    return seg_dict  # chunk-id path accepts dicts


def _safe(title: str) -> str:
    clean = re.sub(r"<[^>]+>", "", title)
    return re.sub(r'[<>:"/\\|?*]', "", clean).strip() or "Chapter"


def _save_state(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
