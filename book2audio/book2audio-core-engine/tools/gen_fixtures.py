"""Run the Python reference code on sample text and save the results as test fixtures for the TypeScript port.
Usage (from book2audio-core-engine): python tools/gen_fixtures.py ../../book2audio-python"""
import json, sys, pathlib
ref = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "../../book2audio-python").resolve()
sys.path.insert(0, str(ref))
from b2a.clean import clean_paragraph_text, load_lexicon
from b2a.segment import split_sentences
from b2a.prosody.rules import RulesEngine
from b2a.config import load_config

RAW = [
 "## Chapter 1\n",
 "Dr. Smith met Mr. Jones at 3.5 p.m. in St. Paul, e.g., near the park. He said: \"Never give up!\" and left.",
 "The **bold** idea, [linked text](http://x.com), and a footnote[12] with a URL https://example.com/a?b=1 here.",
 "word-\nwrap should join; semicolons become commas — and dashes too.",
 "Page 42",
 "All rights reserved. ISBN 978-3-16-148410-0",
 "Contents",
 "Introduction .......... 15",
 "AI and the API of Naval Ravikant explore dharma, karma and the Bhagavad Gita.",
 "1234 5678 9012 3456 7890",
 "She wondered... was it truly the best way? Nobody knew. The silence grew... and grew.",
 "THE END IS NEAR, but we must never stop believing in the truth of our heart.",
 "This is a long reflective paragraph that goes on and on without any exclamation, " * 4 + "ending quietly.",
 "- list item one\n- list item two",
 "He asked, \"Are you coming?\" She replied, \"Yes, I am coming tonight.\"",
 "Café owners in Zürich: etc. vs. the rest, see Fig. 3 for details.",
]
lex = load_lexicon(ref / "lexicon.yaml")
cfg = load_config(ref / "config.yaml")
out = {"lexicon": lex, "power_words": cfg.get_path("emphasis.power_words", []),
       "max_emph": int(cfg.get_path("emphasis.max_per_sentence", 2)), "clean": [], "sentences": [], "rules": {}}
for r in RAW:
    out["clean"].append({"in": r, "out": clean_paragraph_text(r, lexicon=lex), "out_nolex": clean_paragraph_text(r)})
SENT = [
 "Mr. Brown paid $3.50 for it. He left at 5 p.m. Then he came back... slowly. Really? Yes! Done.",
 "J. R. R. Tolkien wrote it, e.g. in 1954. The end.",
 "A very long sentence " + "with many clauses, but it keeps going; " * 20 + "and finally ends.",
 "No punctuation at all just words " * 30,
 "Quote: \"Stop.\" He said so. (Really.) Okay.",
]
for s in SENT: out["sentences"].append({"in": s, "out": split_sentences(s), "out_60": split_sentences(s, 60)})
for style in ["warm", "motivational", "storyteller", "minimal"]:
    class C: pass
    eng = RulesEngine(cfg, style)
    out["rules"][style] = []
    for r in RAW + [x["in"] for x in out["sentences"]]:
        c = clean_paragraph_text(r)
        if not c: continue
        seg = eng.plan_paragraph(c, "p")
        d = seg.to_dict(); d["text"] = c; out["rules"][style].append(d)
dest = pathlib.Path(__file__).resolve().parent.parent / "packages/core/test/fixtures/reference.json"
dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print("wrote", dest, len(out["clean"]), "clean,", len(out["sentences"]), "sentence cases,", sum(len(v) for v in out["rules"].values()), "rule cases")

# ---- book readers (markdown + txt) ----
from b2a.extract import extract_markdown, extract_txt
import tempfile, os
class Cfg:
    def get_path(self, k, d=None): return {"extract.min_paragraph_chars": 25}.get(k, d)
def summarize(book): return {"title": book.title, "author": book.author,
    "chapters": [{"index": c.index, "title": c.title, "paras": [p.text for p in c.paragraphs]} for c in book.chapters]}
books = {}
md_real = ref.parent / "Books-Library" / "How to Talk to Anyone Communicate with Confidence.md"
if md_real.exists():
    books["md_real"] = {"file": md_real.name, "kind": "md", "raw": md_real.read_text(encoding="utf-8", errors="replace")}
books["md_small"] = {"file": "my_book.md", "kind": "md", "raw": "# My Book\n\nIntro text that is long enough to keep as a paragraph.\n\n## Chapter One\n\nThe river ran slowly past the old mill. Birds sang.\n\nShort.\n\nAnother full paragraph that stays on its own line okay.\n\n## Chapter Two\n\nThe second chapter begins here with some more words.\n\n## Contents\n\nSkip me please, I am a contents block of text.\n"}
books["txt_small"] = {"file": "some_story.txt", "kind": "txt", "raw": "Some Story\n\nOpening words that are long enough to be kept as text.\n\nChapter 1: The Start\n\nIt began on a quiet morning with a long walk along the shore.\n\nChapter 2 - The Middle\n\nThen things got complicated, as they often do in stories.\n\nCHAPTER III\n\nAnd it all ended well, with everyone back home by dinner.\n"}
for k, b in books.items():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / b["file"]; p.write_text(b["raw"], encoding="utf-8")
        book = (extract_markdown if b["kind"] == "md" else extract_txt)(p, Cfg())
        b["expected"] = summarize(book)
(dest.parent / "books.json").write_text(json.dumps(books, ensure_ascii=False), encoding="utf-8")
print("wrote books.json", {k: len(v["expected"]["chapters"]) for k, v in books.items()})
