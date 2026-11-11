/** Core data shapes shared by every part of the app (Node engine, browser engine, UI). */
export interface Paragraph { id: string; text: string; sentences: string[] }
export interface Chapter { index: number; title: string; paragraphs: Paragraph[] }
export interface Book { title: string; author: string; sourcePath: string; sourceFormat: string; chapters: Chapter[] }
export interface ProsodySegment {
  paraId: string; mood: string; emphasis: string[];
  rateDelta: number; pitchDelta: number; pauseAfterMs: number; source: "rules" | "llm" | "minimal";
}
export const totalParagraphs = (b: Book) => b.chapters.reduce((a, c) => a + c.paragraphs.length, 0);
export const totalChars = (b: Book) => b.chapters.reduce((a, c) => a + c.paragraphs.reduce((x, p) => x + [...p.text].length, 0), 0);
