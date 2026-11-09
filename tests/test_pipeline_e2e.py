"""End-to-end pipeline test: sample markdown book -> chapter MP3s (silent engine)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from b2a.config import apply_overrides, load_config
from b2a.pipeline import Pipeline
from b2a.tts.silent import SilentTTS

ROOT = Path(__file__).resolve().parent.parent


def make_test_book(tmp_path) -> Path:
    p = tmp_path / "test_book.md"
    p.write_text(
        "# The Test Book\n\n"
        "## Chapter One: The Pier\n\n"
        "Mara walked the length of the pier into the night.\n\n"
        "## Chapter Two: The Lantern\n\n"
        "The light breathed like an ancient flame on the water.\n",
        encoding="utf-8"
    )
    return p


def make_pipeline(tmp_path):
    cfg = load_config()
    apply_overrides(cfg, {
        "tts.engine": "silent",
        "llm.provider": "off",
        "tts.speed": 12,
        "tts.pitch": 2,
        "style": "warm",
    })
    return Pipeline(cfg, SilentTTS(cfg, "warm"), tmp_path)


def test_e2e_silent(tmp_path):
    pipeline = make_pipeline(tmp_path)
    book_file = make_test_book(tmp_path)
    manifest = pipeline.convert(book_file)

    assert manifest["chapters"], "no chapters produced"
    assert len(manifest["chapters"]) == 2

    files = [Path(c["file"]) for c in manifest["chapters"]]
    for f in files:
        assert f.exists(), f"missing {f}"
        assert f.stat().st_size > 500, f"empty {f}"

    assert manifest["total_seconds"] > 0
    assert manifest["rate_pct"] == 20  # speed 12 -> +20%

    out_dir = files[0].parent
    m = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert m["book"] == "The Test Book"
    assert len(m["chapters"]) == len(files)


def test_resume_skips_existing(tmp_path):
    pipeline = make_pipeline(tmp_path)
    book_file = make_test_book(tmp_path)
    manifest1 = pipeline.convert(book_file)
    # second run: all chapters exist -> all marked skipped
    manifest2 = pipeline.convert(book_file)
    assert all(c.get("skipped") for c in manifest2["chapters"])


def test_prosody_plan_cached(tmp_path):
    pipeline = make_pipeline(tmp_path)
    book_file = make_test_book(tmp_path)
    pipeline.convert(book_file)
    build_dirs = list((tmp_path / "_build").iterdir())
    assert build_dirs, "no build dir"
    plans = list(build_dirs[0].glob("plan_c*.json"))
    assert plans, "no prosody plan cached"
    plan = json.loads(plans[0].read_text(encoding="utf-8"))
    assert plan and "emphasis" in plan[0]
