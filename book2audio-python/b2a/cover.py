"""Find or extract a cover image for a book (EPUB cover, PDF first page, or a
cover.jpg/png next to the source file). Never falls back to an unrelated image."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

log = logging.getLogger("b2a.cover")

_SIDE_NAMES = ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg")


def find_cover(source: Path, out_dir: Path) -> Optional[Path]:
    """Return a cover image path for `source`, extracting into out_dir if needed."""
    source = Path(source)
    out_dir = Path(out_dir)
    cached = out_dir / "cover_auto.jpg"
    if cached.is_file() and cached.stat().st_size > 0:
        return cached
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        suffix = source.suffix.lower()
        if suffix == ".epub":
            data = _epub_cover(source)
            if data:
                cached.write_bytes(data)
                return cached
        elif suffix == ".pdf":
            data = _pdf_first_page(source)
            if data:
                cached.write_bytes(data)
                return cached
    except Exception as e:  # cover is optional, never fatal
        log.warning("cover extraction failed for %s: %s", source.name, e)
    for name in _SIDE_NAMES:
        cand = source.parent / name
        if cand.is_file():
            shutil.copyfile(cand, cached)
            return cached
    return None


def _epub_cover(path: Path) -> Optional[bytes]:
    import ebooklib
    from ebooklib import epub

    book = epub.read_epub(str(path))
    for item in book.get_items_of_type(ebooklib.ITEM_COVER):
        return item.get_content()
    meta = book.get_metadata("OPF", "cover")
    if meta:
        cover_id = (meta[0][1] or {}).get("content")
        if cover_id:
            item = book.get_item_with_id(cover_id)
            if item is not None:
                return item.get_content()
    for item in book.get_items_of_type(ebooklib.ITEM_IMAGE):
        if "cover" in (item.get_name() or "").lower():
            return item.get_content()
    return None


def _pdf_first_page(path: Path) -> Optional[bytes]:
    import fitz  # pymupdf

    with fitz.open(path) as doc:
        if doc.page_count == 0:
            return None
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(1.6, 1.6))
        return pix.tobytes("jpeg")
