# book2audio — Detailed Project Plan

**Goal:** convert any book the user owns (PDF / EPUB / MD / TXT) into a
realistic audiobook with natural pitch, voice, emphasis on meaning-carrying
words and dramatic pauses — conveying the *motivation behind each sentence* —
using **only local, free, offline components**. Personal & family use only.

---

## 1. Requirements & constraints

| # | Requirement | Decision |
|---|---|---|
| R1 | Input formats: PDF, EPUB, MD (TXT bonus) | pymupdf (TOC-aware), ebooklib+bs4 (spine order), regex splitter |
| R2 | Realistic pitch/voice/emphasis | Ollama LLM prosody planner → SSML `prosody` + `break` |
| R3 | "Microsoft Ava voice, Windows 11" | WinRT `Windows.Media.SpeechSynthesis` via PowerShell (natural voices are invisible to SAPI/System.Speech) |
| R4 | "speed 12" | user scale 0–20 (10 = normal) → SSML rate `+20%`; pitch scale −10…+10 → `+N%` |
| R5 | Everything local, nothing paid | Ollama + WinRT voices + Piper + ffmpeg; zero network calls at runtime (exception: one-time Piper model download) |
| R6 | Chapter MP3 output | ffmpeg loudnorm + libmp3lame per chapter + manifest.json |
| R7 | CLI interface | argparse: convert / list-voices / doctor / demo |
| R8 | Personal/family use | license note, no voice files bundled, ID3 comment tag |

**Deliberately rejected:** external cloud token gateways and remote proxies.
They require transmitting book text and private documents off-device to third-party
servers. To keep personal audiobooks 100% private, offline, and reliable, the
architecture is strictly local-first: Ollama + Windows Natural voices + Piper.

## 2. Architecture

```
┌──────────┐  ┌─────────┐  ┌──────────┐  ┌───────────────────────┐
│ extract  │─▶│ clean   │─▶│ segment  │─▶│ prosody planner       │
│ pdf/epub │  │ markup  │  │ sentence │  │ rules.py  (fast)      │
│ md/txt   │  │ urls... │  │ +clauses │  │ llm.py    (Ollama)    │
└──────────┘  └─────────┘  └──────────┘  └──────────┬────────────┘
                                                    │ ProsodySegment{}
                                                    ▼
┌──────────────────────────┐   ┌──────────┐   ┌─────────────┐
│ audio.py                 │◀──│ tts/*    │◀──│ ssml.py     │
│ stitch + loudnorm → mp3  │   │ winrt    │   │ SSML/SAPI/  │
└────────────┬─────────────┘   │ sapi     │   │ plain text  │
             ▼                 │ piper    │   └─────────────┘
   output/Book/NN - Title.mp3  └──────────┘
   manifest.json
```

Key mechanism — **emphasis without `<emphasis>`**: Windows voices support
SSML prosody reliably but `<emphasis>` poorly, so word stress = wrap word in
`<prosody rate="-12%" pitch="+6%">` (slower + slightly brighter). This is the
trick that makes individual words *feel* stressed on Ava.

## 3. Prosody design

### 3.1 The ProsodySegment contract
Per paragraph: `mood` (8 enums), `emphasis` (≤4 verbatim words), `rate_delta`,
`pitch_delta` (pp vs global), `pause_after_ms` (0–1500).

### 3.2 LLM planner (llm.py)
* Ollama `/api/chat`, `format: <JSON schema>` (structured outputs, enforced),
  temperature 0.2, batch of 3 paragraphs per call.
* System prompt = narration director rules (warm style guidance).
* Auto model pick: nvidia-smi probe → GPU list (7B class) or CPU list (3B
  class) intersected with `ollama list`; never downloads anything.
* Validation: emphasis must appear verbatim in the paragraph; deltas clamped;
  any failure → rule engine for that batch (never breaks the run).
* Plans cached per chapter (`plan_cNN.json`) — LLM runs once per book.

### 3.3 Rule planner (rules.py)
Signals: dialogue quotes → dialogue mood; `?` → question; `!` → excited;
`…`/long reflective paragraphs → slower; CAPS words always emphasized; power
word list (configurable); em-dash → 600 ms beat. Style profiles (styles.py)
scale pauses and deltas: `warm` / `motivational` / `storyteller` / `minimal`.

