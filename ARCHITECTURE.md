# Book2Audio Complete Architecture & Technical Reference

## 1. System Overview

**Book2Audio** is an offline, privacy-first Neural Audiobook Creator and Synchronized Speed-Reading Web Studio. It ingests digital literature (PDF, EPUB, Markdown, Plain Text, and Web Articles), extracts and cleans the text structure, computes expressive narration prosody, synthesizes speech through local neural speech engines, masters the audio with broadcast DSP filters, packages the result into a chaptered `.m4b` container with embedded artwork, and presents a responsive speed-reading UI that highlights words and sentences in real-time as the audio plays.

---

## 2. Comprehensive Directory & File Structure

```
my_audio_book_creator/
├── .archify/                                  # Interactive architecture diagram configurations
│   └── architecture-audiobook-creator-.../
│       └── candidate.json                     # Canonical diagram layout and connectivity schema
├── .gitignore                                 # Git ignore rules for models, audio, and caches
├── ARCHITECTURE.md                            # Complete technical architecture specification (this file)
├── PLANNING.md                                # Project planning, evolution phases, and roadmap
├── architecture.html                          # Rendered self-contained interactive architecture viewer
│
├── Books-Library/                             # Centralized repository for all source and mastered books
│   ├── *.epub                                 # Source EPUB digital books
│   ├── *.pdf                                  # Source PDF documents
│   ├── *.md                                   # Source Markdown manuscripts
│   └── *.m4b                                  # Auto-synthesized, mastered chaptered audiobooks
│
├── book2audio-python/                         # Complete Python reference implementation
│   ├── b2a/                                   # Core Python library package
│   │   ├── prosody/                           # Narration prosody and emotion intelligence
│   │   │   ├── __init__.py                    # Prosody package initialization
│   │   │   ├── llm.py                         # Local LLM connector (Ollama Qwen2.5/Llama 3) for chapter tone arcs
│   │   │   ├── rules.py                       # Deterministic linguistic prosody engine (dialogue, cadence, speed)
│   │   │   ├── ssml.py                        # W3C SSML, SAPI XML, and plain text markup generator
│   │   │   └── styles.py                      # Preset voice styling definitions (calibrated speed, pitch, pauses)
│   │   ├── tts/                               # Multi-backend speech synthesis engine tier
│   │   │   ├── __init__.py                    # TTS package initialization and factory dispatch
│   │   │   ├── base.py                        # Abstract base class for all TTS engines (`BaseTTS`)
│   │   │   ├── kokoro_engine.py               # Kokoro-82M ONNX neural TTS engine (54 voices, 24 kHz)
│   │   │   ├── piper_local.py                 # Piper ONNX neural voice engine for fast CPU synthesis
│   │   │   ├── sapi.ps1                       # PowerShell script wrapper for Windows SAPI desktop voices
│   │   │   ├── sapi.py                        # Python driver for Windows SAPI integration
│   │   │   ├── silent.py                      # Mock silent synthesizer for fast unit test execution
│   │   │   ├── windows.py                     # Windows WinRT Natural Voice driver (Ava, Jenny, Guy)
│   │   │   ├── winrt.ps1                      # PowerShell script executing WinRT asynchronous speech synthesis
│   │   │   └── xtts_local.py                  # Coqui XTTS v2 speaker voice cloning engine
│   │   ├── web/                               # Web Studio frontend static assets
│   │   │   ├── assets/                        # UI brand logos, icons, and animated loaders
│   │   │   └── index.html                     # Responsive single-page application for narration & speed reading
│   │   ├── __init__.py                        # b2a package exports and versioning
│   │   ├── audio.py                           # FFmpeg DSP mastering, EBU R128 loudness, 320k CBR, M4B packaging
│   │   ├── clean.py                           # Text normalization, header/footer stripping, boilerplate filter
│   │   ├── cli.py                             # Command-line interface driver (`b2a build`, `audition`, `doctor`)
│   │   ├── config.py                          # YAML configuration loader with safe schema defaults
│   │   ├── cover.py                           # Cover art extractor from EPUB metadata and PDF rasterization
│   │   ├── extract.py                         # Document parsers for PDF (PyMuPDF), EPUB (ebooklib), and Markdown
│   │   ├── models.py                          # Strongly-typed dataclasses (Book, Chapter, Sentence, ProsodyPlan)
│   │   ├── pipeline.py                        # High-level pipeline coordinator orchestrating end-to-end builds
│   │   ├── segment.py                         # Sentence boundary disambiguation with abbreviation protection
│   │   ├── studio.py                          # Local HTTP/REST server serving UI, Range requests, and build APIs
│   │   └── webread.py                         # Security-hardened web article reader API with SSRF protection
│   │
│   ├── extension/                             # Chrome Manifest V3 Browser Extension
│   │   ├── icons/                             # Extension icons (32x32, 128x128)
│   │   ├── background.js                      # Background service worker coordinating API calls
│   │   ├── content.js                         # In-page content script extracting article DOM nodes
│   │   ├── manifest.json                      # Chrome extension manifest defining permissions and endpoints
│   │   ├── options.html & options.js          # User settings UI for API endpoints and default voices
│   │   ├── popup.html & popup.js              # One-click "Read Article" popup UI
│   │   ├── pair.js                            # Localhost authentication and pairing handshake
│   │   ├── ui.css                             # Extension stylesheet
│   │   └── README.md                          # Extension installation and usage instructions
│   │
│   ├── samples/                               # Sample audition and benchmark audio files
│   ├── tests/                                 # Unit, integration, and end-to-end pytest test suite
│   │   ├── test_clean.py                      # Tests for text cleaning and artifact stripping
│   │   ├── test_kokoro.py                     # Tests for Kokoro ONNX neural speech generation
│   │   ├── test_pipeline_e2e.py               # End-to-end tests for chapter and book synthesis
│   │   ├── test_prosody.py                    # Tests for rule-based and LLM prosody planning
│   │   ├── test_segment.py                    # Tests for sentence segmentation edge cases
│   │   ├── test_ssml.py                       # Tests for SSML and XML tag formation
│   │   └── test_xtts.py                       # Tests for XTTS cloning parameter formatting
│   │
│   ├── b2a.bat                                # Windows command launcher targeting Python 3.14
│   ├── book2audio.py                          # CLI entry point wrapper
│   ├── config.yaml                            # Default configuration settings (voice, speed, sample rate)
│   ├── install.bat                            # Windows automated setup script
│   ├── lexicon.yaml                           # Pronunciation dictionary mapping difficult terms to phonemes
│   ├── requirements.txt                       # Production Python dependencies
│   ├── requirements-dev.txt                   # Development and test dependencies
│   └── studio.bat                             # One-click batch file to launch Book2Audio Studio on port 8080
│
└── book2audio/                                # Modern TypeScript Parity Monorepo
    ├── book2audio-core-engine/                # TypeScript core extraction and synthesis libraries
    │   ├── packages/
    │   │   ├── core/                          # Zero-dependency TypeScript core logic
    │   │   │   ├── src/
    │   │   │   │   ├── clean.ts               # TS port of text normalization and artifact cleaning
    │   │   │   │   ├── epub.ts                # Pure TS EPUB archive extractor
    │   │   │   │   ├── extractor.ts           # Unified document extractor interface
    │   │   │   │   ├── models.ts              # Strongly-typed TypeScript interfaces
    │   │   │   │   ├── rules.ts               # TS port of prosody rules and dialogue detection
    │   │   │   │   ├── segment.ts             # TS port of quote-aware sentence segmenter
    │   │   │   │   ├── styles.ts              # TS voice configuration presets
    │   │   │   │   └── tts.ts                 # Abstract TTS driver interface
    │   │   │   ├── test/                      # Vitest parity test suite
    │   │   │   │   └── parity.test.ts         # Golden fixture comparison against Python reference
    │   │   │   └── package.json               # @b2a/core package descriptor
    │   │   └── engine-node/                   # Node.js runtime bindings
    │   │       ├── src/
    │   │       │   ├── kokoro-node.ts         # Node.js ONNX Runtime bindings for Kokoro-82M
    │   │       │   ├── pdf-node.ts            # PDF parsing using pdf-parse / pdf-lib
    │   │       │   └── wav-assembler.ts       # Raw WAV concatenation and PCM audio manipulation
    │   │       └── package.json               # @b2a/engine-node package descriptor
    │   └── tools/
    │       └── gen_fixtures.py                # Automated parity test harness dumping golden JSON fixtures
    │
    ├── book2audio-web/                        # Modern Next.js / React web application
    │   ├── package.json                       # Next.js web dependencies
    │   └── README.md                          # Web client deployment instructions
    │
    ├── book2audio-windows/                    # Standalone Windows desktop application wrapper
    │   ├── package.json                       # Electron / desktop wrapper dependencies
    │   └── README.md                          # Desktop application build guide
    │
    ├── package.json                           # Root monorepo workspace configuration
    ├── tsconfig.base.json                     # Shared TypeScript compiler options
    └── tsconfig.json                          # Root TypeScript configuration
```

