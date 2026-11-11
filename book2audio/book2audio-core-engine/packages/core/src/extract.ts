/** Turning raw text into a Book (chapters + paragraphs). Port of the pure parts of b2a/extract.py.
 *  Nothing here touches files: callers pass text/pages, so it runs in Node and in the browser. */
import type { Book, Chapter } from "./models.js";
import { cleanParagraphText, stripMarkdown, type Lexicon } from "./clean.js";

export interface ExtractOptions { minParagraphChars?: number; lexicon?: Lexicon; stripRunningLines?: boolean }
const MIN = (o?: ExtractOptions) => o?.minParagraphChars ?? 25;

export const CHAPTER_HEADING = /^\s*(?:chapter\s+([0-9]+|[ivxlcdm]+)\b[\s.:\-–—]*([^\n]{0,80})?|(?:part|section|book)\s+([0-9]+|[ivxlcdm]+)\b[\s.:\-–—]*([^\n]{0,80})?|([0-9]{1,2})\s*[.)]\s+([^\n]{3,80}))\s*$/i;
const MD_HEADING = /^(#{1,4})\s+(.+?)\s*$/;
const PAGE_LINE = /^(?:page|p\.?)\s*\d+$/i;
const FIG_LINE = /^(?:picture|fig|figure|table)\s*\d+/i;
const MAJOR_SECTION = /^(?:introduction|editor’s introduction|conclusion|epilogue|prologue|preface|afterword)\b/i;
const stripTags = (s: string) => s.replace(/<[^>]+>/g, "");
const stripChars = (s: string, chars: string) => { let a = 0, b = s.length; while (a < b && chars.includes(s[a]!)) a++; while (b > a && chars.includes(s[b - 1]!)) b--; return s.slice(a, b); };

export const paraId = (ch: number, p: number) => `c${String(ch).padStart(2, "0")}-p${String(p).padStart(3, "0")}`;
export const looksLikeHeading = (t: string) => [...t].length < 100 && /^(chapter|part|section|book|prologue|epilogue|introduction|preface|afterword)\b/i.test(t);
export const isSkipChapterTitle = (t: string) => {
  const x = t.trim().toLowerCase();
  return ["contents", "table of contents", "index", "copyright", "colophon", "title page"].includes(x)
    || /^(cover|cover page|book cover|title|copyright page|errata)$/.test(x) || /\bfree (e-?book|access)\b/.test(x);
};
export function cleanChapterTitle(raw: string): string {
  const t = stripChars(stripTags(raw).replace(/\s+/g, " ").trim(), " .:-–—");
  return [...t].slice(0, 80).join("") || "Untitled";
}

/** Turn raw blocks into cleaned paragraphs; tiny fragments are merged forward (same as Python). */
export function makeParagraphs(chapter: Chapter, blocks: string[], o?: ExtractOptions): void {
  const cleaned: string[] = []; let buffer = "";
  for (const block of blocks) {
    const text = cleanParagraphText(block, o?.lexicon);
    if (!text) continue;
    if ([...text].length < MIN(o) && !looksLikeHeading(text)) { buffer = `${buffer} ${text}`.trim(); continue; }
    if (buffer) { cleaned.push(buffer); buffer = ""; }
    cleaned.push(text);
  }
  if (buffer) cleaned.push(buffer);
  cleaned.forEach((text, i) => chapter.paragraphs.push({ id: paraId(chapter.index, i), text, sentences: [] }));
}
export function hasIdentifiableChapters(chapters: Chapter[]): boolean {
  if (chapters.length <= 1) return false;
  const pageLike = chapters.filter((c) => /^(?:page|p\.?)\s*\d+$/i.test(c.title.trim())).length;
  if (pageLike / chapters.length > 0.15) return false;
  const avg = chapters.reduce((a, c) => a + c.paragraphs.length, 0) / chapters.length;
  return !(chapters.length > 20 && avg < 4);
}
const stemOf = (name: string) => name.replace(/^.*[\\/]/, "").replace(/\.[^.]+$/, "");
const titleCase = (s: string) => s.replace(/_/g, " ").replace(/\w\S*/g, (w) => w[0]!.toUpperCase() + w.slice(1).toLowerCase());
const splitParas = (block: string) => block.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);

