import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from b2a.clean import clean_paragraph_text, strip_markdown


def test_strips_markdown_inline():
    assert strip_markdown("**bold** and *italic* and `code`") == "bold and italic and code"


def test_strips_links_and_images():
    assert strip_markdown("[click here](http://x.com) ![](img.png)") == "click here"


def test_removes_urls_and_footnotes():
    text = "Read the docs https://example.com/x now[12]."
    out = clean_paragraph_text(text)
    assert "http" not in out and "[12]" not in out


def test_hyphen_wrap_join():
    out = clean_paragraph_text("some-\nthing beautiful")
    assert "something" in out


def test_abbreviation_expansion():
    out = clean_paragraph_text("Dr. Smith arrived e.g., early.")
    assert "Doctor Smith" in out and "for example," in out


def test_noise_block_rejected():
    assert clean_paragraph_text("| --- | --- |") == ""
    assert clean_paragraph_text("1234") == ""
    assert clean_paragraph_text("### ---") == ""


def test_normal_text_kept():
    text = "The lantern breathed with a light of its own."
    assert clean_paragraph_text(text) == text
