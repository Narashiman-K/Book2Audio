"""TTS backends package."""
from __future__ import annotations

from typing import Optional

from ..config import Config


def create_engine(cfg: Config, style_name: str = "warm", prefer: Optional[str] = None):
    """Factory: picks the best available engine. Never raises — worst case is
    the silent engine so the pipeline stays testable everywhere."""
    from .silent import SilentTTS

    engine_name = (prefer or cfg.get_path("tts.engine", "auto") or "auto").lower()

    def _try(module_path: str, class_name: str):
        try:
            mod = __import__(module_path, fromlist=[class_name])
            engine = getattr(mod, class_name)(cfg, style_name)
            return engine if engine.available() else None
        except Exception as e:  # pragma: no cover
            import logging
            logging.getLogger("b2a.tts").debug("engine %s unavailable: %s", class_name, e)
            return None

    order = {
        "kokoro": [".kokoro_engine.KokoroTTS", ".windows.WindowsWinrtTTS", ".silent.SilentTTS"],
        "winrt":  [".windows.WindowsWinrtTTS", ".piper_local.PiperTTS", ".silent.SilentTTS"],
        "sapi":   [".sapi.SapiTTS", ".piper_local.PiperTTS", ".silent.SilentTTS"],
        "piper":  [".piper_local.PiperTTS", ".silent.SilentTTS"],
        "xtts":   [".xtts_local.XttsTTS", ".piper_local.PiperTTS", ".silent.SilentTTS"],
        "silent": [".silent.SilentTTS"],
        # Kokoro is now the primary high-fidelity neural engine in auto when installed
        "auto":   [".kokoro_engine.KokoroTTS", ".windows.WindowsWinrtTTS", ".sapi.SapiTTS",
                   ".piper_local.PiperTTS", ".silent.SilentTTS"],
    }
    chain = order.get(engine_name, order["auto"])
    for spec in chain:
        module_path, class_name = spec.rsplit(".", 1)
        engine = _try("b2a.tts" + module_path, class_name)
        if engine:
            return engine

    return SilentTTS(cfg, style_name)  # absolute last resort