---

## 3. Technology, Frameworks & Resources Map

| Technology / Tool | Layer / Category | Why Selected | How It Helps the Project |
|---|---|---|---|
| **Python 3.10 – 3.14** | Runtime Engine | Standard in scientific computing, audio DSP, and machine learning. Broad ecosystem for text manipulation. | Powers the reference implementation, PyMuPDF, ONNX Runtime, and audio mastering pipeline. |
| **TypeScript 5+ & Node.js 20+** | Monorepo Runtime | Static type safety, modern modularity, shared types across frontend and backend, instant client distribution. | Implements `@b2a/core` and `@b2a/engine-node` for desktop and web applications without requiring Python on client machines. |
| **PyMuPDF (`fitz`)** | Document Ingestion | High-performance C-backed PDF parsing library. Accurately extracts text blocks, bounding boxes, font weights, and table of contents. | Reconstructs real document hierarchy and chapters from unstructured PDFs while identifying headings and skipping page headers. |
| **ebooklib & BeautifulSoup4** | Document Ingestion | Comprehensive EPUB parser supporting EPUB 2 and EPUB 3 standards; BS4 parses internal XHTML content files. | Traverses the EPUB spine sequentially, strips HTML/CSS boilerplate, preserves chapter divisions, and extracts embedded cover images. |
| **Kokoro-82M ONNX** | Neural Speech Synthesis | 82-million parameter StyleTTS 2 model running in ONNX Runtime. Provides 54 human-like voices (American & British). | Delivers studio-quality natural narration on ordinary consumer CPUs without requiring expensive NVIDIA GPUs or cloud tokens. |
| **Windows WinRT & SAPI** | Native Speech Synthesis | Built into Windows 10/11 operating systems. Offers ultra-fast synthesis using natural voices (Ava, Jenny, Guy, David). | Provides a zero-download fallback that works instantly on any Windows PC with zero configuration or disk overhead. |
| **Coqui XTTS v2** | Voice Cloning | State-of-the-art multilingual voice cloning model capable of cloning a speaker from a 5–10 second clean reference audio clip. | Enables users to generate personal audiobooks in their own voice or chosen narrator voice completely offline. |
| **Piper TTS** | Fast Neural Speech | Lightweight ONNX-based neural synthesizer optimized for embedded and real-time synthesis. | Delivers 5x–10x real-time generation speed on low-power devices. |
| **FFmpeg (`libavcodec`, `libavformat`)** | Audio DSP & Container Assembly | Industry standard multimedia processing suite. Supports complex filtergraphs, EBU R128 loudness measurement, and AAC encoding. | Normalizes volume to -19 LUFS broadcast standards, eliminates clipping via -1.5 dBFS true-peak limiters, cuts sub-bass rumble with 90Hz highpass, and muxes M4B containers. |
| **FFMETADATA1 & APIC** | Metadata & Tagging | Universal metadata specification for QuickTime/MP4 containers. | Embeds chapter title markers, timing boundaries, author/title tags, and high-resolution cover artwork directly into `.m4b` audiobooks. |
| **Native Web Audio API** | Frontend Player | Standard browser audio subsystem supporting precise sample-accurate timing and dynamic playback rate manipulation. | Enables smooth speed adjustment from 0.5x to 2.0x in real-time without pitch distortion or audio stutter. |
| **HTML5 HTTP 206 Partial Content** | Audio Streaming | HTTP range requests supported by `b2a/studio.py`. | Allows instant audio seeking to any second of a 10-hour audiobook without having to download or buffer the entire file first. |
| **Web Highlights API / DOM Range** | Speed Reading | Native browser text selection and highlighting interfaces. | Highlights sentences and individual words synchronously with narration playback for active comprehension and speed reading. |
| **Ollama Local LLM** | Prosody Intelligence | Runs local open-weights language models (Qwen2.5, Llama 3) via local REST API. | Infers emotional mood arcs (suspense, excitement, contemplation) across long narrative passages to dynamically guide TTS inflection. |
| **Chrome Manifest V3** | Web Ingestion Extension | Google Chrome extension standard providing safe background workers and DOM extraction. | Turns any web article, news post, or documentation page into an audiobook with one click. |
| **Readability Algorithm** | Text Extraction | Mozilla Readability DOM heuristic algorithm. | Strips advertisements, navigation bars, cookie banners, and comments from web pages, isolating clean article body text. |
| **Archify** | Architecture Visualization | Formal architecture diagram generator and layout contract validator. | Generates production-grade standalone interactive HTML diagrams (`architecture.html`) with theme toggling and layout verification. |

