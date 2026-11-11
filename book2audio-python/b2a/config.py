"""Configuration loader (config.yaml + CLI overrides)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


class Config(dict):
    """Dict with attribute access + dotted get()."""

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError as e:  # pragma: no cover
            raise AttributeError(item) from e

    def get_path(self, dotted: str, default: Any = None) -> Any:
        node: Any = self
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set_path(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node: dict = self
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value


def load_config(path: str | os.PathLike | None = None) -> Config:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    else:  # pragma: no cover
        data = {}
    return Config(data)


def apply_overrides(cfg: Config, overrides: Dict[str, Any]) -> Config:
    """Apply dotted-key CLI overrides, e.g. {"tts.speed": 14}."""
    for key, value in overrides.items():
        if value is not None:
            cfg.set_path(key, value)
    return cfg
