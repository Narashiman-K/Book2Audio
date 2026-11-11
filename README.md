# Book2Audio 🎧📖

> **100% Offline, Privacy-First Neural Audiobook Creator & Synchronized Speed-Reading Web Studio.**

Convert your digital books (**PDF, EPUB, Markdown, TXT**) and **Web Articles** into studio-quality chaptered audiobooks (`.m4b` and 320 kbps MP3) with real-time synchronized word and sentence highlighting.

---

## 🌟 Key Features

- **🧠 Neural Voice Synthesis**: Kokoro-82M ONNX (54 expressive voices, StyleTTS 2 architecture, 24 kHz studio quality) running locally on CPU.
- **🎙️ Voice Cloning**: Coqui XTTS v2 local speaker cloning using short reference audio samples.
- **⚡ Native Windows Voices**: Seamless WinRT Natural Speech (Ava, Jenny, Guy) and SAPI desktop integration.
- **📦 Chaptered M4B Containers**: Automatic extraction and embedding of book cover art and `FFMETADATA1` chapter markers into standard MPEG-4 Part 14 audiobooks.
- **🎛️ Broadcast DSP Mastering**: Two-pass EBU R128 loudness normalization (-19 LUFS), -1.5 dBFS true-peak limiters, and 90Hz highpass filtering via FFmpeg.
- **👁️ Speed-Reading Studio**: Interactive browser UI (`http://127.0.0.1:8080`) highlighting text word-by-word with click-to-read seek and live speed adjustments (0.5x – 2.0x).
- **🌐 WebRead & Chrome Extension**: Ingest web articles directly into audiobooks with built-in SSRF security protections.
- **🔒 100% Offline & Private**: Zero cloud tokens, zero external API costs, zero telemetry. All processing stays on your machine.
- **🔄 Dual Python & TypeScript Engines**: Complete Python reference implementation + pure TypeScript core monorepo (`@b2a/core`, `@b2a/engine-node`, Web, Windows).

---

## 📁 Repository Structure

| Folder / File | Description |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Comprehensive technical architecture, file-by-file inventory, technology stack map, and security analysis. |
| [`PLANNING.md`](PLANNING.md) | Phased project roadmap, test-driven parity verification strategy, and architectural decisions (ADRs). |
| [`architecture.html`](architecture.html) | Interactive standalone architecture diagram viewer generated via Archify. |
| [`Books-Library/`](Books-Library/) | Centralized library for all source documents (EPUB, PDF, MD) and generated `.m4b` audiobooks. |
| [`book2audio-python/`](book2audio-python/) | Complete Python reference implementation, studio server, audio pipeline, and pytest suite (56 tests). |
| [`book2audio/`](book2audio/) | TypeScript monorepo (`@b2a/core`, `@b2a/engine-node`, Next.js web app, and Windows desktop wrapper). |
| [`.archify/`](.archify/) | Archify architecture diagram schemas and layout candidate files. |

---

## 🚀 Quick Start

### 1. Launch Book2Audio Studio (Python)
```powershell
cd book2audio-python
.\studio.bat
```
Then open your browser at **`http://127.0.0.1:8080`**.

### 2. Command Line Usage
```powershell
# Check dependencies and hardware
python book2audio-python/b2a/cli.py doctor

# Synthesize an audiobook directly
python book2audio-python/b2a/cli.py build "Books-Library/sample.epub" --voice af_heart --speed 1.15 --pitch 4 --m4b
```

### 3. Run the Test Suite
```powershell
python -m pytest book2audio-python/tests
```

---

## 📄 License

Licensed under the MIT License. See [LICENSE](LICENSE) for details.
