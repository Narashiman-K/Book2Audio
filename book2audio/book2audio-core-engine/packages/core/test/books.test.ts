import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import JSZip from "jszip";
import { extractMarkdown, extractTxt, extractEpub, buildPdfBook } from "../src/index.js";

const ref = JSON.parse(readFileSync(new URL("./fixtures/reference.json", import.meta.url), "utf8"));
const books = JSON.parse(readFileSync(new URL("./fixtures/books.json", import.meta.url), "utf8"));
const shape = (b: any) => ({ title: b.title, author: b.author, chapters: b.chapters.map((c: any) => ({ title: c.title, paras: c.paragraphs ? c.paragraphs.map((p: any) => p.text) : c.paras })) });

describe("MD / TXT readers match the Python version", () => {
  for (const [name, b] of Object.entries<any>(books)) {
    it(name, () => {
      const got = (b.kind === "md" ? extractMarkdown : extractTxt)(b.raw, b.file, { lexicon: ref.lexicon });
      expect(shape(got)).toEqual(shape(b.expected));
      got.chapters.forEach((c, i) => expect(c.index).toBe(i + 1));
    });
  }
});

describe("EPUB reader", () => {
  it("reads chapters in spine order, skips nav/cover, takes titles from headings", async () => {
    const z = new JSZip();
    z.file("mimetype", "application/epub+zip");
    z.file("META-INF/container.xml", `<container><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>`);
    z.file("OEBPS/content.opf", `<package><metadata><dc:title>Test &amp; Book</dc:title><dc:creator>Ann Author</dc:creator></metadata>
      <manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"/><item id="c2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/><item id="c1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/><item id="css" href="s.css" media-type="text/css"/></manifest>
      <spine><itemref idref="nav"/><itemref idref="c1"/><itemref idref="c2"/></spine></package>`);
    z.file("OEBPS/nav.xhtml", `<html><body><h1>Contents</h1><p>Chapter one, Chapter two and more words here.</p></body></html>`);
    z.file("OEBPS/text/ch1.xhtml", `<html><head><title>x</title></head><body><h1>The First Chapter</h1><p>It was a bright cold day in April, and the clocks were striking <b>thirteen</b>.</p><p>Second paragraph of the first chapter, long enough to stay.</p></body></html>`);
    z.file("OEBPS/text/ch2.xhtml", `<html><body><h2>Second</h2><p>Another chapter with enough words to count as a paragraph here.</p></body></html>`);
    const b = await extractEpub(await z.generateAsync({ type: "uint8array" }), "test.epub");
    expect(b.title).toBe("Test & Book"); expect(b.author).toBe("Ann Author");
    expect(b.chapters.map((c) => c.title)).toEqual(["The First Chapter", "Second"]);
    expect(b.chapters[0]!.paragraphs[1]!.text).toContain("striking thirteen.");
    expect(b.chapters[0]!.paragraphs[1]!.id).toBe("c01-p001");
  });
});

describe("PDF chapter logic", () => {
  const filler = (n: string) => `This is a long enough sentence number ${n} for the paragraph builder to keep it. `.repeat(3);
  it("uses bookmarks when there are two or more", () => {
    const b = buildPdfBook({ fileName: "a.pdf", pagesText: [filler("1"), filler("2"), filler("3")], outline: [{ title: "One", page: 1 }, { title: "Two", page: 3 }] });
    expect(b.chapters.map((c) => c.title)).toEqual(["One", "Two"]);
  });
  it("falls back to 'Chapter N' headings near the top of a page", () => {
    const b = buildPdfBook({ fileName: "a.pdf", pagesText: [`Chapter 1: Start\n${filler("1")}`, filler("2"), `Chapter 2: End\n${filler("3")}`] });
    expect(b.chapters.map((c) => c.title)).toEqual(["Chapter 1: Start", "Chapter 2: End"]);
  });
  it("explains scanned PDFs", () => { expect(() => buildPdfBook({ fileName: "scan.pdf", pagesText: ["", ""] })).toThrow(/scanned/i); });
});
