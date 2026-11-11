import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from b2a.clean import apply_lexicon, clean_paragraph_text, is_boilerplate_paragraph, strip_markdown


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


def test_lexicon_replacement():
    lex = {"AI": "A.I.", "Naval": "Nuh-vaal", "Ravikant": "Rah-vee-kaant"}
    out = apply_lexicon("Naval Ravikant talks about AI and ethics.", lex)
    assert "Nuh-vaal" in out and "Rah-vee-kaant" in out and "A.I." in out


def test_boilerplate_detection():
    assert is_boilerplate_paragraph("Copyright 2026. All rights reserved. Printed in USA.")
    assert is_boilerplate_paragraph("ISBN 978-3-16-148410-0")
    assert is_boilerplate_paragraph("Table of Contents")
    assert is_boilerplate_paragraph("Library of Congress Cataloging-in-Publication Data")
    assert not is_boilerplate_paragraph("The journey began when the stars aligned over the sea.")

