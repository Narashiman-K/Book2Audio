# book2audio — your books, narrated with feeling

Turn books you own (PDF, EPUB, Markdown, TXT) into expressive audiobooks:
a local LLM decides **which words to stress, where to breathe, and how the
mood of each paragraph shifts pitch and pace** — then Windows 11's natural
voices (Ava, Jenny, ...) speak it. Everything runs **100% on your machine**.

```
PDF/EPUB/MD/TXT ─▶ extract ─▶ clean ─▶ sentences ─▶ prosody brain ─▶ SSML ─▶ TTS ─▶ ffmpeg ─▶ chapter MP3s
                                                            │
                                              Ollama (local LLM) or rule engine
```

> **Personal & family use only.** You are producing audio of books you own for
> private listening. Do not distribute the generated files.

---

## Quick start (Windows 11)

### 1. One-time setup

```bat
:: ffmpeg (audio assembly)
winget install Gyan.FFmpeg

:: Ollama — the local prosody brain (no API keys, nothing leaves your PC)
winget install Ollama.Ollama
ollama pull qwen2.5:3b        :: 3B model, fine on CPU (8 GB RAM)
:: on a GPU machine prefer:   ollama pull qwen2.5:7b-instruct

:: install the natural voice "Ava" (works offline after download!)
:: Settings > Accessibility > Narrator > "Add natural voices" > Ava (English US)
```

### 2. Install book2audio

```bat
cd book2audio
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python book2audio.py doctor        :: verifies ffmpeg, voices, ollama
```

(Or just run `install.bat` which does all of the above.)

### 3. Make your first audiobook

```bat
python book2audio.py convert "My Book.epub" --voice Ava --speed 12
python book2audio.py demo                       :: try the bundled sample book
```

Output lands in `output\<Book Title>\01 - Chapter ....mp3` plus a
`manifest.json` with durations and settings.

---

## Use your own voice (voice cloning)

Yes — you can narrate the book **with your recorded voice**, fully locally:

```bat
:: 1. install the cloning engine (one-time, ~2.5 GB with PyTorch)
pip install coqui-tts

:: 2. record 10–60 s of yourself reading naturally (see recording tips below)
::    and save as WAV, e.g.  my_voice.wav

:: 3. convert
python book2audio.py convert "My Book.epub" --engine xtts --clone-voice my_voice.wav
```

What happens: the XTTS v2 model (≈1.8 GB, downloaded once into
`models/xtts/`, then **fully offline**) learns the timbre of your sample and
speaks every paragraph in your voice. The prosody brain still works — each
paragraph's rate change (urgent → slightly faster, solemn → slower) is
applied through XTTS's native speed control, pauses are inserted at assembly
time.

**Recording tips (Windows Voice Recorder or Audacity):**

* quiet room, 20–30 cm from the mic, no echo; read the way you would tell a
  story to a friend — the clone inherits your *delivery*, not just your timbre
* 10–60 s is enough for a good clone; 2–5 min is better; mono 16-bit WAV,
  any sample rate (it is resampled automatically)
* one clean take beats a long noisy one: no background music, no clipping

**Honest expectations:** output is 22–24 kHz — "podcast" quality, close to
Ava's natural voices but not studio HD. Cloning a *spoken* voice for *read*
narration sounds like you reading aloud; more/dramatic sample delivery →
more expressive book. The model license (Coqui CPML) allows personal/family
use; do not clone other people's voices without permission.

Full custom training (better fidelity, much more work) is also possible with
Piper's training pipeline — record 1–5 h, train your own `piper` voice model,
then drop it into `models/piper/`. See `piper` docs; the program picks it up
via `tts.piper.voice` in `config.yaml`.

## How emotional is the narration, really?

The program **tunes itself per paragraph and per sentence** — that is the
prosody brain's whole job:

1. Every paragraph gets a *mood* (warm / reflective / urgent / excited /
   solemn / dialogue / question) from the local Ollama LLM — or from the rule
   engine when Ollama is off. The mood shifts the paragraph's **pitch and
   rate** (hope → brighter, gravity → slower/deeper).
2. Within each sentence, 0–3 weight-carrying words get word-level **stress**
   (slower + brighter prosody lift), so the emotional load lands on the right
   words.
3. **Pauses breathe with the story**: short after commas, longer after
   sentences, up to 1.5 s around scene shifts and reveals.

Two honest ceilings, so you know what to expect:

* Windows natural voices (Ava) don't support actor-style emotions
  ("read this like you're crying"); expressiveness comes from pitch, pace,
  stress and timing — which is exactly what this pipeline controls. With
  Ollama on, the narration clearly breathes and leans; with `--llm off` it is
  competent but more uniform.
* For **more emotional depth than Ava can give**, use voice cloning with an
  expressive sample: XTTS carries the *feeling of your own delivery* into the
  whole book, on top of the prosody plan. That combination is the most
  human-sounding option in this project.

