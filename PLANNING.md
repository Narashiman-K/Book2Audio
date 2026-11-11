# Book2Audio Project Planning & Evolution Manifesto

## 1. Project Overview & Vision
**Book2Audio** is an offline, privacy-first Neural Audiobook Creator and Speed-Reading Web Studio. It converts digital text documents—specifically **PDF, EPUB, Markdown, TXT**, and **Web Articles**—into studio-quality mastered audiobooks (`.m4b` chaptered format and 320 kbps CBR MP3) while offering a synchronized visual reading experience with sentence and word-level highlighting.

The primary mission of the project is to deliver **100% private, zero-cloud token, high-comprehension audio learning**. Unlike commercial cloud TTS solutions (such as ElevenLabs or Azure Speech API), Book2Audio runs entirely on local consumer hardware (CPU or GPU) with zero recurring costs, zero telemetry, and complete data confidentiality.

---

## 2. Phased Development Roadmap

The project followed a five-phase strategic evolution:

### Phase 1: Python Core Engine & Pipeline Architecture (Completed)
- **Goal**: Build a solid, extensible Python pipeline for document ingestion, text normalization, and audio synthesis.
- **Key Modules Developed**:
  - `b2a/extract.py`: Pluggable document extractors for PDF (PyMuPDF / fitz), EPUB (`ebooklib` + `BeautifulSoup4`), Markdown, and plain text.
  - `b2a/clean.py`: Robust cleaning pipeline removing page numbers, running headers/footers, XML/HTML tags, Markdown artifacts, and noise.
  - `b2a/segment.py`: Quote-aware sentence segmenter with protected abbreviation lists (e.g., `Mr.`, `Dr.`, `e.g.`, `i.e.`).
  - `b2a/prosody/rules.py` & `llm.py`: Dual prosody engine combining deterministic linguistic heuristics with local LLM tone inference (Ollama Qwen2.5 / Llama 3).
  - `b2a/audio.py`: Two-pass EBU R128 loudness normalization (`-19 LUFS`, `-1.5 dBFS` true peak), 90Hz high-pass filter, and lossless intermediate stitching.
  - `b2a/models.py`: Strongly typed dataclasses (`Chapter`, `Book`, `Sentence`, `ProsodyPlan`, `VoiceConfig`).
  - Comprehensive unit test suite (`tests/` with 56 passing tests).

### Phase 2: Neural TTS Integration & Studio Mastering (Completed)
- **Goal**: Integrate high-fidelity neural speech synthesis with human-like prosody and container packaging.
- **Key Capabilities Added**:
  - **Kokoro-82M ONNX Integration** (`b2a/tts/kokoro_engine.py`): StyleTTS 2 architecture, 54 expressive voices (American & British accents), 24 kHz native studio fidelity, running on local CPU/DirectML.
  - **Voice Cloning** (`b2a/tts/xtts_local.py`): Coqui XTTS v2 local speaker cloning using short WAV references.
  - **Windows Native Speech** (`b2a/tts/windows.py`, `winrt.ps1`, `sapi.ps1`): WinRT Natural Speech (Ava, Jenny, Guy) and SAPI desktop voices.
  - **Piper ONNX Engine** (`b2a/tts/piper_local.py`): Ultra-fast local neural synthesis.
  - **M4B Container Engine**: Full MPEG-4 Part 14 audio container generation with `FFMETADATA1` chapter markers and embedded cover art extraction (`b2a/cover.py`).

### Phase 3: Interactive Web Studio & Speed-Reading Engine (Completed)
- **Goal**: Provide a local web UI for reading, listening, auditioning, and real-time synchronized speed-reading.
- **Key Features Built**:
  - `b2a/studio.py` & `b2a/web/index.html`: Responsive Narration Studio running locally at `http://127.0.0.1:8080`.
  - **Sub-Second Speed-Reading Highlighting**: Real-time synchronized word and sentence highlighting matched to audio playback timestamps (`currentTime / duration`).
  - **Click-to-Read Interactive Navigation**: Clicking any sentence or paragraph in the text pane immediately seeks audio playback to that exact location.
  - **Unified `Books-Library`**: Centralized storage for all source documents and mastered `.m4b` files with automatic loading upon selection.
  - **Dynamic Audio Controls**: Live playback speed adjustment (0.5x to 2.0x) without pitch distortion, pitch modulation (+4 semitones), and auto-scroll tracking.

