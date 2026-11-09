"""Tests for Kokoro-82M TTS engine."""
from pathlib import Path
from unittest.mock import MagicMock, patch
import numpy as np
import pytest

from b2a.config import Config
from b2a.tts.base import ChunkJob
from b2a.tts.kokoro_engine import KokoroTTS


@pytest.fixture
def test_cfg():
    return Config({
        "tts": {
            "engine": "kokoro",
            "voice": "af_heart",
            "speed": 1.0,
            "kokoro": {
                "voice": "af_heart",
            }
        }
    })


def test_kokoro_init(test_cfg):
    engine = KokoroTTS(test_cfg)
    assert engine.name == "kokoro"
    assert engine.chosen_voice == "af_heart"


def test_kokoro_list_voices_mocked(test_cfg):
    engine = KokoroTTS(test_cfg)
    mock_inst = MagicMock()
    mock_inst.get_voices.return_value = ["af_heart", "am_michael", "bf_emma"]

    with patch.object(engine, "available", return_value=True), \
         patch.object(engine, "_get_kokoro", return_value=mock_inst):
        voices = engine.list_voices()
        assert len(voices) == 3
        assert voices[0]["name"] == "af_heart"
        assert voices[0]["gender"] == "female"
        assert voices[1]["name"] == "am_michael"
        assert voices[1]["gender"] == "male"


def test_kokoro_synthesize_batch(test_cfg, tmp_path):
    engine = KokoroTTS(test_cfg)
    mock_inst = MagicMock()
    mock_inst.get_voices.return_value = ["af_heart", "am_michael"]
    # Return 1 second of silence at 24kHz
    mock_inst.create.return_value = (np.zeros(24000, dtype=np.float32), 24000)

    with patch.object(engine, "_get_kokoro", return_value=mock_inst):
        jobs = [
            ChunkJob(id="chunk1", text="Hello world.", ssml="<speak>Hello world.</speak>", rate_delta=0),
            ChunkJob(id="chunk2", text="Second line.", ssml="Second line.", rate_delta=10),
        ]
        results = engine.synthesize_batch(jobs, tmp_path)
        assert len(results) == 2
        assert results[0].ok is True
        assert results[0].path.exists()
        assert results[1].ok is True
        assert results[1].path.exists()
