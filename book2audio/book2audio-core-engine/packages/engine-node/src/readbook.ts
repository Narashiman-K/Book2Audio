import { extractEpub, extractMarkdown, extractTxt, type Book, type ExtractOptions } from "@b2a/core";
import { extractPdf } from "./pdf.js";

export const SUPPORTED = [".pdf", ".epub", ".md", ".markdown", ".txt"];
/** Read any supported book from its bytes. */
export async function readBook(fileName: string, bytes: Uint8Array, o?: ExtractOptions): Promise<Book> {
  const ext = (/\.[^.\\/]+$/.exec(fileName)?.[0] ?? "").toLowerCase();
  const text = () => new TextDecoder("utf-8").decode(bytes);
  switch (ext) {
    case ".pdf": return extractPdf(bytes, fileName, o);
    case ".epub": return extractEpub(bytes, fileName, o);
    case ".md": case ".markdown": return extractMarkdown(text(), fileName, o);
    case ".txt": return extractTxt(text(), fileName, o);
    default: throw new Error(`Unsupported format '${ext}'. Supported: pdf, epub, md, txt`);
  }
}
