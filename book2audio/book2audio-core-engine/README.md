# book2audio-core-engine

The shared engine used by both the web app and the Windows app.

- `packages/core`: pure TypeScript that runs anywhere (text cleaning, sentence splitting, narration rules, book readers for Markdown, text, EPUB and PDF chapter logic, the voice-engine interface).
- `packages/engine-node`: the Node side (Kokoro voice, PDF reading, and next the audiobook build with M4B and the local server).
- `tools/gen_fixtures.py`: runs the Python version (`../../book2audio-python`) to create test fixtures, so the TypeScript port can be checked against it.

Later in this folder: `packages/engine-web` (Kokoro in the browser) and `packages/ui` (the shared React screens).
