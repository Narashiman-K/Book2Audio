/** EPUB reader: bytes in, Book out. Works in Node and in the browser (jszip + a pure-JS HTML parser). */
import JSZip from "jszip";
import { parse } from "node-html-parser";
import type { Book, Chapter } from "./models.js";
import { makeParagraphs, isSkipChapterTitle, type ExtractOptions } from "./extract.js";

const SKIP_NAME = /(^|[_\-./])(cover|toc|nav|ncx|titlepage|colophon|copyright|dedication)([_\-./]|$)/i;
const BLOCKS = "p,h1,h2,h3,h4,h5,h6,li,blockquote,pre,dd,dt,figcaption,td";
const unesc = (s: string) => s.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&apos;/g, "'");
const attr = (tag: string, name: string) => new RegExp(`${name}\\s*=\\s*["']([^"']*)["']`, "i").exec(tag)?.[1];
const dirOf = (p: string) => (p.includes("/") ? p.slice(0, p.lastIndexOf("/") + 1) : "");
function resolve(base: string, href: string): string {
  const parts = (base + decodeURIComponent(href.split("#")[0]!)).split("/"), out: string[] = [];
  for (const p of parts) { if (p === "..") out.pop(); else if (p && p !== ".") out.push(p); }
  return out.join("/");
}

export async function extractEpub(bytes: Uint8Array | ArrayBuffer, fileName: string, o?: ExtractOptions): Promise<Book> {
  const zip = await JSZip.loadAsync(bytes);
  const read = async (p: string) => { const f = zip.file(p) ?? zip.file(decodeURIComponent(p)); return f ? await f.async("string") : ""; };
  const container = await read("META-INF/container.xml");
  const opfPath = attr(/<rootfile\b[^>]*>/i.exec(container)?.[0] ?? "", "full-path");
  if (!opfPath) throw new Error(`Not a valid EPUB: ${fileName}`);
  const opf = await read(opfPath), base = dirOf(opfPath);
  const tag = (name: string) => unesc((new RegExp(`<dc:${name}[^>]*>([\\s\\S]*?)</dc:${name}>`, "i").exec(opf)?.[1] ?? "").replace(/<[^>]+>/g, "").trim());
  const stem = fileName.replace(/^.*[\\/]/, "").replace(/\.[^.]+$/, "").replace(/_/g, " ");
  const book: Book = { title: tag("title") || stem, author: tag("creator") || "Unknown author", sourcePath: fileName, sourceFormat: "epub", chapters: [] };

  const manifest = new Map<string, { href: string; type: string; props: string }>();
  for (const m of opf.matchAll(/<item\b[^>]*>/gi)) { const id = attr(m[0], "id"), href = attr(m[0], "href"); if (id && href) manifest.set(id, { href, type: attr(m[0], "media-type") ?? "", props: attr(m[0], "properties") ?? "" }); }
  let order = [...opf.matchAll(/<itemref\b[^>]*>/gi)].map((m) => attr(m[0], "idref")).filter((x): x is string => !!x);
  if (!order.length) order = [...manifest.keys()];
  // Chapter titles from the book's own table of contents (EPUB3 nav or EPUB2 ncx), keyed by file.
  const toc = new Map<string, string>();
  const addToc = (href: string, title: string, from: string) => {
    const t = unesc(title.replace(/<[^>]+>/g, "")).replace(/\s+/g, " ").trim(), key = resolve(dirOf(from), href);
    if (t && !/^\d+$/.test(t) && !toc.has(key)) toc.set(key, t);   // a bare number is a page label, not a title
  };
  const navItem = [...manifest.values()].find((m) => /\bnav\b/.test(m.props)), ncxItem = [...manifest.values()].find((m) => /dtbncx/.test(m.type));
  if (navItem) { const p = resolve(base, navItem.href); { const doc = await read(p), tocNav = /<nav\b[^>]*epub:type=["']toc["'][^>]*>([\s\S]*?)<\/nav>/i.exec(doc)?.[1] ?? doc;   // not landmarks / page-list
    for (const m of tocNav.matchAll(/<a\b[^>]*href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi)) addToc(m[1]!, m[2]!, p); } }
  if (!toc.size && ncxItem) { const p = resolve(base, ncxItem.href); for (const m of (await read(p)).matchAll(/<navPoint\b[\s\S]*?<text>([\s\S]*?)<\/text>[\s\S]*?<content\b[^>]*src=["']([^"']+)["']/gi)) addToc(m[2]!, m[1]!, p); }
  let no = 0, seenToc = false;
  for (const id of order) {
    const it = manifest.get(id); if (!it || !/x?html/i.test(it.type)) continue;
    const path = resolve(base, it.href);
    if (SKIP_NAME.test(path.toLowerCase())) continue;
    // With a real table of contents, pages before its first entry are front matter (title page, legal text): not narrated.
    if (toc.size >= 3) { if (toc.has(path)) seenToc = true; else if (!seenToc) continue; }
    const html = await read(path); if (!html) continue;
    const root = parse(html, { blockTextElements: { script: false, style: false, noscript: false } });
    root.querySelectorAll("script,style,head,nav").forEach((n) => n.remove());
    const blocks: string[] = [];
    for (const el of root.querySelectorAll(BLOCKS)) {
      if (el.querySelector(BLOCKS)) continue;                       // only leaf blocks
      const t = unesc(el.text).replace(/\s+/g, " ").trim();
      if (t && !/^[\s\-–—_=*#|>]+$/.test(t)) blocks.push(t);
    }
    if (!blocks.length) continue;
    const heads = root.querySelectorAll("h1,h2,h3").map((h) => unesc(h.text).replace(/\s+/g, " ").trim()).filter(Boolean);
    let raw = heads[0] ?? unesc(root.querySelector("title")?.text ?? "").replace(/\s+/g, " ").trim();
    if (heads[0] && /^(chapter|part|section|book)\s+(\d+|[ivxlcdm]+)$/i.test(heads[0]) && heads[1]) raw = `${heads[0]}: ${heads[1]}`;  // "Chapter 3" + its title heading
    const title = [...(toc.get(path) || raw || `Chapter ${no + 1}`)].slice(0, 80).join("");
    if (isSkipChapterTitle(title)) continue;
    const ch: Chapter = { index: no + 1, title, paragraphs: [] };
    makeParagraphs(ch, blocks, o);
    if (ch.paragraphs.length) { no++; book.chapters.push(ch); }
  }
  if (!book.chapters.length) throw new Error(`No readable chapters in ${fileName}`);
  return book;
}
