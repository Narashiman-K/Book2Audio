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
from .cover import find_cover
from .config import Config
from .extract import extract_book
from .models import Book
from .prosody.llm import OllamaProsody
from .prosody.rules import RulesEngine
from .prosody.ssml import build_plain_text, build_sapi_xml, build_ssml, compute_global_prosody
from .segment import split_sentences
from .tts.base import BaseTTS, ChunkJob

log = logging.getLogger("b2a.pipeline")


class BuildCancelled(Exception):
    """Raised when the user stops a build; finished chapters are kept for resume."""


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
        # on_progress(fraction 0..1, message); should_stop() -> bool
        self.on_progress: Optional[Callable[[float, str], None]] = None
        self.should_stop: Callable[[], bool] = lambda: False
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
        meta: Optional[dict] = None,
        cover: Optional[Path] = None,
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
            wanted = {int(i) for i in chapters}
            target_chapters = [c for c in book.chapters if c.index in wanted]
            if not target_chapters:
                raise ValueError(
                    f"None of the requested chapters {sorted(wanted)} exist; "
                    f"this book has chapters 1..{len(book.chapters)}")

        def emit(frac: float, msg: str):
            if self.on_progress:
                self.on_progress(max(0.0, min(1.0, frac)), msg)

        weights = {c.index: max(1, sum(len(p.text) for p in c.paragraphs)) for c in target_chapters}
        total_w = float(sum(weights.values()))
        render_share = 0.90 if m4b else 1.0
        done_w = 0.0
        n_total = len(target_chapters)

        for n_done, chapter in enumerate(target_chapters, start=1):
            if self.should_stop():
                raise BuildCancelled()
            out_name = f"{chapter.index:02d} - {_safe(chapter.title)}.mp3"
            out_path = out_book_dir / out_name
            ch_wav = build_dir / f"chapter_{chapter.index:02d}.wav"
            base_w = done_w

            def ch_progress(f, msg, _base=base_w, _w=weights[chapter.index], _n=n_done):
                emit(render_share * (_base + _w * f) / total_w,
                     f"Chapter {_n}/{n_total}: {msg}")

            self._ch_progress = ch_progress
            if out_path.exists() and ch_wav.exists() and not force:
                dur = probe_duration(out_path)
                chapter_results.append({
                    "chapter": chapter.index, "title": chapter.title,
                    "file": str(out_path),
                    "wav_file": str(ch_wav) if ch_wav.exists() else None,
                    "seconds": dur, "skipped": True,
                })
                say(f"Chapter {chapter.index}: already built, reusing ({human_duration(dur)})")
                done_w += weights[chapter.index]
                emit(render_share * done_w / total_w, f"Chapter {n_done}/{n_total}: already built")
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
            done_w += weights[chapter.index]
            emit(render_share * done_w / total_w, f"Chapter {n_done}/{n_total} done")

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
            if self.should_stop():
                raise BuildCancelled()
            idxs = [c["chapter"] for c in chapter_results]
            slug = slugify(book.title)
            full = len(idxs) == len(book.chapters)
            if full:
                m4b_path = out_book_dir / f"{slug}.m4b"
            elif idxs == list(range(idxs[0], idxs[-1] + 1)):
                m4b_path = out_book_dir / f"{slug} (ch {idxs[0]}-{idxs[-1]}).m4b"
            else:
                m4b_path = out_book_dir / f"{slug} (selected chapters).m4b"

            cover_cand = Path(cover) if cover and Path(cover).is_file() else None
            if not cover_cand and book.source_path:
                cover_cand = find_cover(Path(book.source_path), out_book_dir)

            meta_full = {"title": book.title, "author": book.author,
                         "narrator": f"Kokoro {self.engine.chosen_voice}"
                                     if self.engine.name == "kokoro" else self.engine.chosen_voice}
            meta_full.update({k: v for k, v in (meta or {}).items() if v not in (None, "")})
            titles = (meta or {}).get("chapter_titles") or {}
            for cr in chapter_results:
                custom = titles.get(str(cr["chapter"]))
                if custom:
                    cr["title"] = custom

            say(f"Assembling M4B: {m4b_path.name} ...")
            marks: list = []
            assemble_m4b(chapter_results, m4b_path, meta=meta_full, bitrate=bitrate,
                         cover_image=cover_cand, marks_out=marks,
                         progress_cb=lambda f: emit(0.90 + 0.10 * f, "Assembling M4B"))
            manifest["m4b_file"] = str(m4b_path)
            if cover_cand:
                manifest["cover_image"] = str(cover_cand)

            timeline = self._book_timeline(book, build_dir, chapter_results, marks, tempo)
            sidecar = {
                "version": 1,
                "m4b": m4b_path.name,
                "meta": meta_full,
                "settings": {"voice": self.engine.chosen_voice,
                             "speed": self.cfg.get_path("tts.kokoro.speed"),
                             "pitch": self.cfg.get_path("tts.kokoro.pitch"),
                             "bitrate": bitrate},
                "cover": cover_cand.name if cover_cand else "",
                "cover_path": str(cover_cand) if cover_cand else "",
                "duration": marks[-1]["end"] if marks else 0,
                "chapters": [dict(m, mp3=Path(cr["file"]).name)
                             for m, cr in zip(marks, chapter_results)],
                "timeline": timeline,
                "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            m4b_path.with_suffix(".b2a.json").write_text(
                json.dumps(sidecar, indent=1, ensure_ascii=False), encoding="utf-8")
            say(f"M4B ready: {m4b_path.name}"
                + (" (with cover art)" if cover_cand else " (no cover found)"))
            emit(1.0, "M4B ready")

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
        plan = None
        if plan_path.exists():
            try:
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                have = {p.get("para_id") for p in plan}
                if not all(par.id in have for par in chapter.paragraphs):
                    plan = None  # stale plan from a different chapter grouping
            except Exception:
                plan = None
        if plan is None:
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
            results = []
            step = 6
            cp = getattr(self, "_ch_progress", None)
            for i in range(0, len(jobs), step):
                if self.should_stop():
                    raise BuildCancelled()
                results.extend(self.engine.synthesize_batch(jobs[i:i + step], chunks_dir))
                if cp:
                    cp(min(0.95, (i + step) / len(jobs)),
                       f"narrating paragraph {min(i + step, len(jobs))}/{len(jobs)}")
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
        timeline = []
        lead_gap_ms = int(pauses.get("chapter_gap_ms", 1200))
        cur_pos_ms = lead_gap_ms

        for para in chapter.paragraphs:
            seg_dict = plan_by_id.get(para.id, {})
            chunk_id = self._chunk_id(para.text, gpros, _shim(seg_dict))
            path = self._chunk_file(chunks_dir, chunk_id)
            if path is None:
                raise RuntimeError(f"chunk audio missing for {para.id} ({chunk_id})")
            pause_ms = int(seg_dict.get("pause_after_ms", 400))
            ordered.append((path, pause_ms))

            chunk_dur_sec = probe_duration(path)
            start_sec = cur_pos_ms / 1000.0
            end_sec = start_sec + chunk_dur_sec
            timeline.append({
                "para_id": para.id,
                "start": round(start_sec, 2),
                "end": round(end_sec, 2),
            })
            cur_pos_ms += int(chunk_dur_sec * 1000) + pause_ms

        timeline_path = build_dir / f"timeline_c{chapter.index:02d}.json"
        try:
            timeline_path.write_text(json.dumps(timeline, indent=1), encoding="utf-8")
        except Exception as e:
            log.warning("Could not write timeline: %s", e)

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
    def _book_timeline(self, book: Book, build_dir: Path, chapter_results: List[dict],
                       marks: List[dict], tempo: float) -> dict:
        """paragraph id -> [start, end] seconds inside the finished M4B."""
        by_index = {c.index: c for c in book.chapters}
        scale = 1.0 / tempo if tempo and abs(tempo - 1.0) > 0.01 else 1.0
        out: dict = {}
        for cr, mk in zip(chapter_results, marks):
            ch = by_index.get(cr["chapter"])
            if not ch:
                continue
            tl_path = build_dir / f"timeline_c{ch.index:02d}.json"
            tl = None
            if tl_path.exists():
                try:
                    tl = json.loads(tl_path.read_text(encoding="utf-8"))
                except Exception:
                    tl = None
            have = {t["para_id"]: t for t in tl} if tl else {}
            if tl and all(p.id in have for p in ch.paragraphs):
                for p in ch.paragraphs:
                    t = have[p.id]
                    out[p.id] = [round(mk["start"] + t["start"] * scale, 2),
                                 round(min(mk["end"], mk["start"] + t["end"] * scale), 2)]
            else:  # no exact timings (older build): spread by characters
                total = float(sum(len(p.text) for p in ch.paragraphs)) or 1.0
                span = mk["end"] - mk["start"]
                cur = mk["start"]
                for p in ch.paragraphs:
                    d = span * len(p.text) / total
                    out[p.id] = [round(cur, 2), round(cur + d, 2)]
                    cur += d
        return out

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