---

## 4. End-to-End System Workflow

```
[ Source Documents ]               [ Web Article ]
(PDF, EPUB, MD, TXT)             (Chrome Extension)
         │                                │
         ▼                                ▼
[ Ingestion & Cover Extractor ]    [ WebRead SSRF-Protected API ]
(PyMuPDF / ebooklib / bs4)         (Readability Parser)
         │                                │
         └────────────────┬───────────────┘
                          ▼
            [ Text Normalization & Cleaning ]
            (Headers, Footers, Page artifacts removed)
                          │
                          ▼
            [ Quote-Aware Sentence Segmenter ]
            (Protected Abbreviations, Dialogue isolation)
                          │
                          ▼
            [ Narration Prosody Intelligence ]
            ┌─────────────────────┴─────────────────────┐
            ▼                                           ▼
  [ Deterministic Rule Engine ]             [ Ollama LLM Mood Arc ]
  (1.15x pace, +4st pitch, quotes)         (Suspense, humor, calm)
            └─────────────────────┬─────────────────────┘
                                  ▼
                    [ SSML / SAPI / Plain Markup ]
                                  │
                                  ▼
                 [ Speech Synthesis Tier (Offline) ]
     ┌──────────────┬──────────────┬──────────────┬──────────────┐
     ▼              ▼              ▼              ▼              ▼
[ Kokoro-82M ] [ WinRT Natural ] [ Coqui XTTS ] [ Piper ONNX ] [ SAPI Desktop ]
(54 voices)    (Ava/Jenny/Guy)   (Voice Clone)  (Fast Local)   (David/Zira)
     └──────────────┴──────────────┴──────────────┴──────────────┘
                                  │
                                  ▼
               [ Studio DSP Audio Pipeline (FFmpeg) ]
               - 90Hz High-pass filter (sub-bass cleanup)
               - Two-pass EBU R128 loudness norm (-19 LUFS)
               - Lossless WAV stitching & 320 kbps CBR master
                                  │
                                  ▼
              [ M4B Container & Cover Assembly Engine ]
              - Embedded chapter markers (FFMETADATA1)
              - Embedded cover artwork (APIC)
              - AAC audio stream encapsulation
                                  │
                                  ▼
                     [ Books-Library Central Store ]
                     (Single source of truth)
                                  │
                                  ▼
            [ Interactive Web Studio & Speed Reader UI ]
            - Continuous full-book playback
            - Sub-second word & sentence highlighting sync
            - Interactive click-to-read instant seek
            - Live speed adjustment (0.5x – 2.0x)
```

