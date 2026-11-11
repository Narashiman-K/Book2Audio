"""Voice-cloning tests: XTTS engine logic with a stubbed TTS package,
plus a pipeline run that verifies rate_delta passthrough for no-SSML engines.
"""
import contextlib
import sys
import types
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from b2a.config import apply_overrides, load_config
from b2a.tts.base import ChunkJob, ChunkResult
from b2a.tts.xtts_local import XttsTTS

ROOT = Path(__file__).resolve().parent.parent


# ---------- helpers ----------

def make_cfg(clone_voice: str = "", speed: float = 12):
    cfg = load_config()
    apply_overrides(cfg, {
        "tts.xtts.clone_voice": clone_voice,
        "tts.speed": speed,
        "llm.provider": "off",
    })
    return cfg


def install_stub_tts(monkeypatch, calls: list):
    """Fake coqui-tts package so the engine's import + API surface is exercised."""
    class FakeTTS:
        def __init__(self, model_name):
            calls.append(("init", model_name))

        def tts_to_file(self, **kw):
            calls.append(("synth", kw))
            Path(kw["file_path"]).write_bytes(
                b"RIFF" + b"\x00" * 100)  # fake audio payload

    api_mod = types.ModuleType("TTS.api")
    api_mod.TTS = FakeTTS
    pkg = types.ModuleType("TTS")
    pkg.api = api_mod
    monkeypatch.setitem(sys.modules, "TTS", pkg)
    monkeypatch.setitem(sys.modules, "TTS.api", api_mod)


def write_silence_wav(path: Path, seconds: float = 1.0, rate: int = 16000):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * int(rate * seconds))


# ---------- speed mapping ----------

def test_speed_mapping_global():
    eng = XttsTTS(make_cfg(speed=12))          # speed 12 -> +20% -> 1.2
    assert eng._speed_for(0) == pytest.approx(1.2)


def test_speed_mapping_paragraph_delta():
    eng = XttsTTS(make_cfg(speed=12))
    assert eng._speed_for(10) == pytest.approx(1.30)   # +20% base +10pp delta
    assert eng._speed_for(-15) == pytest.approx(1.05)  # +20% base -15pp delta


def test_speed_mapping_clamps():
    eng = XttsTTS(make_cfg(speed=20))          # +100% base
    assert eng._speed_for(15) == pytest.approx(2.0)    # clamped high
    eng0 = XttsTTS(make_cfg(speed=0))                  # -100% base
    assert eng0._speed_for(-15) == pytest.approx(0.5)  # clamped low


# ---------- voice identity ----------

def test_chosen_voice_builtin():
    eng = XttsTTS(make_cfg())
    assert eng.chosen_voice == "Claribel Dervla"
    voices = eng.list_voices()
    assert voices[0]["engine"] == "xtts" and "built-in" in voices[0]["name"]


def test_chosen_voice_cloned(tmp_path):
    sample = tmp_path / "my_voice.wav"
    write_silence_wav(sample, 10.0)
    eng = XttsTTS(make_cfg(clone_voice=str(sample)))
    assert eng.chosen_voice == "my_voice (cloned)"
    assert eng.list_voices()[0]["id"] == str(sample)


# ---------- availability & sample checks ----------

def test_available_false_without_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "TTS", None)  # import TTS -> ImportError
    assert XttsTTS(make_cfg()).available() is False


def test_available_true_with_stub(monkeypatch):
    install_stub_tts(monkeypatch, calls=[])
    assert XttsTTS(make_cfg()).available() is True


def test_sample_duration_seconds(tmp_path):
    wav = tmp_path / "s.wav"
    write_silence_wav(wav, 1.0, rate=8000)
    assert XttsTTS.sample_duration_seconds(wav) == pytest.approx(1.0, abs=0.01)
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"not a wav at all")
    assert XttsTTS.sample_duration_seconds(junk) == 0.0


def test_missing_clone_sample_raises(tmp_path, monkeypatch):
    install_stub_tts(monkeypatch, calls=[])
    eng = XttsTTS(make_cfg(clone_voice=str(tmp_path / "nope.wav")))
    with pytest.raises(FileNotFoundError):
        eng.synthesize_batch([ChunkJob(id="x", text="hi", ssml="hi")], tmp_path / "o")


