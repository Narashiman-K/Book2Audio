"""Prosody styles: global character of the narration."""
from __future__ import annotations

from typing import Dict

STYLES: Dict[str, dict] = {
    # style: base rate/pitch deltas + emphasis strength + pause multiplier
    "warm": {
        "desc": "Gentle pitch lifts, soft pauses before big reveals, calm pace",
        "base_rate_delta": 0,
        "base_pitch_delta": 0,
        "emphasis_rate_delta": -12,   # slow down on stressed words
        "emphasis_pitch_delta": 6,    # slight lift on stressed words
        "pause_scale": 1.0,
        "max_rate_delta": 10,
        "max_pitch_delta": 10,
    },
    "motivational": {
        "desc": "Energetic: stresses power words, punchy short pauses",
        "base_rate_delta": 4,
        "base_pitch_delta": 2,
        "emphasis_rate_delta": -15,
        "emphasis_pitch_delta": 10,
        "pause_scale": 0.7,
        "max_rate_delta": 15,
        "max_pitch_delta": 15,
    },
    "storyteller": {
        "desc": "Distinct pitch per dialogue line, dramatic scene pauses",
        "base_rate_delta": -2,
        "base_pitch_delta": 0,
        "emphasis_rate_delta": -14,
        "emphasis_pitch_delta": 8,
        "pause_scale": 1.25,
        "max_rate_delta": 12,
        "max_pitch_delta": 14,
    },
    "minimal": {
        "desc": "Only punctuation pauses, no word stress",
        "base_rate_delta": 0,
        "base_pitch_delta": 0,
        "emphasis_rate_delta": 0,
        "emphasis_pitch_delta": 0,
        "pause_scale": 1.0,
        "max_rate_delta": 0,
        "max_pitch_delta": 0,
    },
}


def get_style(name: str) -> dict:
    return STYLES.get(name, STYLES["warm"])
