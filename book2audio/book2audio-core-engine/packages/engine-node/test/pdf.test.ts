import { describe, it, expect } from "vitest";
import { PDFDocument, StandardFonts } from "pdf-lib";
import { readBook } from "../src/index.js";

describe("PDF reading with pdf.js", () => {
  it("reads page text and finds chapters", async () => {
    const doc = await PDFDocument.create(), font = await doc.embedFont(StandardFonts.Helvetica);
    const pages = [["Chapter 1: Start", "It was a bright cold day in April and the clocks were striking thirteen. Winston slipped", "quickly through the glass doors of Victory Mansions to escape the vile wind."],
                   ["More words on the second page of chapter one, enough to count as text for sure."],
                   ["Chapter 2: End", "And then the second chapter begins here with plenty of words to read aloud today."]];
    for (const lines of pages) { const p = doc.addPage([400, 400]); lines.forEach((l, i) => p.drawText(l, { x: 20, y: 360 - i * 20, size: 9, font })); }
    const b = await readBook("tiny.pdf", await doc.save());
    expect(b.chapters.map((c) => c.title)).toEqual(["Chapter 1: Start", "Chapter 2: End"]);
    expect(b.chapters[0]!.paragraphs[0]!.text).toContain("striking thirteen");
  });
});
