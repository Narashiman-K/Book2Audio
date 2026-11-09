"""book2audio command line interface."""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

from .config import apply_overrides, load_config
from .pipeline import Pipeline, slugify


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_convert(args) -> int:
    cfg = load_config(args.config)
    overrides = {
        "tts.voice": args.voice,
        "tts.speed": args.speed,
        "tts.pitch": args.pitch,
        "tts.tempo": args.tempo,
        "style": args.style,
        "tts.engine": args.engine,
        "llm.provider": args.llm,
        "llm.model": args.ollama_model,
        "pipeline.workers": args.workers,
        "tts.xtts.clone_voice": args.clone_voice,
    }
    apply_overrides(cfg, overrides)

    _setup_logging(args.verbose)
    from .tts import create_engine

    engine = create_engine(cfg, cfg.get_path("style", "warm"), prefer=args.engine)
    logging.getLogger("b2a.cli").info(
        "Engine: %s | voice: %s | speed %s (rate %+d%%) | pitch %+d%% | style: %s",
        engine.name, engine.chosen_voice, cfg.get_path("tts.speed"),
        (cfg.get_path("tts.speed", 10) - 10) * 10,
        cfg.get_path("tts.pitch", 0) * 6,
        cfg.get_path("style", "warm"),
    )
    if engine.name == "silent":
        logging.getLogger("b2a.cli").warning(
            "No TTS engine available — producing SILENT audio. On Windows install "
            "natural voices (Settings > Accessibility > Narrator) or Piper.")

    pipeline = Pipeline(cfg, engine, Path(args.out))
    manifest = pipeline.convert(Path(args.book), force=args.force, m4b=getattr(args, "m4b", False))
    print()
    print("=" * 62)
    print(f"  Audiobook ready: {args.out}/{slugify(manifest['book'])}/")
    if manifest.get("m4b_file"):
        print(f"  Single M4B file: {manifest['m4b_file']}")
    total = manifest.get("total_seconds", 0)
    h, rem = divmod(int(total), 3600)
    m, s = divmod(rem, 60)
    print(f"  {len(manifest['chapters'])} chapters  |  total {h}h {m:02d}m {s:02d}s"
          f"  |  built in {manifest.get('elapsed_s', 0)}s")
    print(f"  manifest: {args.out}/{slugify(manifest['book'])}/manifest.json")
    print("=" * 62)
    return 0


def cmd_list_voices(args) -> int:
    cfg = load_config(args.config)
    _setup_logging(False)
    from .tts import create_engine

    engine = create_engine(cfg, "warm", prefer=args.engine)
    voices = engine.list_voices()
    print(f"Engine: {engine.name}")
    for v in voices:
        print(f"  - {v.get('name')}  [{v.get('language')}]  {v.get('gender', '')}")
    if not voices:
        print("  (none found)")
    return 0


