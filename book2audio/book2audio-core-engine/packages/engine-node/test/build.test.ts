import { describe, it, expect } from "vitest";
import { mkdtempSync, existsSync, readFileSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { SilentEngine, type Book } from "@b2a/core";
import { buildBook, BuildCancelled } from "../src/index.js";

const hasFfmpeg = spawnSync("ffmpeg", ["-version"]).status === 0;
const para = (id: string, text: string) => ({ id, text, sentences: [text] });
const book: Book = { title: "Test: Book/One", author: "A. Writer", sourcePath: "x.md", sourceFormat: "md", chapters: [
  { index: 1, title: "Opening", paragraphs: [para("c1p1", "Hello there, this is the first paragraph of the book."), para("c1p2", "And a second one follows it quickly!")] },
  { index: 2, title: "Middle <b>Part</b>", paragraphs: [para("c2p1", "Chapter two starts here with some more words to say aloud.")] },
] };
const d = hasFfmpeg ? describe : describe.skip;
d("buildBook (silent voice, real ffmpeg)", () => {
  it("builds MP3s, a chaptered M4B and a sidecar, then reuses finished chapters", async () => {
    const root = mkdtempSync(path.join(tmpdir(), "b2a-build-")), events: string[] = [];
    const r = await buildBook({ book, outRoot: root, engine: new SilentEngine(), onProgress: (e) => events.push(e.phase) });
    expect(r.chapters.length).toBe(2); expect(existsSync(r.m4b!)).toBe(true); expect(events).toContain("done");
    const files = readdirSync(r.outDir); expect(files.some((f) => f.startsWith("01 - Opening") && f.endsWith(".mp3"))).toBe(true);
    expect(files.some((f) => f.includes(".partial"))).toBe(false);
    const side = JSON.parse(readFileSync(r.sidecar!, "utf8"));
    expect(side.chapters.length).toBe(2); expect(side.chapters[1].title).toBe("Middle Part");
    expect(side.chapters[0].end).toBeCloseTo(side.chapters[1].start, 2);
    expect(Object.keys(side.timeline)).toEqual(["c1p1", "c1p2", "c2p1"]);
    expect(side.timeline.c2p1[0]).toBeGreaterThanOrEqual(side.chapters[1].start);
    const probe = spawnSync("ffprobe", ["-v", "error", "-show_chapters", "-of", "json", r.m4b!], { encoding: "utf8" });
    expect(JSON.parse(probe.stdout).chapters.length).toBe(2);
    const r2 = await buildBook({ book, outRoot: root, engine: new SilentEngine(), m4b: false });
    expect(r2.reused).toEqual([1, 2]);
  }, 120000);
  it("stops cleanly and builds only selected chapters", async () => {
    const root = mkdtempSync(path.join(tmpdir(), "b2a-build-"));
    const ac = new AbortController(); ac.abort();
    const s = await buildBook({ book, outRoot: root, engine: new SilentEngine(), signal: ac.signal });
    expect(s.stopped).toBe(true); expect(s.m4b).toBeUndefined();
    const only = await buildBook({ book, outRoot: root, engine: new SilentEngine(), chapters: [2], mp3: false });
    expect(path.basename(only.m4b!)).toContain("(ch 2-2)");
  }, 120000);
});
void BuildCancelled;
