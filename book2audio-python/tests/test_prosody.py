import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from b2a.config import load_config
from b2a.prosody.rules import RulesEngine


def cfg():
    return load_config()


def test_dialogue_detection():
    engine = RulesEngine(cfg(), "warm")
    seg = engine.plan_paragraph('She said, "Dream bigger, then begin."', "c01-p000")
    assert seg.mood == "dialogue"
    assert seg.pitch_delta >= 0


def test_excited_detection():
    engine = RulesEngine(cfg(), "warm")
    seg = engine.plan_paragraph("The light exploded across the water!", "c01-p001")
    assert seg.mood == "excited"
    assert seg.rate_delta > 0


def test_question_detection():
    engine = RulesEngine(cfg(), "warm")
    seg = engine.plan_paragraph("Will you hold me tomorrow too?", "c01-p002")
    assert seg.mood == "question"


def test_power_word_emphasis():
    engine = RulesEngine(cfg(), "warm")
    seg = engine.plan_paragraph(
        "You must never forget the courage it took to begin.", "c01-p003")
    words = set(seg.emphasis)
    assert {"must", "never", "courage"} & words


def test_reflective_slower():
    engine = RulesEngine(cfg(), "warm")
    seg = engine.plan_paragraph(
        "The keeper paused... and the sea answered softly, quietly, at length.", "c01-p004")
    assert seg.mood == "reflective"
    assert seg.rate_delta < 0


def test_caps_word_always_emphasized():
    engine = RulesEngine(cfg(), "warm")
    seg = engine.plan_paragraph("The sign said BEGIN HERE and nothing else.", "c01-p005")
    assert "BEGIN HERE" in seg.emphasis or any("begin" in w.lower() for w in seg.emphasis)


def test_minimal_style_no_emphasis_deltas():
    from b2a.prosody.ssml import build_ssml
    engine = RulesEngine(cfg(), "minimal")
    seg = engine.plan_paragraph("You must begin today!", "c01-p006")
    ssml = build_ssml(seg.emphasis and "You must begin today!" or "x",
                      ["You must begin today!"], seg, 0, 0, "minimal", {})
    assert "<prosody" not in ssml


def test_contrast_and_superlative_emphasis():
    engine = RulesEngine(cfg(), "warm")
    seg = engine.plan_paragraph("This is the best path forward, but we must stay vigilant.", "c01-p007")
    words = set(seg.emphasis)
    assert words & {"best", "but", "must"}


def test_expressive_paragraph_detection():
    from b2a.prosody.llm import is_expressive_paragraph
    # Dialogue
    assert is_expressive_paragraph('"Wait for me!" she cried.', 2, 10)
    # Questions
    assert is_expressive_paragraph("Why did he leave the lantern dark?", 3, 10)
    # Punchy lines (< 70 chars)
    assert is_expressive_paragraph("Silence fell.", 4, 10)
    # Chapter boundary
    assert is_expressive_paragraph("The morning dawned over the rolling hills of grey granite.", 0, 10)
    # Normal exposition (not expressive)
    assert not is_expressive_paragraph(
        "The carriage rolled along the quiet cobblestones of the village street while the villagers were finishing their daily chores before twilight.",
        5, 10
    )


def test_dialogue_colouring_in_ssml():
    from b2a.models import ProsodySegment
    from b2a.prosody.ssml import build_ssml
    seg = ProsodySegment(para_id="c01-p008", mood="neutral", emphasis=[], rate_delta=0, pitch_delta=0)
    s = build_ssml('She whispered, "Sleep well," and left.', ['She whispered, "Sleep well," and left.'],
                   seg, 0, 0, "warm", {})
    assert '<prosody pitch="+3%">' in s