def cmd_doctor(args) -> int:
    cfg = load_config(args.config)
    _setup_logging(False)
    print("book2audio doctor — environment check\n")

    ok_all = True

    # system & platform
    import platform
    cpu_arch = platform.machine()
    proc_desc = os.environ.get("PROCESSOR_IDENTIFIER", "") or platform.processor()
    print(f"[+] System: Python {sys.version.split()[0]} on {platform.system()} ({cpu_arch})")
    if proc_desc:
        print(f"    CPU info: {proc_desc[:65]}")

    # ffmpeg
    from .audio import find_ffmpeg_info
    cfg_ffmpeg = cfg.get_path("tools.ffmpeg", "") or cfg.get_path("audio.ffmpeg_path", "")
    ff_path, ff_source = find_ffmpeg_info(cfg_ffmpeg)
    if Path(ff_path).is_file() or shutil.which(ff_path):
        print(f"[+] ffmpeg found ({ff_source}): {ff_path}")
    else:
        ok_all = False
        print("[-] ffmpeg NOT found. Install:  winget install Gyan.FFmpeg")

    # tts engines
    from .tts.sapi import SapiTTS
    from .tts.silent import SilentTTS
    from .tts.windows import WindowsWinrtTTS

    win = WindowsWinrtTTS(cfg)
    sapi = SapiTTS(cfg)
    if win.available():
        voices = win.list_voices()
        natural = [v for v in voices if "natural" in v.get("name", "").lower()]
        print(f"[+] WinRT voices: {len(voices)} installed "
              f"({len(natural)} natural — e.g. {[v['name'] for v in natural][:2]})")
        wanted = cfg.get_path("tts.voice", "Ava")
        has_wanted = any(wanted.lower() in v["name"].lower() for v in voices)
        has_ava = any("ava" in v["name"].lower() for v in voices)
        has_andrew = any("andrew" in v["name"].lower() for v in voices)
        if has_wanted:
            print(f"[+] Active voice '{wanted}' is installed and available.")
        else:
            print(f"[-] Configured voice '{wanted}' not found in WinRT.")
        if not (has_ava or has_andrew):
            print("    [!] Windows 11 Natural voices (Ava / Andrew) not yet installed.")
            print("        Install free in Windows: Settings > Accessibility > Narrator")
            print("        > 'Add natural voices' > Download 'Microsoft Ava (Natural)'.")
    else:
        if sys.platform == "win32":
            ok_all = False
            print("[-] WinRT voices unavailable (unexpected on Windows).")
        else:
            print("[i] WinRT voices: not on Windows (piper/silent will be used)")
    if sys.platform == "win32" and sapi.available():
        print("[+] SAPI fallback voices:", [v["name"] for v in sapi.list_voices()][:3])

    # piper
    try:
        import piper  # noqa: F401
        print("[+] Piper TTS installed (offline cross-platform fallback)")
    except Exception:
        print("[i] Piper not installed (optional):  pip install piper-tts")

    # voice cloning (optional)
    try:
        import TTS  # noqa: F401
        gpu = False
        try:
            import torch
            gpu = torch.cuda.is_available()
        except Exception:
            pass
        print(f"[+] Voice cloning (XTTS) installed — torch GPU: {gpu}")
        clone = cfg.get_path("tts.xtts.clone_voice", "")
        if clone:
            if Path(clone).exists():
                from .tts.xtts_local import XttsTTS
                dur = XttsTTS.sample_duration_seconds(clone)
                hint = "" if dur >= 6 else "  (short sample — 10-60 s reads better)"
                print(f"[+] Clone voice sample: {clone} ({dur:.1f}s){hint}")
            else:
                ok_all = False
                print(f"[-] Clone voice sample not found: {clone}")
        else:
            print("[i] No clone sample set (tts.xtts.clone_voice / --clone-voice) "
                  "— XTTS would use a built-in narrator")
    except Exception:
        print("[i] Voice cloning not installed (optional, ~2.5 GB with PyTorch):  "
              "pip install coqui-tts")

    # ollama
    import requests
    from .prosody.llm import OllamaProsody
    url = cfg.get_path("llm.url", "http://localhost:11434")
    try:
        r = requests.get(f"{url}/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        prosody = OllamaProsody(cfg)
        picked = prosody.pick_model()
        if picked:
            print(f"[+] Ollama reachable at {url}: model '{picked}' selected ({len(models)} models installed)")
        else:
            print(f"[i] Ollama reachable at {url}: {len(models)} model(s) installed {models[:4]}")
            print("    [!] No safe non-reasoning instruction model found (reasoning models like gpt-oss/qwen3 skipped).")
            print("        Rule-based prosody engine will be used.")
    except Exception:
        print("[i] Ollama not running — rule-based prosody will be used "
              "(install: https://ollama.com, then: ollama pull llama3.1:8b)")

    # 1-sentence synthesis probe test
    print("\n[+] Running 1-sentence audio synthesis probe...")
    try:
        from .tts import create_engine
        from .tts.base import ChunkJob
        import tempfile
        import time
        engine = create_engine(cfg, cfg.get_path("style", "warm"))
        with tempfile.TemporaryDirectory() as td:
            t0 = time.time()
            res = engine.synthesize_batch(
                [ChunkJob(id="probe", text="Audiobook probe verification successful.", ssml="Audiobook probe verification successful.")],
                Path(td)
            )
            t_taken = time.time() - t0
            if res and res[0].ok and Path(res[0].path).stat().st_size > 100:
                print(f"[+] Synthesis probe passed [{engine.name} / {engine.chosen_voice}]: "
                      f"{t_taken:.2f}s ({Path(res[0].path).stat().st_size} bytes)")
            else:
                print(f"[-] Synthesis probe failed: {res[0].error if res else 'no output'}")
    except Exception as e:
        print(f"[-] Synthesis probe error: {e}")

    print()
    print("All core checks passed." if ok_all else
          "Some required pieces are missing — see [-] lines above.")
    return 0 if ok_all else 1


def cmd_bench(args) -> int:
    """Benchmark LLM planning and TTS synthesis throughput on 5 paragraphs."""
    import time
    import tempfile
    from .config import apply_overrides, load_config
    from .extract import extract_book
    from .prosody.llm import OllamaProsody
    from .prosody.rules import RulesEngine
    from .prosody.ssml import build_ssml
    from .tts.base import ChunkJob
    from .tts import create_engine

    cfg = load_config(args.config)
    overrides = {}
    if getattr(args, "engine", None):
        overrides["tts.engine"] = args.engine
    if getattr(args, "voice", None):
        overrides["tts.voice"] = args.voice
    if getattr(args, "llm", None):
        overrides["llm.provider"] = args.llm
    if getattr(args, "clone_voice", None):
        overrides["tts.xtts.clone_voice"] = args.clone_voice
        overrides["tts.engine"] = "xtts"
    apply_overrides(cfg, overrides)

    sample_file = args.book or (Path(__file__).resolve().parent.parent / "samples" / "the_lantern_keeper.md")
    book = extract_book(Path(sample_file), cfg)
    all_paras = []
    for ch in book.chapters:
        all_paras.extend(ch.paragraphs)
        if len(all_paras) >= 5:
            break
    sample_paras = all_paras[:5]
    if not sample_paras:
        print("[-] Error: No paragraphs found to benchmark.")
        return 1

    total_words = sum(len(p.text.split()) for p in sample_paras)
    total_chars = sum(len(p.text) for p in sample_paras)

    print("=========================================================")
    print("                book2audio Benchmark                     ")
    print("=========================================================")
    print(f"Input Sample: 5 real paragraphs ({total_words} words, {total_chars} characters)\n")

    # 1. Benchmark Prosody Planner
    llm_provider = cfg.get_path("llm.provider", "auto")
    t0 = time.time()
    if llm_provider == "off":
        planner = RulesEngine(cfg, "warm")
        plans = [planner.plan_paragraph(p.text, p.id) for p in sample_paras]
        planner_name = "Rule-based engine"
    else:
        llm = OllamaProsody(cfg, "warm")
        model = llm.pick_model() if llm.available() else None
        if model:
            llm.model = model
            planner_name = f"Ollama ({model})"
            plans = [llm.plan_paragraph(p.text, p.id) for p in sample_paras]
        else:
            planner = RulesEngine(cfg, "warm")
            planner_name = "Rule-based engine (Ollama skipped)"
            plans = [planner.plan_paragraph(p.text, p.id) for p in sample_paras]
    t_plan = time.time() - t0
    plan_wps = total_words / max(0.001, t_plan)
    print(f"[1/2] Prosody Planning [{planner_name}]:")
    print(f"      Time taken:   {t_plan:.2f}s  ({plan_wps:.1f} words/sec)\n")

    # 2. Benchmark TTS Synthesis
    engine = create_engine(cfg, cfg.get_path("style", "warm"))
    print(f"[2/2] TTS Synthesis [{engine.name} / {engine.chosen_voice}]:")

    from .prosody.ssml import build_ssml, build_plain_text, build_sapi_xml, compute_global_prosody
    from .segment import split_sentences
    gpros = compute_global_prosody(cfg.get_path("tts.speed", 12), cfg.get_path("tts.pitch", 2), cfg)
    pauses = cfg.get_path("pauses", {})
    style_name = cfg.get_path("style", "warm")

    jobs = []
    for p, plan in zip(sample_paras, plans):
        sentences = split_sentences(p.text, int(cfg.get_path("extract.max_sentence_chars", 200)))
        if engine.name == "sapi":
            markup = build_sapi_xml(p.text, sentences, plan, gpros["rate_pct"], gpros["pitch_pct"], style_name, pauses)
        elif engine.name in ("piper", "xtts", "silent"):
            markup = build_plain_text(p.text, sentences, plan)
        else:
            markup = build_ssml(p.text, sentences, plan, gpros["rate_pct"], gpros["pitch_pct"], style_name, pauses)
        jobs.append(ChunkJob(id=p.id, text=p.text, ssml=markup, rate_delta=plan.rate_delta))

    with tempfile.TemporaryDirectory() as td:
        t0 = time.time()
        results = engine.synthesize_batch(jobs, Path(td))
        t_synth = time.time() - t0

    ok_results = [r for r in results if r.ok]
    if not ok_results:
        print(f"      [-] Synthesis failed: {results[0].error if results else 'Unknown error'}")
        return 1

    synth_wps = total_words / max(0.001, t_synth)
    total_time = t_plan + t_synth
    overall_wps = total_words / max(0.001, total_time)

    print(f"      Time taken:   {t_synth:.2f}s  ({synth_wps:.1f} words/sec)")
    print(f"      Total Time:   {total_time:.2f}s  ({overall_wps:.1f} combined words/sec)\n")

    # 3. Projections for standard book lengths
    print("---------------------------------------------------------")
    print("              Projected Book Build Times                 ")
    print("---------------------------------------------------------")
    projections = [
        ("Short Story", 10_000),
        ("Novella", 40_000),
        ("Standard Novel", 90_000),
    ]
    for label, count in projections:
        proj_sec = count / max(0.001, overall_wps)
        mins = proj_sec / 60.0
        hrs = mins / 60.0
        if hrs >= 1.0:
            formatted = f"{hrs:.1f} hours ({mins:.0f} mins)"
        else:
            formatted = f"{mins:.1f} minutes"
        print(f"  * {label:18} ({count:,} words): {formatted}")
    print("=========================================================\n")
    return 0


def cmd_demo(args) -> int:
    """Convert the bundled sample book end-to-end."""
    cfg = load_config(args.config)
    _setup_logging(False)
    sample = Path(__file__).resolve().parent.parent / "samples" / "the_lantern_keeper.md"
    argv = ["book2audio", "convert", str(sample),
            "--out", args.out, "--engine", args.engine]
    if getattr(args, "clone_voice", None):
        argv += ["--clone-voice", args.clone_voice]
    sys.argv = argv
    return main_inner(sys.argv[1:])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="book2audio",
        description="Turn your books (PDF/EPUB/MD/TXT) into expressive audiobooks — "
                    "100%% local: Windows natural voices + Ollama + ffmpeg.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--config", help="path to config.yaml", default=None)
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("convert", help="convert a book to audiobook chapter MP3s")
    c.add_argument("book", help="path to .pdf/.epub/.md/.txt")
    c.add_argument("--out", default="output", help="output root directory")
    c.add_argument("--voice", help='voice name substring (default: Ava)')
    c.add_argument("--speed", type=float, help="0-20, 10=normal (12 = +20%%)")
    c.add_argument("--pitch", type=float, help="-10..10, 0=normal")
    c.add_argument("--style", choices=["warm", "motivational", "storyteller", "minimal"])
    c.add_argument("--engine", choices=["auto", "winrt", "sapi", "piper", "xtts", "silent"])
    c.add_argument("--clone-voice", metavar="WAV",
                   help="use YOUR voice: path to a 10-60 s WAV sample "
                        "(engine xtts; see README 'Use your own voice')")
    c.add_argument("--llm", choices=["auto", "ollama", "off"],
                   help="prosody brain: ollama = local LLM, off = rules")
    c.add_argument("--ollama-model", help='e.g. "qwen2.5:3b" (default: auto)')
    c.add_argument("--tempo", type=float, help="ffmpeg tempo guarantee (1.0 = off)")
    c.add_argument("--workers", type=int, help="parallel chapter workers")
    c.add_argument("--force", action="store_true", help="re-synthesize even if cached")
    c.add_argument("--m4b", action="store_true", help="also generate a single chaptered .m4b audiobook file")
    c.add_argument("-v", "--verbose", action="store_true")
    c.set_defaults(func=cmd_convert)

    lv = sub.add_parser("list-voices", help="list installed voices for the chosen engine")
    lv.add_argument("--engine", choices=["auto", "winrt", "sapi", "piper", "xtts", "silent"])
    lv.set_defaults(func=cmd_list_voices)

    d = sub.add_parser("doctor", help="check environment (ffmpeg, voices, ollama)")
    d.set_defaults(func=cmd_doctor)

    b = sub.add_parser("bench", help="benchmark LLM & TTS throughput and project novel build times")
    b.add_argument("book", nargs="?", help="optional book to benchmark (default: sample book)")
    b.add_argument("--engine", choices=["auto", "winrt", "sapi", "piper", "xtts", "silent"])
    b.add_argument("--voice", help="voice name substring")
    b.add_argument("--clone-voice", metavar="WAV", help="clone WAV sample for xtts benchmark")
    b.add_argument("--llm", choices=["auto", "ollama", "off"])
    b.set_defaults(func=cmd_bench)

    e = sub.add_parser("demo", help="convert the bundled sample book")
    e.add_argument("--out", default="output")
    e.add_argument("--engine", default="auto")
    e.add_argument("--clone-voice", metavar="WAV", help="clone this WAV voice sample")
    e.set_defaults(func=cmd_demo)
    return p


def main_inner(argv) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def main() -> int:
    return main_inner(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