### 3.4 SSML builder (ssml.py)
* Global: rate/pitch percentages from speed/pitch scales.
* Sentence `<break time="380ms">` (style-scaled), ellipsis → `+800ms`,
  clause breaks at `; : —` → 150 ms.
* Emphasis spans found case-insensitively with word-boundary checks, capped
  at 2 per sentence (more sounds mechanical).
* SAPI flavor (`<rate speed>`, `<pitch middle>`, `<silence msec>`) and plain
  text flavor (Piper) generated from the same plan.

## 4. TTS backends

| Engine | Access | Why PowerShell? |
|---|---|---|
| winrt (Ava...) | `Windows.Media.SpeechSynthesis` | WinRT projection from Python (pywinrt/winsdk) is version-fragile; PS 5.1 is preinstalled and fully WinRT-capable. Batch job-file design amortizes the ~1 s interpreter start over a whole chapter; per-job try/catch + status JSON enables resume. |
| sapi | `System.Speech` (SSML via SpeakSsml) | fallback for machines without natural voices |
| piper | `PiperVoice.synthesize_wav` | cross-platform, CPU-only; model auto-download with 429 backoff |
| silent | `wave` module | pipeline/CI testing without any TTS |

Natural voices caveat documented: they're Microsoft's Narrator voices,
licensed for personal use on-device; availability to third-party apps can
vary by Windows build (`doctor` + `list-voices` make the state visible;
engine auto-falls back).

## 5. Audio pipeline (audio.py)
1. decode each chunk with format auto-probe (WinRT streams may be wav *or* mp3
   depending on voice — `.audio` extension, content sniffed)
2. resample/channels to the first chunk's rate, mono
3. insert paragraph pauses + chapter lead-in silence
4. `ffmpeg loudnorm I=-19 TP=-1.5 LRA=11` (optional `atempo` guarantee)
5. `libmp3lame 96k mono` + ID3 tags (title/album/artist/track)

## 6. Pipeline & caching (pipeline.py)
* `Book → Chapter → Paragraph` models; sentence split stored per paragraph.
* Chunk id = sha1(text + global rate/pitch + emphasis + deltas + engine +
  voice) — changes in narration settings produce a new cache automatically.
* Resume: existing chunk audio skipped; existing chapter MP3 skipped unless
  `--force`. Build artifacts live under `output/_build/<slug-hash>/`.
* `manifest.json`: chapters, durations, engine, voice, style, rate/pitch %.

## 7. Error strategy
* Extraction failure (scanned PDF) → explicit error, no silent empty output.
* Ollama missing/unresponsive → rule engine, run continues (warning logged).
* All chunks failing in a chapter → hard error naming engine + first error.
* PowerShell batch failures are per-chunk; only those paragraphs are retried
  on the next run (cache-aware).

## 8. Testing
* 35 pytest tests: cleanup, segmentation (abbreviations, ellipsis, overrun),
  rule prosody (mood/emphasis/pauses), SSML builder (mapping table, escaping,
  emphasis wrap, caps, SAPI flavor), full end-to-end with silent engine,
  resume behavior, plan caching.
* Live verification in development: end-to-end runs with silent engine
  (structure) and Piper engine (real speech, loudnorm verified −19 dB mean).

## 9. Performance estimates (typical novel ≈ 90 k words)
| Stage | CPU-only | GPU |
|---|---|---|
| extract+clean+segment | < 30 s | < 30 s |
| LLM prosody (~10 k paragraphs) | 40–70 min (3B) | 8–15 min (7B) |
| TTS synthesis (Ava) | ~0.5–1.5 s/paragraph ≈ 1.5–4 h | same |
| ffmpeg assembly | ~5 min | ~5 min |
Rules-only prosody removes the LLM stage entirely. Chapter-level resume makes
long runs interruptible.

## 10. Roadmap ideas
* M4B export with embedded chapter markers (ffmpeg `-map_metadata`)
* Local web UI (FastAPI) with drag-drop + progress bar
* Character-voice mapping for dialogue (pitch per speaker)
* Hindi/Hinglish voices (edge-tts equivalents offline via Piper hi_IN)
* `atempo` auto-fallback when a voice ignores SSML rate (detect via duration)
* EPUB cover → embedded MP3 cover art
