import { buildPdfBook, type Book, type ExtractOptions } from "@b2a/core";

/** Read a PDF with pdf.js (pure JavaScript, no native parts): page text, bookmarks and title/author. */
export async function extractPdf(bytes: Uint8Array, fileName: string, o?: ExtractOptions): Promise<Book> {
  const pdfjs: any = await import("pdfjs-dist/legacy/build/pdf.mjs");
  const doc = await pdfjs.getDocument({ data: new Uint8Array(bytes), useSystemFonts: true, isEvalSupported: false, verbosity: 0 }).promise;
  const info: any = (await doc.getMetadata().catch(() => null))?.info ?? {};
  const pagesText: string[] = [];
  for (let i = 1; i <= doc.numPages; i++) {
    const page = await doc.getPage(i), tc = await page.getTextContent();
    let s = "";
    for (const it of tc.items as any[]) { s += it.str; if (it.hasEOL) s += "\n"; }
    pagesText.push(s); page.cleanup();
  }
  const outline: { title: string; page: number }[] = [];
  try {
    for (const item of (await doc.getOutline()) ?? []) {            // top level only, like the Python version
      let dest = item.dest; if (typeof dest === "string") dest = await doc.getDestination(dest);
      if (Array.isArray(dest) && dest[0]) outline.push({ title: String(item.title ?? ""), page: (await doc.getPageIndex(dest[0])) + 1 });
    }
  } catch { /* no usable bookmarks: heading detection takes over */ }
  await doc.destroy();
  return buildPdfBook({ title: info.Title, author: info.Author, fileName, pagesText, outline }, o);
}