---

## 5. Security, Isolation & Privacy Architecture

### 100% Offline & Zero Telemetry
- All speech generation, document parsing, audio processing, and UI interactions execute strictly on `127.0.0.1` (localhost).
- No external network connections are made during synthesis or reading.
- No user text, book content, voice recordings, or transcripts are transmitted to third-party cloud services or telemetry endpoints.
- Completely immune to API rate limits, provider outages, or subscription price increases.

### Server-Side Request Forgery (SSRF) Defense (`b2a/webread.py`)
When ingesting web content via the `/api/webread` endpoint, Book2Audio enforces strict SSRF protections:
- **Private IP Blacklisting**: Prohibits requests targeting RFC-1918 private address ranges:
  - `10.0.0.0/8`
  - `172.16.0.0/12`
  - `192.168.0.0/16`
- **Loopback Blocking**: Rejects connections to `127.0.0.0/8`, `localhost`, `0.0.0.0`, and `::1`.
- **Link-Local & Cloud Metadata Blocking**: Prohibits requests targeting `169.254.169.254` (AWS, Google Cloud, and Azure instance metadata services) and link-local ranges (`169.254.0.0/16`).
- **Scheme Enforcement**: Restricts allowed URL protocols strictly to `http` and `https`.
- **DNS Rebinding Protection**: Resolves domain hostnames prior to connection and verifies the resulting IP address against the forbidden IP ranges.

### Command Injection Prevention
- All external tool calls (including `ffmpeg`, `ffprobe`, PowerShell scripts, and Piper binaries) are executed using explicit argument vectors (lists of strings) through `subprocess.run(..., shell=False)`.
- No user-supplied text or filenames are passed to a command shell, preventing shell metacharacter injection (`&`, `|`, `;`, `$`, `` ` ``).

### Path Traversal Mitigation
- File uploads and book access are strictly bounded to the designated `Books-Library/` and output directories.
- All file paths are normalized using `os.path.abspath` and validated using `os.path.commonpath` to ensure requests cannot escape the allowed workspace roots using `../` traversal tricks.

### Safe Dependency Management
- The TypeScript core engine (`@b2a/core`) is designed with **zero runtime dependencies**, eliminating supply chain vulnerability attack vectors in core parsing and segmentation logic.
- Python dependencies are constrained to well-established, audited libraries (`PyMuPDF`, `ebooklib`, `onnxruntime`, `pydub`, `pyyaml`).
