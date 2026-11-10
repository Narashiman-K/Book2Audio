"""Book extraction: PDF / EPUB / Markdown / TXT -> Book(chapters, paragraphs).

Design notes
------------
* PDF   : prefer the document TOC (bookmarks). Without a TOC, fall back to
          "Chapter N" heading detection across pages, else one chapter/book.
          Running headers/footers and standalone page numbers are stripped.
* EPUB  : the spine order IS the reading order; each content item becomes a
          chapter (nav/cover/colophon files are skipped).
* MD    : split on H1/H2 headings, strip markdown syntax.
* TXT   : split on common chapter heading patterns.
"""
from __future__ import annotations

import re
import warnings
from pathlib import Path
from typing import Callable, List, Optional, Tuple

try:
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except ImportError:
    pass

from .clean import clean_paragraph_text, load_lexicon, strip_markdown
from .models import Book, Chapter, Paragraph

CHAPTER_HEADING_RE = re.compile(
    r"""^\s*(?:
        chapter\s+([0-9]+|[ivxlcdm]+)\b[\s.:\-–—]*([^\n]{0,80})?
      | (?:part|section|book)\s+([0-9]+|[ivxlcdm]+)\b[\s.:\-–—]*([^\n]{0,80})?
      | ([0-9]{1,2})\s*[\.\)]\s+([^\n]{3,80})
    )\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


def _para_id(chapter_index: int, para_index: int) -> str:
    return f"c{chapter_index:02d}-p{para_index:03d}"


def _make_paragraphs(chapter: Chapter, blocks: List[str], min_chars: int, lexicon: Optional[dict] = None) -> None:
    """Turn raw text blocks into cleaned Paragraph objects (merging tiny ones)."""
    cleaned: List[str] = []
    buffer = ""
    for block in blocks:
        text = clean_paragraph_text(block, lexicon=lexicon)
        if not text:
            continue
        if len(text) < min_chars and not _looks_like_heading(text):
            buffer = f"{buffer} {text}".strip()
            continue
        if buffer:
            cleaned.append(buffer)
            buffer = ""
        cleaned.append(text)
    if buffer:
        cleaned.append(buffer)

    for i, text in enumerate(cleaned):
        chapter.paragraphs.append(Paragraph(id=_para_id(chapter.index, i), text=text))


def _is_skip_chapter_title(title: str) -> bool:
    t = title.strip().lower()
    return t in ("contents", "table of contents", "index", "copyright", "colophon", "title page")


def _looks_like_heading(text: str) -> bool:
    return len(text) < 100 and bool(
        re.match(r"^(chapter|part|section|book|prologue|epilogue|introduction|preface|afterword)\b", text, re.I)
    )


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------

def extract_pdf(path: Path, cfg) -> Book:
    import fitz  # pymupdf

    doc = fitz.open(path)
    meta = doc.metadata or {}
    book = Book(
        title=(meta.get("title") or "").strip() or path.stem.replace("_", " ").title(),
        author=(meta.get("author") or "").strip() or "Unknown author",
        source_path=str(path),
        source_format="pdf",
    )

    pages_text: List[str] = []
    for page in doc:
        pages_text.append(page.get_text("text") or "")

    # --- locate chapter boundaries ------------------------------------
    spans: List[Tuple[str, int, int]] = []  # (title, page_start, page_end_inclusive)

    toc = doc.get_toc(simple=True)  # [[level, title, page], ...]
    top_level = [t for t in toc if t[0] <= 1]
    if len(top_level) >= 2:
        for i, (_lvl, title, page) in enumerate(top_level):
            start = max(page - 1, 0)
            end = (top_level[i + 1][2] - 2) if i + 1 < len(top_level) else doc.page_count - 1
            spans.append((title.strip() or f"Chapter {i + 1}", start, max(end, start)))
    else:
        # scan pages for "Chapter N" style headings
        current_title: Optional[str] = None
        current_start = 0
        for pno, text in enumerate(pages_text):
            m = None
            for line in text.splitlines()[:6]:  # headings live near the top
                if CHAPTER_HEADING_RE.match(line.strip()):
                    m = line.strip()
                    break
            if m:
                if current_title is not None:
                    spans.append((current_title, current_start, pno - 1))
                current_title, current_start = _clean_chapter_title(m), pno
        if current_title is not None:
            spans.append((current_title, current_start, doc.page_count - 1))
        if not spans:
            spans = [(book.title, 0, doc.page_count - 1)]

    # --- running header/footer detection ------------------------------
    repeated = _running_lines(pages_text) if cfg.get_path("extract.strip_running_lines", True) else set()
    lexicon = load_lexicon(cfg.get_path("lexicon.path"))

    ch_idx = 1
    for title, p0, p1 in spans:
        if _is_skip_chapter_title(title):
            continue
        chapter = Chapter(index=ch_idx, title=title)
        blocks: List[str] = []
        for pno in range(p0, p1 + 1):
            blocks.extend(_pdf_page_blocks(pages_text[pno], repeated))
        _make_paragraphs(chapter, blocks, cfg.get_path("extract.min_paragraph_chars", 25), lexicon=lexicon)
        if chapter.paragraphs:
            book.chapters.append(chapter)
            ch_idx += 1

    doc.close()
    if not book.chapters:  # scanned/dead PDF — be explicit rather than silent
        raise ValueError(
            f"No extractable text found in '{path.name}'. It appears to be a scanned PDF "
            f"(images only) without a digital text layer. Please run an OCR tool (e.g. "
            f"ocrmypdf or Adobe Acrobat) on this document first."
        )
    return book


def _pdf_page_blocks(page_text: str, repeated: set) -> List[str]:
    lines = [ln for ln in (l.strip() for l in page_text.splitlines()) if ln]
    # drop running headers/footers + standalone page numbers
    lines = [
        ln for ln in lines
        if ln not in repeated and not re.fullmatch(r"(page\s*)?[0-9ivxlcdm]{1,6}", ln, re.I)
    ]
    # re-join wrapped lines into paragraphs (blank line = paragraph break)
    blocks: List[str] = []
    buf = ""
    for ln in lines:
        if not buf:
            buf = ln
        elif ln.endswith("-"):
            buf = buf + ln[:-1]  # hyphenated wrap: join without hyphen
        else:
            buf += " " + ln
        # crude paragraph end: sentences usually end before a big gap or number
        if len(buf) > 800 and ln.endswith((".", "!", "?", '"', "”")):
            blocks.append(buf)
            buf = ""
    if buf:
        blocks.append(buf)
    return blocks


def _running_lines(pages_text: List[str]) -> set:
    """Lines that appear (normalized) as first/last line on >40% of pages."""
    from collections import Counter

    counts: Counter = Counter()
    total = max(len(pages_text), 1)
    for text in pages_text:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        edges = set()
        if lines:
            edges.add(lines[0])
        if len(lines) > 1:
            edges.add(lines[-1])
        for ln in edges:
            if len(ln) > 2:  # skip trivial
                counts[re.sub(r"\d+", "#", ln)] += 1
    return {ln for ln, n in counts.items() if n / total > 0.4}


def _clean_chapter_title(raw: str) -> str:
    # Strip HTML anchor tags (e.g., <a id="epub-1"></a>)
    t = re.sub(r"<[^>]+>", "", raw)
    t = re.sub(r"\s+", " ", t).strip(" .:-–—")
    return t[:80] or "Untitled"


# --------------------------------------------------------------------------
# EPUB
# --------------------------------------------------------------------------

_SKIP_RE = re.compile(r"(^|[_\-\./])(cover|toc|nav|ncx|titlepage|colophon|copyright|dedication)([_\-\./]|$)", re.I)


def extract_epub(path: Path, cfg) -> Book:
    import ebooklib
    from ebooklib import epub
    from bs4 import BeautifulSoup

    book_doc = epub.read_epub(str(path))
    meta_title = book_doc.get_metadata("DC", "title")
    meta_author = book_doc.get_metadata("DC", "creator")
    book = Book(
        title=meta_title[0][0] if meta_title else path.stem.replace("_", " ").title(),
        author=meta_author[0][0] if meta_author else "Unknown author",
        source_path=str(path),
        source_format="epub",
    )

    chapter_no = 0
    lexicon = load_lexicon(cfg.get_path("lexicon.path"))
    for item in book_doc.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        name = (item.get_name() or "").lower()
        if _SKIP_RE.search(name):
            continue
        soup = BeautifulSoup(item.get_content(), "lxml")
        for tag in soup(["script", "style", "head"]):
            tag.decompose()
        text = soup.get_text("\n")
        blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
        # drop blocks that are pure markup leftovers / very short dupes
        blocks = [b for b in blocks if not re.fullmatch(r"[\s\-–—_=*#|>]+", b)]
        if not blocks:
            continue
        title = _epub_title(soup, chapter_no + 1)
        if _is_skip_chapter_title(title):
            continue
        chapter_no += 1
        chapter = Chapter(index=chapter_no, title=title)
        _make_paragraphs(chapter, blocks, cfg.get_path("extract.min_paragraph_chars", 25), lexicon=lexicon)
        if chapter.paragraphs:
            book.chapters.append(chapter)

    if not book.chapters:
        raise ValueError(f"No readable chapters in {path.name}")
    return book


def _epub_title(soup, fallback_no: int) -> str:
    h = soup.find(["h1", "h2", "h3"])
    if h and h.get_text(strip=True):
        return h.get_text(strip=True)[:80]
    if soup.title and soup.title.get_text(strip=True):
        return soup.title.get_text(strip=True)[:80]
    return f"Chapter {fallback_no}"


# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Markdown / TXT
# --------------------------------------------------------------------------

MD_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
PAGE_LINE_RE = re.compile(r"^(?:page|p\.?)\s*\d+$", re.I)
FIG_LINE_RE = re.compile(r"^(?:picture|fig|figure|table)\s*\d+", re.I)
MAJOR_SECTION_RE = re.compile(
    r"^(?:introduction|editor’s introduction|conclusion|epilogue|prologue|preface|afterword)\b",
    re.I,
)


def extract_markdown(path: Path, cfg) -> Book:
    raw = path.read_text(encoding="utf-8", errors="replace")

    # Strip HTML comments & raw base64 images upfront
    raw = re.sub(r"<!--.*?-->", "", raw, flags=re.S)
    raw = re.sub(r"!\[[^\]]*\]\(data:image\/[^)]+\)", "", raw)

    first_h = _first_heading(raw)
    book = Book(
        title=first_h or path.stem.replace("_", " ").title(),
        author="Unknown author",
        source_path=str(path),
        source_format="md",
    )
    _split_markdown_by_chapters(book, raw, cfg)
    return book


def extract_txt(path: Path, cfg) -> Book:
    raw = path.read_text(encoding="utf-8", errors="replace")
    book = Book(
        title=path.stem.replace("_", " ").title(),
        author="Unknown author",
        source_path=str(path),
        source_format="txt",
    )
    _split_by_headings(book, raw.splitlines(), cfg, md_mode=False)
    return book


def _first_heading(raw: str) -> Optional[str]:
    for line in raw.splitlines():
        m = MD_HEADING_RE.match(line.strip())
        if m:
            clean = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if not PAGE_LINE_RE.match(clean) and not FIG_LINE_RE.match(clean):
                return clean[:80]
    return None


def strip_markdown_heading_lines(raw: str) -> List[str]:
    out: List[str] = []
    for line in raw.splitlines():
        m = MD_HEADING_RE.match(line.strip())
        out.append(m.group(2) if m else line)
    return out


def _merge_adjacent_heading_lines(lines: List[str]) -> List[str]:
    """Merge OCR headings that were split over consecutive lines."""
    merged: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = MD_HEADING_RE.match(line.strip())
        if m:
            lvl = m.group(1)
            txt = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if not PAGE_LINE_RE.match(txt) and not FIG_LINE_RE.match(txt):
                combined_txt = txt
                j = i + 1
                while j < len(lines):
                    if not lines[j].strip():
                        j += 1
                        continue
                    next_m = MD_HEADING_RE.match(lines[j].strip())
                    if next_m:
                        next_txt = re.sub(r"<[^>]+>", "", next_m.group(2)).strip()
                        if not PAGE_LINE_RE.match(next_txt) and not FIG_LINE_RE.match(next_txt):
                            if len(combined_txt) < 45 or combined_txt.endswith(
                                ("THE", "OF", "AND", "IN", "TO", "A", "AN", "SAYING", "NON-VERBAL", "FOR", "WITH")
                            ):
                                combined_txt = f"{combined_txt} {next_txt}"
                                j += 1
                                continue
                    break
                merged.append(f"{lvl} {combined_txt}")
                i = j
                continue
        merged.append(line)
        i += 1
    return merged


def _split_markdown_by_chapters(book: Book, raw: str, cfg) -> None:
    lines = raw.splitlines()
    lines = _merge_adjacent_heading_lines(lines)

    skip_titles = {"contents", "table of contents", "index", "title page", "copyright", "and", "colophon"}

    # Check if explicit 'Chapter N' headings exist in the document
    has_explicit_chapters = False
    for line in lines:
        stripped = line.strip()
        m = MD_HEADING_RE.match(stripped)
        if m:
            txt = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if not PAGE_LINE_RE.match(txt) and CHAPTER_HEADING_RE.match(txt):
                has_explicit_chapters = True
                break

    sections: List[Tuple[str, List[str]]] = []
    current_title = book.title
    current: List[str] = []

    for line in lines:
        stripped = line.strip()
        m = MD_HEADING_RE.match(stripped)
        if m:
            level = len(m.group(1))
            txt = re.sub(r"<[^>]+>", "", m.group(2)).strip(" .:–—#").strip()

            # Ignore page markers and figure labels
            if PAGE_LINE_RE.match(txt) or FIG_LINE_RE.match(txt):
                continue

            is_chapter_break = False
            if has_explicit_chapters:
                if CHAPTER_HEADING_RE.match(txt) or MAJOR_SECTION_RE.match(txt):
                    is_chapter_break = True
            else:
                if level <= 3 and txt.lower() not in skip_titles:
                    # Ignore standalone author names or short noise
                    if len(txt) > 3 and not re.match(r"^(?:by|shân rees|roderick)\b", txt, re.I):
                        is_chapter_break = True

            if is_chapter_break:
                if current:
                    sections.append((current_title, current))
                    current = []
                current_title = txt
                continue

        current.append(line)

    if current:
        sections.append((current_title, current))

    chapter_no = 0
    lexicon = load_lexicon(cfg.get_path("lexicon.path"))
    for title, block_lines in sections:
        if _is_skip_chapter_title(title):
            continue
        block = "\n".join(block_lines)
        paras = [p.strip() for p in re.split(r"\n\s*\n", block) if p.strip()]
        if not paras:
            continue
        chapter_no += 1
        chapter = Chapter(index=chapter_no, title=title)
        _make_paragraphs(chapter, paras, cfg.get_path("extract.min_paragraph_chars", 25), lexicon=lexicon)
        if chapter.paragraphs:
            book.chapters.append(chapter)


def _split_by_headings(book: Book, lines: List[str], cfg, md_mode: bool) -> None:
    sections: List[Tuple[str, List[str]]] = []
    current_title = book.title
    current: List[str] = []

    for line in lines:
        stripped = line.strip()
        if md_mode:
            m = MD_HEADING_RE.match(stripped)
            if m:
                if current:
                    sections.append((current_title, current))
                    current = []
                current_title = m.group(2)
                continue
        else:
            if CHAPTER_HEADING_RE.match(stripped) and len(stripped) < 90:
                if current:
                    sections.append((current_title, current))
                    current = []
                current_title = _clean_chapter_title(stripped)
                continue
        current.append(line)
    if current:
        sections.append((current_title, current))

    chapter_no = 0
    lexicon = load_lexicon(cfg.get_path("lexicon.path"))
    for title, block_lines in sections:
        if _is_skip_chapter_title(title):
            continue
        block = "\n".join(block_lines)
        paras = [p.strip() for p in re.split(r"\n\s*\n", block) if p.strip()]
        if not paras:
            continue
        chapter_no += 1
        chapter = Chapter(index=chapter_no, title=title)
        _make_paragraphs(chapter, paras, cfg.get_path("extract.min_paragraph_chars", 25), lexicon=lexicon)
        if chapter.paragraphs:
            book.chapters.append(chapter)


EXTRACTORS: dict[str, Callable] = {
    ".pdf": extract_pdf,
    ".epub": extract_epub,
    ".md": extract_markdown,
    ".markdown": extract_markdown,
    ".txt": extract_txt,
}


def extract_book(path: str | Path, cfg) -> Book:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    extractor = EXTRACTORS.get(suffix)
    if extractor is None:
        raise ValueError(f"Unsupported format '{suffix}'. Supported: pdf, epub, md, txt")
    return extractor(path, cfg)
