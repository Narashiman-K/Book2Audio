#!/usr/bin/env python3
"""
book2audio — turn your own books (PDF/EPUB/MD/TXT) into expressive audiobooks.

    python book2audio.py convert "My Book.epub" --voice Ava --speed 12
    python book2audio.py list-voices
    python book2audio.py doctor
    python book2audio.py demo

Everything runs locally: Windows 11 natural voices (WinRT), Ollama for the
prosody brain, ffmpeg for audio assembly. No cloud, no API keys.
Personal & family use only — do not distribute the generated audio.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from b2a.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