function addSections(book: Book, sections: [string, string[]][], o?: ExtractOptions) {
  let n = 0;
  for (const [title, lines] of sections) {
    if (isSkipChapterTitle(title)) continue;
    const paras = splitParas(lines.join("\n")); if (!paras.length) continue;
    n++; const ch: Chapter = { index: n, title, paragraphs: [] };
    makeParagraphs(ch, paras, o);
    if (ch.paragraphs.length) book.chapters.push(ch); else n--;
  }
}
// NOTE: Python increments chapter_no before checking the paragraphs, so an empty chapter leaves a gap in numbering.
// Here the numbering stays contiguous, which is what the Studio expects (it was a source of the "chapter [0] not found" confusion).

/* ---------------- Markdown / TXT ---------------- */
function firstHeading(raw: string): string | null {
  for (const line of raw.split(/\r?\n/)) {
    const m = MD_HEADING.exec(line.trim());
    if (m) { const c = stripTags(m[2]!).trim(); if (!PAGE_LINE.test(c) && !FIG_LINE.test(c)) return [...c].slice(0, 80).join(""); }
  }
  return null;
}
function mergeAdjacentHeadingLines(lines: string[]): string[] {
  const out: string[] = []; let i = 0;
  while (i < lines.length) {
    const m = MD_HEADING.exec(lines[i]!.trim());
    if (m) {
      const lvl = m[1]!, txt = stripTags(m[2]!).trim();
      if (!PAGE_LINE.test(txt) && !FIG_LINE.test(txt)) {
        let combined = txt, j = i + 1;
        while (j < lines.length) {
          if (!lines[j]!.trim()) { j++; continue; }
          const nm = MD_HEADING.exec(lines[j]!.trim());
          if (nm) {
            const nt = stripTags(nm[2]!).trim();
            if (!PAGE_LINE.test(nt) && !FIG_LINE.test(nt)) {
              if ([...combined].length < 45 || ["THE","OF","AND","IN","TO","A","AN","SAYING","NON-VERBAL","FOR","WITH"].some((s) => combined.endsWith(s))) { combined = `${combined} ${nt}`; j++; continue; }
            }
          }
          break;
        }
        out.push(`${lvl} ${combined}`); i = j; continue;
      }
    }
    out.push(lines[i]!); i++;
  }
  return out;
}
export function extractMarkdown(raw: string, fileName: string, o?: ExtractOptions): Book {
  raw = raw.replace(/<!--.*?-->/gs, "").replace(/!\[[^\]]*\]\(data:image\/[^)]+\)/g, "");
  const book: Book = { title: firstHeading(raw) ?? titleCase(stemOf(fileName)), author: "Unknown author", sourcePath: fileName, sourceFormat: "md", chapters: [] };
  const lines = mergeAdjacentHeadingLines(raw.split(/\r?\n/));
  const skip = new Set(["contents", "table of contents", "index", "title page", "copyright", "and", "colophon"]);
  let explicit = false;
  for (const line of lines) {
    const m = MD_HEADING.exec(line.trim());
    if (m) { const t = stripTags(m[2]!).trim(); if (!PAGE_LINE.test(t) && CHAPTER_HEADING.test(t)) { explicit = true; break; } }
  }
  const sections: [string, string[]][] = []; let title = book.title, cur: string[] = [];
  for (const line of lines) {
    const m = MD_HEADING.exec(line.trim());
    if (m) {
      const level = m[1]!.length, txt = stripChars(stripTags(m[2]!).trim(), " .:–—#").trim();
      if (PAGE_LINE.test(txt) || FIG_LINE.test(txt)) continue;
      let brk = false;
      if (explicit) brk = CHAPTER_HEADING.test(txt) || MAJOR_SECTION.test(txt);
      else if (level <= 3 && !skip.has(txt.toLowerCase())) brk = [...txt].length > 3 && !/^(?:by|shân rees|roderick)\b/i.test(txt);
      if (brk) { if (cur.length) { sections.push([title, cur]); cur = []; } title = txt; continue; }
    }
    cur.push(line);
  }
  if (cur.length) sections.push([title, cur]);
  addSections(book, sections, o);
  return book;
}
export function extractTxt(raw: string, fileName: string, o?: ExtractOptions): Book {
  const book: Book = { title: titleCase(stemOf(fileName)), author: "Unknown author", sourcePath: fileName, sourceFormat: "txt", chapters: [] };
  const sections: [string, string[]][] = []; let title = book.title, cur: string[] = [];
  for (const line of raw.split(/\r?\n/)) {
    const s = line.trim();
    if (CHAPTER_HEADING.test(s) && [...s].length < 90) { if (cur.length) { sections.push([title, cur]); cur = []; } title = cleanChapterTitle(s); continue; }
    cur.push(line);
  }
  if (cur.length) sections.push([title, cur]);
  addSections(book, sections, o);
  return book;
}