Tip: `--style motivational|storyteller` biases the LLM toward more dynamic
rate/pitch/pause choices than `warm`.

---

## What "expressive" means here

| Signal | Source | Effect in audio |
|---|---|---|
| Word stress | LLM picks 0–3 weight-carrying words per paragraph (rule engine: power words, CAPS, quoted phrases) | word wrapped in `<prosody rate="-12%" pitch="+6%">` → slower + brighter |
| Mood | warm / reflective / urgent / excited / solemn / dialogue / question | paragraph-level rate & pitch deltas |
| Pauses | punctuation + drama (ellipsis → 800 ms, scene shifts up to 1.5 s) | `<break time="…ms"/>` between sentences, silence between paragraphs |
| Global voice | `--speed 12` (0–20 scale, 10 = normal) → rate +20%; `--pitch +2` → +12% | whole-book prosody envelope |
| Loudness | ffmpeg `loudnorm` | every chapter at −19 LUFS, no volume jumps |

The narration style is selectable: `--style warm|motivational|storyteller|minimal`.

## Engines (auto-selected, all local)

| Engine | Voices | Where | Notes |
|---|---|---|---|
| **winrt** (default) | Ava, Jenny, Aria, ... "natural" | Windows 11 22H2+ | the Narrator natural voices — install via Settings, then they work offline. Reached through `Windows.Media.SpeechSynthesis` (WinRT) — **not** visible to SAPI. Full SSML prosody. |
| sapi | David, Zira Desktop | every Windows | legacy robotic fallback |
| piper | `en_US-lessac-medium` + more | any OS, CPU-only | `pip install piper-tts`; voice model auto-downloads once |
| **xtts** | **your own cloned voice** | any OS, GPU recommended | `pip install coqui-tts`; opt-in via `--engine xtts --clone-voice me.wav`; zero-shot cloning, model downloads once then offline |
| silent | — | CI/tests | timed silence, for pipeline testing |

## CLI

```
python book2audio.py convert BOOK [--out DIR] [--voice Ava] [--speed 12]
    [--pitch 2] [--style warm] [--engine auto|winrt|sapi|piper|xtts|silent]
    [--clone-voice my_voice.wav] [--llm auto|ollama|off]
    [--ollama-model qwen2.5:3b] [--tempo 1.0] [--force] [-v]
python book2audio.py list-voices [--engine ...]
python book2audio.py doctor
python book2audio.py demo [--engine ...] [--clone-voice me.wav]
```

* **Resume/caching** — every paragraph's audio is content-hashed; interrupted
  runs skip finished work. Change voice/speed/style → new cache, old one kept.
* `--llm off` → rule-engine prosody (instant, no Ollama needed).
* `--tempo 1.1` → hard guarantee: ffmpeg time-stretch (pitch-preserving) —
  useful if some voice ignores SSML rate.

## Why not third-party cloud token gateways?

External token proxies and routing gateways exist, but they are **cloud services**:
your book text and manuscripts would be sent to remote third-party providers.
To ensure 100% privacy and complete offline reliability for personal/family audiobooks,
this project is deliberately local-first: **Ollama + WinRT voices + Piper cover
everything locally at zero cost with zero data leaving your machine.** (If you ever
want to connect an external API, `b2a/prosody/llm.py` is the single file to adapt —
the LLM contract is a clean JSON schema call.)

## Licensing note

Windows natural voices are licensed by Microsoft for accessibility/personal
use on your device. Converting books you own for private listening is the
intended, fair use case; redistribution of generated audio is not. This
project ships no Microsoft voice files — it drives the voices installed
through Windows Settings.

## Layout

```
book2audio.py          CLI entry
config.yaml            voices, speed/pitch mapping, pauses, styles
b2a/
  extract.py           PDF (TOC-aware) / EPUB (spine) / MD / TXT → chapters
  clean.py             markup/URL/footnote stripping, hyphen wraps, speech expansions
  segment.py           sentence splitting (abbrev guard, ellipsis, overrun split)
  prosody/
    rules.py           deterministic prosody (no LLM)
    llm.py             Ollama structured-JSON prosody (GPU/CPU auto model pick)
    ssml.py            SSML + SAPI XML + plain-text builders, emphasis → prosody lift
    styles.py          warm / motivational / storyteller / minimal profiles
  tts/
    winrt.ps1          PowerShell WinRT batch engine (natural voices)
    windows.py         Python driver for the above (batched, resumable)
    sapi.ps1 / sapi.py legacy SAPI fallback
    piper_local.py     Piper neural TTS (auto model download)
    xtts_local.py      XTTS v2 zero-shot voice cloning (--clone-voice)
    silent.py          test engine
  audio.py             pydub stitching + ffmpeg loudnorm → chapter MP3
  pipeline.py          orchestration, hashing, resume, manifest
tests/                 48 unit + end-to-end tests
samples/               "The Lantern Keeper" sample book
```

Run tests: `python -m pytest tests/ -q`
