import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from b2a.config import Config
from b2a.models import ProsodySegment
from b2a.prosody.ssml import build_plain_text, build_sapi_xml, build_ssml, compute_global_prosody

CFG = Config({
    "tts": {
        "rate_percent_per_speed_unit": 10,
        "pitch_percent_per_pitch_unit": 6,
    }
})


def seg(**kw) -> ProsodySegment:
    d = dict(para_id="c01-p000", emphasis=[], rate_delta=0, pitch_delta=0,
             pause_after_ms=400, mood="neutral")
    d.update(kw)
    return ProsodySegment(**d)


def test_global_mapping_speed12_pitch2():
    g = compute_global_prosody(12, 2, CFG)
    assert g["rate_pct"] == 20          # (12-10)*10
    assert g["pitch_pct"] == 12         # 2*6


def test_ssml_wraps_prosody_and_escapes():
    s = build_ssml('He said "go" & ran.', ['He said "go" & ran.'], seg(), 20, 0, "warm", {})
    assert s.startswith('<speak version="1.0"')
    assert 'rate="+20%"' in s
    assert "&amp;" in s
    assert "</speak>" in s


def test_emphasis_word_wrapping():
    sentence = "You must begin today."
    s = build_ssml(sentence, [sentence], seg(emphasis=["begin"]), 0, 0, "warm", {})
    assert '<prosody rate="-12%" pitch="+6%">begin</prosody>' in s


def test_emphasis_is_case_insensitive_and_capped():
    sentence = "Dream and DREAM and dream again and again."
    s = build_ssml(sentence, [sentence], seg(emphasis=["dream"]), 0, 0, "warm", {})
    assert s.count("<prosody") == 2  # hard cap of 2 per sentence


def test_sentence_breaks_inserted():
    sents = ["First sentence.", "Second sentence."]
    s = build_ssml(" ".join(sents), sents, seg(), 0, 0, "warm",
                   {"sentence_ms": 380, "ellipsis_ms": 800, "comma_ms": 150})
    assert '<break time="380ms"/>' in s


def test_ellipsis_break():
    text = "He paused... then moved."
    s = build_ssml(text, [text], seg(), 0, 0, "warm",
                   {"sentence_ms": 380, "ellipsis_ms": 700, "comma_ms": 150})
    assert '<break time="700ms"/>' in s


def test_sapi_xml_uses_scales():
    s = build_sapi_xml("Hello there.", ["Hello there."], seg(), 20, 12, "warm", {})
    assert 'rate speed="2"' in s       # 20%/10
    assert 'pitch middle="2"' in s     # 12%/6


def test_plain_text_for_piper():
    s = build_plain_text("Hello *world*.", ["Hello world."], seg(emphasis=["world"]))
    assert "world" in s and "prosody" not in s


def test_rate_clamped():
    g = compute_global_prosody(30, 0, CFG)
    assert g["rate_pct"] <= 80


@pytest.mark.parametrize("speed,pitch,exp_r,exp_p", [
    (10, 0, 0, 0), (12, 2, 20, 12), (8, -3, -20, -18), (20, 10, 80, 50),
])
def test_mapping_table(speed, pitch, exp_r, exp_p):
    g = compute_global_prosody(speed, pitch, CFG)
    assert g["rate_pct"] == exp_r
    assert g["pitch_pct"] == exp_p