/* ---------------- PDF (page text in, Book out) ---------------- */
export interface PdfInput { title?: string; author?: string; fileName: string; pagesText: string[]; outline?: { title: string; page: number }[] }
export function pdfPageBlocks(pageText: string, repeated: Set<string>): string[] {
  const lines = pageText.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)
    .filter((ln) => !repeated.has(ln) && !/^(page\s*)?[0-9ivxlcdm]{1,6}$/i.test(ln));
  const blocks: string[] = []; let buf = "";
  for (const ln of lines) {
    if (!buf) buf = ln; else if (ln.endsWith("-")) buf = buf + ln.slice(0, -1); else buf += " " + ln;
    if ([...buf].length > 800 && /[.!?"”]$/.test(ln)) { blocks.push(buf); buf = ""; }
  }
  if (buf) blocks.push(buf);
  return blocks;
}
export function runningLines(pages: string[]): Set<string> {
  const counts = new Map<string, number>(), total = Math.max(pages.length, 1);
  for (const text of pages) {
    const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean), edges = new Set<string>();
    if (lines.length) edges.add(lines[0]!); if (lines.length > 1) edges.add(lines[lines.length - 1]!);
    for (const ln of edges) if ([...ln].length > 2) { const k = ln.replace(/\d+/g, "#"); counts.set(k, (counts.get(k) ?? 0) + 1); }
  }
  return new Set([...counts].filter(([, n]) => n / total > 0.4).map(([k]) => k));
}
export function buildPdfBook(inp: PdfInput, o?: ExtractOptions): Book {
  const n = inp.pagesText.length;
  const book: Book = { title: (inp.title ?? "").trim() || titleCase(stemOf(inp.fileName)), author: (inp.author ?? "").trim() || "Unknown author", sourcePath: inp.fileName, sourceFormat: "pdf", chapters: [] };
  const spans: [string, number, number][] = [];
  const top = inp.outline ?? [];
  if (top.length >= 2) {
    top.forEach((t, i) => { const start = Math.max(t.page - 1, 0), end = i + 1 < top.length ? top[i + 1]!.page - 2 : n - 1; spans.push([t.title.trim() || `Chapter ${i + 1}`, start, Math.max(end, start)]); });
  } else {
    let cur: string | null = null, curStart = 0;
    inp.pagesText.forEach((text, p) => {
      let hit: string | null = null;
      for (const raw of text.split(/\r?\n/).slice(0, 6)) { const l = raw.trim(); if (!PAGE_LINE.test(l) && !FIG_LINE.test(l) && CHAPTER_HEADING.test(l)) { hit = l; break; } }
      if (hit) { if (cur !== null) spans.push([cur, curStart, p - 1]); cur = cleanChapterTitle(hit); curStart = p; }
    });
    if (cur !== null) spans.push([cur, curStart, n - 1]);
    if (!spans.length) spans.push([book.title, 0, n - 1]);
  }
  const repeated = o?.stripRunningLines === false ? new Set<string>() : runningLines(inp.pagesText);
  let idx = 1;
  for (const [title, p0, p1] of spans) {
    if (isSkipChapterTitle(title)) continue;
    const ch: Chapter = { index: idx, title, paragraphs: [] }, blocks: string[] = [];
    for (let p = p0; p <= p1; p++) blocks.push(...pdfPageBlocks(inp.pagesText[p] ?? "", repeated));
    makeParagraphs(ch, blocks, o);
    if (ch.paragraphs.length) { book.chapters.push(ch); idx++; }
  }
  if (!book.chapters.length) throw new Error(`No extractable text found in '${inp.fileName}'. It appears to be a scanned PDF (images only) without a digital text layer. Please run an OCR tool (for example ocrmypdf) on it first.`);
  return book;
}
export { stripMarkdown };