def test_short_sample_warns_but_proceeds(tmp_path, monkeypatch, caplog):
    install_stub_tts(monkeypatch, calls=[])
    sample = tmp_path / "short.wav"
    write_silence_wav(sample, 2.0)  # < 6 s -> warning
    eng = XttsTTS(make_cfg(clone_voice=str(sample)))
    with caplog.at_level("WARNING", logger="b2a.tts.xtts"):
        res = eng.synthesize_batch(
            [ChunkJob(id="x", text="hi there", ssml="hi there")], tmp_path / "o")
    assert res[0].ok
    assert any("only 2" in r.getMessage() for r in caplog.records)


# ---------- synthesis against the stub API ----------

def test_synthesize_batch_clone_calls(tmp_path, monkeypatch):
    calls: list = []
    install_stub_tts(monkeypatch, calls)
    sample = tmp_path / "me.wav"
    write_silence_wav(sample, 12.0)

    eng = XttsTTS(make_cfg(clone_voice=str(sample), speed=12))
    out = tmp_path / "chunks"
    jobs = [ChunkJob(id="a1", text="One.", ssml="One.", rate_delta=0),
            ChunkJob(id="b2", text="Two!", ssml="Two!", rate_delta=10)]
    results = eng.synthesize_batch(jobs, out)

    assert all(r.ok for r in results)
    assert (out / "a1.wav").exists() and (out / "b2.wav").exists()

    inits = [c for c in calls if c[0] == "init"]
    assert inits and "xtts_v2" in inits[0][1]

    synths = [c[1] for c in calls if c[0] == "synth"]
    assert len(synths) == 2
    assert synths[0]["speaker_wav"] == str(sample)   # cloned voice used
    assert synths[0]["speed"] == pytest.approx(1.2)  # global +20%
    assert synths[1]["speed"] == pytest.approx(1.30) # paragraph delta applied
    assert synths[0]["language"] == "en"


def test_synthesize_batch_builtin_speaker(tmp_path, monkeypatch):
    calls: list = []
    install_stub_tts(monkeypatch, calls)
    eng = XttsTTS(make_cfg(clone_voice=""))
    results = eng.synthesize_batch(
        [ChunkJob(id="z", text="Hello.", ssml="Hello.")], tmp_path / "o")
    assert results[0].ok
    synths = [c[1] for c in calls if c[0] == "synth"]
    assert "speaker" in synths[0] and "speaker_wav" not in synths[0]


# ---------- pipeline integration ----------

class _CapturingXtts(XttsTTS):
    """Records jobs without needing the real coqui-tts package."""

    def __init__(self, cfg, style_name="warm"):
        super().__init__(cfg, style_name)
        self.captured: list = []

    def available(self) -> bool:
        return True

    def synthesize_batch(self, jobs, out_dir):
        import wave as _wave
        out_dir.mkdir(parents=True, exist_ok=True)
        for job in jobs:
            self.captured.append(job)
            p = out_dir / f"{job.id}.wav"
            # ~320 ms per word, like the silent engine — very short clips can
            # trip a libmp3lame assertion during loudnorm
            ms = min(max(1, len(job.text.split())) * 320, 120_000)
            with _wave.open(str(p), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(22050)
                wf.writeframes(b"\x00\x00" * int(22050 * ms / 1000))
        return [ChunkResult(id=j.id, ok=True,
                            path=out_dir / f"{j.id}.wav",
                            bytes=(out_dir / f"{j.id}.wav").stat().st_size)
                for j in jobs]


def test_pipeline_routes_xtts_and_passes_rate_delta(tmp_path):
    from b2a.pipeline import Pipeline

    test_doc = tmp_path / "xtts_test.md"
    test_doc.write_text("# Chapter 1\n\nHope is a lantern carried across the water.\n", encoding="utf-8")

    cfg = make_cfg()
    engine = _CapturingXtts(cfg)
    pipeline = Pipeline(cfg, engine, tmp_path)
    manifest = pipeline.convert(test_doc)

    assert engine.captured, "engine received no jobs"
    for job in engine.captured:
        assert isinstance(job.rate_delta, int)
        assert -15 <= job.rate_delta <= 15
        assert not job.ssml.lstrip().startswith("<speak")  # plain text routing

    assert manifest["engine"] == "xtts"
    assert "chapters" in manifest and manifest["chapters"]