### Phase 4: WebRead Article Ingestion & Chrome Extension (Completed)
- **Goal**: Extend Book2Audio from static books to dynamic web content.
- **Key Modules Developed**:
  - `b2a/webread.py`: Local REST API endpoint (`/api/webread`) that fetches web articles, extracts core readable text via Readability algorithms, and strips clutter.
  - **SSRF Hardening**: Strict URL validation blocking private RFC-1918 ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16), loopback (`127.0.0.1`, `localhost`), link-local, and cloud metadata endpoints (`169.254.169.254`).
  - `extension/`: Chrome Manifest V3 browser extension providing 1-click article capture, instant narration, and synchronized reading.

### Phase 5: TypeScript Parity Monorepo & Cross-Platform Distribution (Active)
- **Goal**: Port the core engine to a pure TypeScript monorepo for native Node.js, Electron, and Next.js web deployment.
- **Key Packages**:
  - `book2audio-core-engine/packages/core`: Pure TypeScript implementation of text extractors, cleaner, segmentation, rules, and models with zero runtime dependencies.
  - `book2audio-core-engine/packages/engine-node`: Node.js runtime engine with Kokoro ONNX bindings, `pdf-parse`, and WAV audio assembler.
  - `book2audio-web`: Vercel-ready Next.js / React web application.
  - `book2audio-windows`: Standalone Windows desktop application.
  - `tools/gen_fixtures.py`: Automated parity test harness comparing Python and TypeScript outputs across identical input texts.

---

## 3. Test-Driven Parity Verification Strategy

To guarantee absolute functional parity between the Python reference engine (`book2audio-python`) and the TypeScript engine (`book2audio/book2audio-core-engine`), the project utilizes automated fixture generation:

```
[ Python Reference Engine ] ───> tools/gen_fixtures.py ───> test/fixtures/golden.json
                                                                    │
                                                                    ▼
[ TypeScript Engine (@b2a/core) ] ───> vitest parity.test.ts <───────┘
```

1. **Golden Fixtures**: `gen_fixtures.py` runs Python's `b2a.clean`, `b2a.segment`, and `b2a.prosody.rules` against real-world sample books and dumps normalized outputs into JSON test fixtures.
2. **Vitest Parity Suite**: TypeScript unit tests load these golden fixtures and assert 100% token-for-token equality in:
   - Header / footer stripping
   - Numbered list & footnote handling
   - Abbreviation-protected sentence boundary splitting
   - Dialogue detection and quotation attribution
   - SSML break and rate tag calculations
3. **Regression Safety**: Any modification to segmentation or cleaning rules must pass both the 56 Python `pytest` tests and the TypeScript `vitest` assertions before merging.

---

## 4. Key Architectural Decisions (ADRs)

| ADR ID | Decision | Rationale | Alternatives Considered |
|---|---|---|---|
| **ADR-001** | **Kokoro-82M as Primary Engine** | 82M parameters, StyleTTS 2 architecture, runs in real-time on local CPU via ONNX Runtime without GPU requirement. Exceptional natural human cadence. | Coqui XTTS (requires CUDA/GPU), Piper (faster but more robotic), Cloud APIs (privacy violation). |
| **ADR-002** | **Single .m4b Container with Chapters** | M4B is the universal standard for audiobooks (supported by Apple Books, VLC, Smart AudioBook Player), supports embedded AAC, chapter markers, and cover art. | Multi-file MP3 folders (disorganized), raw WAV (too large), Opus (poor chapter support in standard players). |
| **ADR-003** | **Two-Pass EBU R128 Loudness Normalization** | Guarantees consistent professional broadcast loudness (-19 LUFS for audiobooks, -1.5 dBFS true peak), eliminating volume jumps between chapters or speech pauses. | Simple peak normalization (causes quiet passages to remain too low), no normalization (unbalanced). |
| **ADR-004** | **100% Offline Localhost Architecture** | User data, uploaded books, synthesized audio, and voice cloning references never leave `127.0.0.1`. Zero cloud telemetry or external API dependencies. | Hosted SaaS API (privacy hazard, expensive API bills). |
| **ADR-005** | **Dynamic Speed-Reading Text Sync** | Computes continuous playback position via native Web Audio API and highlights words/sentences dynamically, allowing interactive click-to-seek at any speed (0.5x-2.0x). | Pre-baked word timestamp files (fragile to speed changes), static text display (no reading aid). |

---

## 5. Future Roadmap & Milestones

- **Milestone 6 (Q1 2027)**: WebGPU browser-side Kokoro ONNX execution (zero-install web client running speech synthesis directly inside WebAssembly/WebGPU).
- **Milestone 7 (Q2 2027)**: Multi-speaker character dialogue assignment using local SLM (Small Language Model) character tracking.
- **Milestone 8 (Q3 2027)**: Mobile companion reader (PWA / iOS & Android) with local offline sync via WebRTC or local WiFi.
